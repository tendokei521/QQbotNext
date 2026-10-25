"""超时策略（P3 第 3 步）：不掐协程，只换结果。

旧实现用 `asyncio.wait_for`：一旦超时就 **cancel** 工具协程，把"发了一半的消息 /
写了一半的文件"掐在半路。新语义是"声明 + 策略"：

- 工具可以用 `timeout_s` 声明自己的墙钟上限，否则用全局默认；
- 超时只把**结果**换成 `TOOL_TIMEOUT`（模型看到的是失败回执），协程继续跑并自行收敛；
- 协程最终成功/失败都会留一行日志——这是"超时之后群里还是冒出一条消息"的唯一线索。
"""

from __future__ import annotations

import asyncio

from app.agent.tools.spec import ToolSpec, make_executor


def _ctx(**config) -> object:
    runtime = type("R", (), {"config": config, "bot_id": "1"})()
    return type("C", (), {"runtime": runtime, "event": None, "session_id": "group_1"})()


def _spec(handler, *, timeout_s=None) -> ToolSpec:
    return ToolSpec(
        name="slow",
        description="",
        parameters={"type": "object", "properties": {}},
        handler=handler,
        scopes=("*",),
        timeout_s=timeout_s,
    )


async def test_declared_timeout_replaces_the_result_but_not_the_task():
    """超时后：结果 = TOOL_TIMEOUT；协程仍在跑（这里用一个事件证明它跑完了）。"""
    finished = asyncio.Event()
    seen: list[str] = []

    async def _handler(ctx, args):
        await asyncio.sleep(0.05)
        seen.append("late")
        finished.set()
        return "迟到的结果"

    executor = make_executor([_spec(_handler, timeout_s=0.01)], _ctx())
    result = await executor("slow", {})

    assert "TOOL_TIMEOUT" in result
    assert seen == [], "超时那一刻结果里不该有它的产出"

    await asyncio.wait_for(finished.wait(), timeout=1)
    assert seen == ["late"], "被超时的协程必须继续跑完（不是被 cancel）"


async def test_global_default_timeout_is_used_when_not_declared():
    async def _handler(ctx, args):
        await asyncio.sleep(0.05)
        return "迟到"

    executor = make_executor([_spec(_handler)], _ctx(tool_timeout_default_s=0.01))
    result = await executor("slow", {})

    assert "TOOL_TIMEOUT" in result and "0.01" in result


async def test_fast_tool_returns_its_result_unchanged():
    async def _handler(ctx, args):
        return "很快"

    executor = make_executor([_spec(_handler, timeout_s=5)], _ctx())

    assert (await executor("slow", {})).startswith("很快")


async def test_tool_exception_is_still_reported_as_a_result():
    async def _handler(ctx, args):
        raise RuntimeError("炸了")

    executor = make_executor([_spec(_handler, timeout_s=5)], _ctx())
    result = await executor("slow", {})

    assert "执行异常" in result and "炸了" in result
