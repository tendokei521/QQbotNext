"""有界并发池 + 独占屏障（P3 第 2 步）。

四条不变量，逐条钉住：

1. 同一时刻在执行的工具数 ≤ `parallel_max`；
2. 独占工具前后不与其他工具重叠（它是屏障）；
3. 回填顺序永远等于模型顺序（与完成先后无关）；
4. 拿不到 spec 的执行器（第三方自定义）一律独占——串行只是慢，并发写错不可逆。
"""

from __future__ import annotations

import asyncio

from app.agent.loop.tool_loop import execute_tool_batch
from app.agent.tools.spec import ToolSpec


class _Tracker:
    """记录"当前有几个工具在跑"的峰值与开始/结束事件序列。"""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.events: list[str] = []

    async def run(self, item: dict) -> str:
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.events.append(f"+{item['name']}")
        await asyncio.sleep(0.01)
        self.active -= 1
        self.events.append(f"-{item['name']}")
        return item["name"]


def _spec(name: str, *, concurrency: str = "exclusive", safe=None) -> ToolSpec:
    async def _handler(ctx, args):
        return ""

    return ToolSpec(
        name=name,
        description="",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        scopes=("*",),
        concurrency=concurrency,
        concurrency_safe=safe,
    )


def _item(name: str, **args) -> dict:
    return {"name": name, "args": args or {}, "id": f"c_{name}", "tc": {}}


async def test_parallel_pool_never_exceeds_the_limit():
    tracker = _Tracker()
    specs = {n: _spec(n, concurrency="parallel") for n in "abcd"}
    prepared = [_item(n) for n in "abcd"]

    results = await execute_tool_batch(
        prepared, tracker.run, specs_by_name=specs, parallel_max=2
    )

    assert tracker.peak == 2, f"峰值并发应恰好等于池大小 2，实际 {tracker.peak}"
    assert results == ["a", "b", "c", "d"], "结果顺序必须等于模型顺序"


async def test_exclusive_tool_is_a_barrier():
    tracker = _Tracker()
    specs = {
        "p1": _spec("p1", concurrency="parallel"),
        "p2": _spec("p2", concurrency="parallel"),
        "w": _spec("w"),  # 独占
    }

    await execute_tool_batch(
        [_item("p1"), _item("p2"), _item("w")],
        tracker.run,
        specs_by_name=specs,
        parallel_max=4,
    )

    assert tracker.peak == 2, "两个并行工具可以同时跑，独占的不许挤进来"
    write_start = tracker.events.index("+w")
    write_end = tracker.events.index("-w")
    # 独占区间内不得有其他工具的结束/开始事件
    assert tracker.events[write_start + 1] == "-w"
    assert write_end > write_start
    assert tracker.events.index("-p1") < write_start and tracker.events.index("-p2") < write_start


async def test_unknown_executor_is_serial():
    """没有 spec 信息 → 全串行（fail-closed）。"""
    tracker = _Tracker()

    await execute_tool_batch([_item("a"), _item("b")], tracker.run, specs_by_name=None, parallel_max=8)

    assert tracker.peak == 1
    assert tracker.events == ["+a", "-a", "+b", "-b"]


async def test_per_call_classification_is_honoured():
    """同一个工具按实参分类：读并行、写独占。"""
    tracker = _Tracker()
    specs = {
        "api": _spec(
            "api", concurrency="parallel", safe=lambda args: args.get("action") == "read"
        )
    }

    results = await execute_tool_batch(
        [_item("api", action="read"), _item("api", action="write"), _item("api", action="read")],
        tracker.run,
        specs_by_name=specs,
        parallel_max=4,
    )

    assert results == ["api", "api", "api"]
    # 写操作是屏障：它开始时另一条读必须已经结束（事件序列里 -api 在 +api 之前）
    assert tracker.peak <= 2, "两条读可以并行，但写不允许与任何读重叠"
    assert tracker.events.count("+api") == 3
