"""消息抖动（debounce）：等静默 + 同批消息合并。

背景：语义是「收到能触发 LLM 的消息后先等 N 秒，期间又来新消息则重新计时，并把
这几条合并成同一轮请求」。历史行为是 ``debounce=0``（立即请求），于是用户连发
四条消息就变成四次请求、四次回复——观感就是「一条一条挨个回」。

三个必须钉住的性质：
1. 抖动窗口内只放行最后一条（前面的 Job 被取代并退出，绝不能再各发一次）；
2. ``max_wait`` 从批次第一条消息起算，持续刷屏时到点强制放行（否则永远不响应）；
3. 合并要把每条单独截断，而不是整段截断——整段截会把后面的消息直接丢掉。
"""

from __future__ import annotations

import asyncio

from app.llm.config_schema import SCHEMA
from app.llm.context import LlmContext, LlmJob
from app.llm.pool import LlmPool, merge_debounced_texts


class _Event:
    pass


def _make_job(pool: LlmPool, name: str, *, key: str = "group_1") -> LlmJob:
    ctx = LlmContext(
        event=_Event(),
        runtime=None,
        bot=None,
        session_id=key,
        user_text=name,
    )
    job = LlmJob(id=name, group_key=key, ctx=ctx)
    ctx.job = job
    return job


# ==================== 合并 ====================


def test_merge_keeps_order_and_skips_blank():
    merged = merge_debounced_texts(["一", "  ", "", "二", "三"])
    assert merged == "一\n二\n三"


def test_merge_truncates_each_message_not_the_whole_batch():
    # 每条 5 字、上限 3：期望两条都留下被截断的版本，而不是只留第一条
    merged = merge_debounced_texts(["aaaaa", "bbbbb"], limit=3)
    assert merged == "aaa\nbbb"


def test_merge_without_limit_keeps_full_text():
    assert merge_debounced_texts(["aaaaa", "bbbbb"]) == "aaaaa\nbbbbb"


def test_merge_of_empty_batch_is_empty_string():
    assert merge_debounced_texts([]) == ""


# ==================== 抖动窗口 ====================


async def test_debounce_supersedes_earlier_messages():
    """窗口内的前几条必须被取代（返回 False），只有最后一条继续。"""
    pool = LlmPool()

    async def run(name: str):
        job = _make_job(pool, name)
        ok = await pool.wait_for_continue(job, debounce=0.15)
        return name, ok

    tasks = [asyncio.create_task(run("第一条"))]
    await asyncio.sleep(0.03)
    tasks.append(asyncio.create_task(run("第二条")))
    await asyncio.sleep(0.03)
    tasks.append(asyncio.create_task(run("第三条")))

    results = await asyncio.gather(*tasks)
    assert results == [("第一条", False), ("第二条", False), ("第三条", True)]


async def test_pending_texts_collect_every_message_in_window():
    """存活的 Job 能取到整批消息文本（供合并成一轮请求）。"""
    pool = LlmPool()
    survivor: dict[str, LlmJob] = {}

    async def run(name: str):
        job = _make_job(pool, name)
        ok = await pool.wait_for_continue(job, debounce=0.12)
        if ok:
            survivor["job"] = job
        return ok

    tasks = [asyncio.create_task(run(name)) for name in ("甲", "乙", "丙")]
    # 依次到达，每条都重置计时器
    for _ in tasks:
        await asyncio.sleep(0.02)
    await asyncio.gather(*tasks)

    batch = pool.take_pending_texts("group_1")
    assert batch == ["甲", "乙", "丙"]
    # 取走后不重复给出
    assert pool.take_pending_texts("group_1") == []
    pool.finish(survivor["job"])


async def test_debounce_is_per_session():
    """不同会话互不干扰：A 会话的抖动不应拖住 B 会话。"""
    pool = LlmPool()

    async def run(name: str, key: str):
        job = _make_job(pool, name, key=key)
        return await pool.wait_for_continue(job, debounce=0.05)

    assert await asyncio.gather(run("a", "group_1"), run("b", "private_2")) == [True, True]


async def test_max_wait_releases_despite_continuous_messages():
    """持续刷屏（始终不到静默 10s）时，max_wait 到点必须放行。

    注意 max_wait 的语义是「周期性强制放行」：刷屏期间会有多次放行，
    每次放行之间到达的消息被合并。所以这里断言的是「没有被无限拖延」，
    而不是「只有最后一条放行」。
    """
    pool = LlmPool()
    released_at: list[float] = []
    loop = asyncio.get_running_loop()
    started = loop.time()

    async def run(name: str):
        job = _make_job(pool, name)
        ok = await pool.wait_for_continue(job, debounce=10.0, max_wait=0.25)
        if ok:
            released_at.append(loop.time() - started)
            pool.finish(job)
        return ok

    tasks = []
    for i in range(6):
        tasks.append(asyncio.create_task(run(f"m{i}")))
        await asyncio.sleep(0.08)  # 每条都在“静默 10s”之前到达

    results = await asyncio.gather(*tasks)

    # 关键性质 1：确实放行过（没有被永无止境的重置拖住）
    assert any(results)
    # 关键性质 2：也确实合并过（不是每条都各发一次）
    assert not all(results)
    # 关键性质 3：第一次放行发生在 max_wait 附近，而不是 6 条消息的总时长之后
    assert released_at and released_at[0] < 0.45


async def test_zero_debounce_is_immediate():
    """debounce=0 必须保持旧行为：立刻放行，不加任何延迟。"""
    pool = LlmPool()
    job = _make_job(pool, "直接")
    loop = asyncio.get_running_loop()
    started = loop.time()
    assert await pool.wait_for_continue(job, debounce=0)
    assert loop.time() - started < 0.05
    pool.finish(job)


# ==================== 配置面 ====================


def test_debounce_config_defaults_off():
    """默认必须关闭：不能悄悄改变现有部署的请求时机。"""
    assert SCHEMA["message_debounce_seconds"]["default"] == 0
    assert SCHEMA["message_debounce_max_wait"]["default"] > 0


def test_debounce_max_wait_only_shows_when_debounce_on():
    gate = SCHEMA["message_debounce_max_wait"]["showIf"]
    assert gate == {"key": "message_debounce_seconds", "gt": 0}
