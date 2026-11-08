"""交付侧「消息没发出去却零日志」的失败留痕契约。

覆盖两类旧行为：
1. ``pre_send`` 钩子返回 False → ``send_item`` 静默 ``return None``（一行日志都没有，
   "是模块设计性拦截还是 bug"无从区分）；
2. 钩子自己抛异常被吞成 debug 后按放行处理（消息发了但没人知道钩子坏了）。

本用例只锁"失败时留下了定位信息"（事件码 + 会话 + 账号 + 字数），不锁日志文案。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import diagnostics


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, log, code, message="", **kwargs):  # noqa: ANN001
        self.calls.append({"code": code, "message": message, **kwargs})
        return True

    def first(self, code: str) -> dict:
        for call in self.calls:
            if call["code"] == code:
                return call
        raise AssertionError(f"没有记录到 {code}：{[c['code'] for c in self.calls]}")


@pytest.fixture()
def recorder(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(diagnostics, "log_failure", recorder)
    yield recorder
    diagnostics.reset_failure_state()


@pytest.mark.asyncio
async def test_pre_send_veto_leaves_trace(monkeypatch, recorder):
    from app.agent import hooks
    from app.agent.delivery.channel import send_item

    async def _veto(runtime, stage, ctx, msg=None):  # noqa: ANN001
        return stage != "pre_send"

    monkeypatch.setattr(hooks, "run_stage", _veto)
    runtime = SimpleNamespace(bot_id="contract_bot_send")

    response = await send_item(
        None, "group_123", "你好", runtime=runtime, ctx=None, hooks=True
    )

    assert response is None
    fields = recorder.first("delivery.pre_send_vetoed")["fields"]
    assert fields["session"] == "group_123"
    assert fields["bot"] == "contract_bot_send"
    assert fields["chars"] == len("你好")


@pytest.mark.asyncio
async def test_hook_exception_is_logged_and_treated_as_pass(monkeypatch, recorder):
    from app.agent import hooks
    from app.agent.delivery.channel import send_item

    async def _boom(runtime, stage, ctx, msg=None):  # noqa: ANN001
        raise RuntimeError("hook exploded")

    monkeypatch.setattr(hooks, "run_stage", _boom)
    runtime = SimpleNamespace(bot_id="contract_bot_hook")
    bot = SimpleNamespace(bot_id="contract_bot_hook", _sent=[])

    async def _send_group_msg(group_id, message):  # noqa: ANN001
        bot._sent.append((group_id, message))
        return {"status": "ok"}

    bot.send_group_msg = _send_group_msg
    bot.send_private_msg = _send_group_msg

    response = await send_item(
        bot, "group_123", "你好", runtime=runtime, ctx=None, hooks=True
    )

    # 钩子异常不阻断发送（旧语义保留），但必须留痕
    assert response is not None
    fields = recorder.first("delivery.hook_failed")["fields"]
    assert fields["session"] == ""
    assert fields["bot"] == "contract_bot_hook"
