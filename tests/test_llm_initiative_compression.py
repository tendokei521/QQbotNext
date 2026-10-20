"""主动消息 / 定时任务的历史压缩必须是活的（此前是彻底的死代码）。

审查发现的"截断在前、压缩在后"：

    rounds = int(config.get("history_rounds", 50) or 50)
    history = session_mgr.get_history(session_id, limit=rounds)   # ← 先削到 rounds 条以内
    ...
    history = await maybe_compress_context(chain, config, history, rounds)  # ← 判据是 len > rounds

``should_compress`` 要求 ``len(history) > history_rounds``，而传进来的列表已经被
``limit=rounds`` 削过 —— 条件永远不成立。后果：主动消息 / 定时任务这条路径**从不压缩**，
长会话只是把最旧的整段静默丢掉（丢了什么、丢了多久都无从判断），
而 chat 主路径是取满 ``MAX_HISTORY_MESSAGES`` 再压缩的。

现在对齐 chat 主路径：先按上限取满，再交给压缩判断。
"""

from __future__ import annotations

import types

import pytest

from app.agent.assembly import inputs as assembly_inputs, compress


def _config(**over):
    base = {
        "history_rounds": 4,
        "context_compress_enable": True,
        "max_tokens": 1024,
        "temperature": 0.7,
    }
    base.update(over)
    return base


class _SessionMgr:
    """记录 get_history 收到的 limit，并按 limit 返回对应条数。"""

    MAX_HISTORY_MESSAGES = 200

    def __init__(self, total: int):
        self.total = total
        self.limits: list[int] = []

    def get_history(self, session_id, limit=10):
        self.limits.append(limit)
        return [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"第{i}条"}
            for i in range(min(limit, self.total))
        ]


def _runtime():
    rt = types.SimpleNamespace()
    rt.bot_id = 8101
    rt.memory = None
    rt.provider_chain = lambda: [{"provider": "x", "model": "x"}]
    return rt


async def test_history_is_fetched_beyond_rounds_so_compression_can_trigger(monkeypatch):
    """取数上限必须大于 history_rounds，否则压缩判据永远不成立。"""
    seen: dict = {}

    async def _fake_compress(chain, config, history, rounds):
        seen["len"] = len(history)
        seen["rounds"] = rounds
        return history

    monkeypatch.setattr(compress, "maybe_compress_context", _fake_compress)
    mgr = _SessionMgr(total=120)

    await assembly_inputs.collect_history_materials(
        runtime=_runtime(),
        session_mgr=mgr,
        session_id="group_x",
        is_private=False,
        config=_config(),
        query_text="看看大家在聊什么",
    )

    assert mgr.limits[0] == 200, "应按 MAX_HISTORY_MESSAGES 取满（此前是 history_rounds）"
    assert seen["len"] == 120, "压缩必须看到全部历史（此前只能看到 rounds 条）"
    assert seen["rounds"] == 4


async def test_compression_actually_runs_on_long_history(monkeypatch):
    """端到端：长历史真的会被压缩成「更早对话摘要」+ 最近原文。"""
    calls: list[int] = []

    class _Resp:
        text = "我们之前聊过天气和工作。"

    async def _fake_chat(chain, messages, **kwargs):
        calls.append(len(messages))
        return _Resp()

    monkeypatch.setattr(compress, "chat_with_fallback", _fake_chat)
    mgr = _SessionMgr(total=50)

    out = await assembly_inputs.collect_history_materials(
        runtime=_runtime(),
        session_mgr=mgr,
        session_id="group_x",
        is_private=False,
        config=_config(history_rounds=10),
        query_text="继续聊",
    )

    assert calls, "压缩请求根本没发出（= 此前压缩分支不可达）"
    history = out["history"]
    assert history[0]["role"] == "system"
    assert "更早对话摘要" in history[0]["content"]
    assert len(history) == 11, f"应为 1 条摘要 + 最近 10 条原文，实际 {len(history)}"


async def test_compression_can_be_disabled(monkeypatch):
    """关掉开关后不压缩，也不该多打一次请求。"""
    calls: list[int] = []

    async def _fake_chat(chain, messages, **kwargs):
        calls.append(len(messages))
        raise AssertionError("关掉压缩时不该发摘要请求")

    monkeypatch.setattr(compress, "chat_with_fallback", _fake_chat)
    mgr = _SessionMgr(total=50)

    await assembly_inputs.collect_history_materials(
        runtime=_runtime(),
        session_mgr=mgr,
        session_id="group_x",
        is_private=False,
        config=_config(history_rounds=10, context_compress_enable=False),
        query_text="继续聊",
    )

    assert calls == []


@pytest.mark.parametrize("total,expect_compress", [(10, False), (11, True)])
async def test_compress_boundary(monkeypatch, total, expect_compress):
    """正好等于 history_rounds 时不压缩，超出才压缩（判据是 >）。"""
    calls: list[int] = []

    class _Resp:
        text = "摘要"

    async def _fake_chat(chain, messages, **kwargs):
        calls.append(len(messages))
        return _Resp()

    monkeypatch.setattr(compress, "chat_with_fallback", _fake_chat)
    mgr = _SessionMgr(total=total)

    await assembly_inputs.collect_history_materials(
        runtime=_runtime(),
        session_mgr=mgr,
        session_id="group_x",
        is_private=False,
        config=_config(history_rounds=10),
        query_text="继续聊",
    )

    assert bool(calls) is expect_compress
