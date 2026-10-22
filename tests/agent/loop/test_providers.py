"""上下文块 / 工具源注册表：可组合接缝（多方贡献，不排他）。

与 `output`/`assembly` 这类"谁接管谁独占"的槽不同，记忆、群记录、工具扩展
应该**同时**贡献内容。本文件锁住注册表的四条性质：
按 order 收集、空块丢弃、单点异常不影响整轮、按归属批量移除。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.loop.providers import (
    SimpleContextProvider,
    SimpleToolSource,
    TurnScope,
    collect_context_blocks,
    collect_tool_specs,
    register_context_provider,
    register_tool_source,
    unregister_owner,
)


class _Provider:
    def __init__(self, name, text, *, order=100, owner=None, boom=False):
        self.name = name
        self.order = order
        self.owner = owner
        self._text = text
        self._boom = boom

    async def collect(self, scope):
        if self._boom:
            raise RuntimeError("供应商炸了")
        return self._text


class _Source:
    def __init__(self, name, specs, *, order=100, owner=None):
        self.name = name
        self.order = order
        self.owner = owner
        self._specs = specs

    async def collect(self, scope):
        return list(self._specs)


def _runtime():
    return SimpleNamespace(bot_id="1", context_providers=[], tool_sources=[])


class _Owner:
    pass


def test_register_sorts_by_order():
    runtime = _runtime()
    register_context_provider(runtime, _Provider("b", "B", order=200))
    register_context_provider(runtime, _Provider("a", "A", order=10))
    assert [p.name for p in runtime.context_providers] == ["a", "b"]


def test_register_replaces_same_name():
    runtime = _runtime()
    register_context_provider(runtime, _Provider("mem", "旧"))
    register_context_provider(runtime, _Provider("mem", "新"))
    assert len(runtime.context_providers) == 1
    assert runtime.context_providers[0]._text == "新"


async def test_collect_returns_blocks_in_order_and_drops_empty():
    runtime = _runtime()
    owner = _Owner()
    register_context_provider(runtime, _Provider("mem", "记忆内容", order=10, owner=owner))
    register_context_provider(runtime, _Provider("log", "   ", order=20, owner=owner))
    register_context_provider(runtime, _Provider("ref", "焦点", order=30, owner=owner))

    blocks = await collect_context_blocks(runtime, TurnScope(runtime=runtime))
    assert blocks == [("mem", "记忆内容"), ("ref", "焦点")]


async def test_collect_ignores_failing_provider():
    runtime = _runtime()
    register_context_provider(runtime, _Provider("bad", "", order=10, boom=True))
    register_context_provider(runtime, _Provider("good", "好的", order=20))
    blocks = await collect_context_blocks(runtime, TurnScope(runtime=runtime))
    assert blocks == [("good", "好的")]


async def test_tool_sources_contribute_specs():
    runtime = _runtime()
    register_tool_source(runtime, _Source("onebot", ["s1", "s2"], order=10))
    register_tool_source(runtime, _Source("mcp", ["s3"], order=20))
    specs = await collect_tool_specs(runtime, TurnScope(runtime=runtime))
    assert specs == ["s1", "s2", "s3"]


def test_unregister_owner_removes_both_buckets():
    runtime = _runtime()
    owner = _Owner()
    other = _Owner()
    register_context_provider(runtime, _Provider("a", "A", owner=owner))
    register_tool_source(runtime, _Source("t", [], owner=owner))
    register_context_provider(runtime, _Provider("b", "B", owner=other))

    removed = unregister_owner(runtime, owner)
    assert removed == 2
    assert [p.name for p in runtime.context_providers] == ["b"]
    assert runtime.tool_sources == []


def test_register_rejects_non_conforming():
    runtime = _runtime()
    with pytest.raises(ValueError):
        register_context_provider(runtime, object())
    with pytest.raises(ValueError):
        register_tool_source(runtime, object())


async def test_simple_provider_accepts_sync_and_async():
    runtime = _runtime()
    register_context_provider(runtime, SimpleContextProvider("sync", lambda scope: "同步"))

    async def _async_fn(scope):
        return "异步"

    register_context_provider(runtime, SimpleContextProvider("async", _async_fn, order=200))
    blocks = await collect_context_blocks(runtime, TurnScope(runtime=runtime))
    assert blocks == [("sync", "同步"), ("async", "异步")]


async def test_simple_tool_source_returns_list():
    runtime = _runtime()
    register_tool_source(runtime, SimpleToolSource("t", lambda scope: ["x"]))
    assert await collect_tool_specs(runtime, TurnScope(runtime=runtime)) == ["x"]


# ---------- 接进真实取值/装配链路 ----------


async def test_extra_blocks_render_between_group_log_and_history():
    """贡献的块出现在装配结果里，位置在 group_log 之后、history 之前。"""
    from app.agent.assembly.assembler import PromptRequest, build_messages

    req = PromptRequest(
        config={"system_prompt": "你是助手"},
        session_id="group_1",
        is_private=False,
        user_text="本轮消息",
        history=[{"role": "user", "content": "上一句"}],
        group_log_text="群记录",
        extra_blocks=[("mem", "记忆块正文")],
    )
    messages = build_messages(req)
    contents = [str(m.get("content")) for m in messages]
    assert "群记录" in contents
    assert "记忆块正文" in contents
    assert contents.index("群记录") < contents.index("记忆块正文") < contents.index("上一句")


def test_prompt_request_has_extra_blocks_field():
    from app.agent.assembly.assembler import PromptRequest

    assert PromptRequest(session_id="g", is_private=False).extra_blocks == []
