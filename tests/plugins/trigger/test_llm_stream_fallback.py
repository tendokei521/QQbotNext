"""流式回退链的错误语义：error 事件不能把整条链一起埋掉。

线上形态：配了多 provider 回退，主模型 429/401/余额不足 → 用户收到
「抱歉，我暂时无法回答」，而备用模型**一次都不会被请求**。

根因是契约错位不是取舍：
``iter_stream_with_fallback`` 的 docstring 明写「仅在首个事件前出错才切换到下一个」，
但实现用 error **事件**（不是异常）传递失败，且事件先 ``yield`` 出去；消费端
``chat.stream_response`` / ``initiative_stream`` 收到 error 就 ``break``，生成器随之中止，
「换下一个 provider」的逻辑再也走不到。

现在 provider 适配器仍然可以 yield error 事件，由回退器按上下文决定语义：

- **未产出任何正文就报错** → 这个 provider 起不来：不透出 error，直接换下一个；
- **已产出正文后中途断流** → 如实上报（正文已经发给用户了，换 provider 也接不上）；
- **整条链都起不来** → 最后交出一个 error 事件，保留最后一个真实错误，别让真因丢失。
"""

from __future__ import annotations

import types

import pytest

import app.agent
from app.agent.providers import PROVIDERS, iter_stream_with_fallback
from app.agent.providers.base import BaseProvider, StreamEvent

CALLS: dict[str, int] = {}


class _Quiet:
    def add_info(self, *_a, **_k):
        return self

    def info(self, *_a, **_k):
        pass

    warning = error = debug = info


class _ErrorProvider(BaseProvider):
    async def chat_stream(self, messages, **kwargs):
        name = self.config.get("provider")
        CALLS[name] = CALLS.get(name, 0) + 1
        yield StreamEvent(type="error", text="HTTP 429: rate limited")


class _OkProvider(BaseProvider):
    async def chat_stream(self, messages, **kwargs):
        name = self.config.get("provider")
        CALLS[name] = CALLS.get(name, 0) + 1
        yield StreamEvent(type="text", text="备用模型的回答")
        yield StreamEvent(type="done", finish_reason="stop")


class _TextThenErrorProvider(BaseProvider):
    async def chat_stream(self, messages, **kwargs):
        yield StreamEvent(type="text", text="半句")
        yield StreamEvent(type="error", text="midstream boom")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(app.agent, "logger", _Quiet(), raising=False)
    monkeypatch.setitem(PROVIDERS, "err_probe", _ErrorProvider)
    monkeypatch.setitem(PROVIDERS, "ok_probe", _OkProvider)
    monkeypatch.setitem(PROVIDERS, "text_err_probe", _TextThenErrorProvider)
    CALLS.clear()
    yield
    CALLS.clear()


def _chain(*names):
    return [{"provider": n, "model": n, "max_tokens": 1024} for n in names]


async def _consume(chain, *, break_on_error=True):
    """照抄 chat.stream_response 的消费写法：error 就 break 当失败。"""
    out = []
    async for ev in iter_stream_with_fallback(
        chain, [{"role": "user", "content": "hi"}], max_empty_retries=1, empty_retry_backoff=0
    ):
        if ev.type == "error" and break_on_error:
            out.append(("BREAK", ev.text))
            break
        out.append((ev.type, ev.text))
    return out


async def test_error_before_content_advances_to_next_provider():
    """主 provider 报错、备用正常 → 用户必须拿到备用的回答。"""
    events = await _consume(_chain("err_probe", "ok_probe"))

    assert events == [("text", "备用模型的回答"), ("done", "")]
    assert CALLS == {"err_probe": 1, "ok_probe": 1}


async def test_error_does_not_retry_same_provider():
    """报错是同 provider 的确定性失败，不在它身上重复空跑（它内部已按重试策略试过）。"""
    await _consume(_chain("err_probe", "ok_probe"))

    assert CALLS["err_probe"] == 1, "error 不该触发「空回复重试」"


async def test_all_providers_error_surfaces_last_real_error():
    """整条链都起不来时，把最后一个真实错误交出去 —— 否则日志只剩「空回复」。"""
    events = await _consume(_chain("err_probe", "err_probe"))

    assert events == [("BREAK", "HTTP 429: rate limited")]
    assert CALLS == {"err_probe": 2}, "两个 provider 位置都要试过"


async def test_error_after_content_is_passed_through():
    """已产出正文后中途断流：如实上报，不再换 provider（正文已经发给用户了）。"""
    events = await _consume(_chain("text_err_probe", "ok_probe"))

    assert events == [("text", "半句"), ("BREAK", "midstream boom")]
    assert "ok_probe" not in CALLS


async def test_full_consumption_reaches_backup_too():
    """不 break 的消费端同样能拿到备用回答（回退语义与消费方式无关）。"""
    events = await _consume(_chain("err_probe", "ok_probe"), break_on_error=False)

    assert ("text", "备用模型的回答") in events
    assert "ok_probe" in CALLS


# ==================== 用户可见结果（走 chat.stream_response） ====================


class _Skills:
    def prompt_blocks(self):
        return []


class _Tools:
    def enabled_specs(self):
        return []


class _Telemetry:
    def record_call_simple(self, **kwargs):
        return None

    def record_tool(self, *args, **kwargs):
        return None


def _runtime(bot_id: int, chain: list[dict]):
    from app.agent.session.manager import SessionManager

    rt = types.SimpleNamespace()
    rt.bot_id = bot_id
    rt.config = {
        "system_prompt": "你是助手",
        "group_enable": True,
        "private_enable": True,
        "meta_sender_style": "off",
        "history_rounds": 50,
        "schedule_enable": True,
        "stream_output": True,
        "max_tool_rounds": 5,
        "max_tokens": 1024,
    }
    rt.memory = None
    rt.session_mgr = SessionManager(str(bot_id))
    rt.skills = _Skills()
    rt.llm_tools = _Tools()
    rt.telemetry = _Telemetry()
    rt.provider_config = lambda: {"api_key": "sk-test", "model": "x"}
    rt.provider_chain = lambda: chain
    return rt


def _event_and_ctx():
    from app.agent.loop.context import LlmContext
    from app.domain.events import GroupMessageEvent, MessageSegment, UserInfo

    event = GroupMessageEvent(
        event_type="message_group",
        message_type="group",
        time=1788342260,
        user_id=20002,
        self_id=778,
        message=[MessageSegment("text", {"text": "在吗"})],
        user=UserInfo(user_id=20002, nickname="小明"),
        group=types.SimpleNamespace(group_id=466052056, group_name="测试群"),
    )
    ctx = LlmContext(event=event, runtime=None, bot=None, session_id="group_466052056")
    ctx.user_text = "在吗"
    ctx.state["user_context"] = {"sent_text": "在吗", "sender": "小明(20002)"}
    ctx.state["is_at"] = True
    return event, ctx


async def test_chat_stream_uses_backup_provider_when_primary_errors(monkeypatch, tmp_path):
    """端到端：主模型 429，用户应当收到备用模型的回答，而不是「抱歉，我暂时无法回答」。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    from app.agent.loop import chat as chat_mod

    rt = _runtime(1301, _chain("err_probe", "ok_probe"))
    event, ctx = _event_and_ctx()

    sent = [str(item) async for item in chat_mod.stream_response(rt, event, ctx)]

    assert sent == ["备用模型的回答"]
    assert CALLS["ok_probe"] == 1
