"""回复打断（interrupt）语义回归测试。

背景：远端日志（``1/log/qqbot-logs-20261017144932``）显示私聊开启「回复打断」后
仍然一条一条挨个回复——表现为"幽灵消息"/请求排队不取消。

根因：``b86fc18`` 把代际自增从 ``submit()`` 搬到 ``_run()`` 里"确认真正触发 LLM 后"，
于是任务在拿到会话锁之后**自己**把代际自增，并把自己"认领"为最新代际::

    if getattr(self.runtime, "interrupt_enabled", False):
        self.interrupt_session(ctx.session_id)
        ctx.job.generation = self._session_generation.get(ctx.session_id, 0)

这带来两个后果：

1. 旧任务在 ``await session_lock.acquire()`` 上排队等待时被打断（代际被新任务抬高），
   但拿到锁后立刻把自己的代际改写成当前值 → ``is_stale`` 永远为 False →
   它照样发请求、照样回复。新消息只能等它彻底跑完。
2. 正在流式输出的任务被打断后，只要它自己再执行一次上面的赋值就"复活"，
   ``_run_stream`` 里的 ``is_stale`` 检查随即失效，剩余句子继续一条条发出去。

修好后：代际只在「新消息提交且确实会触发 LLM」时由 ``submit()`` 抬高，
任务自身绝不改写自己的代际，``is_stale`` 只做"我是否已被后来的消息取代"的单调判定。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.agent.loop.context import LlmContext, LlmJob
from app.agent.loop.pipeline import LlmPipeline


class _HooksStub:
    def get(self, stage, event_type):
        return []


class _ConfigStub(dict):
    """最小 config：``set_session``/``clear_session`` 是 no-op。"""

    def set_session(self, session_id):
        return None

    def clear_session(self):
        return None


class _RuntimeStub:
    bot_id = 1

    def __init__(self, **config):
        config.setdefault("interrupt_enable", True)
        self.config = _ConfigStub(config)
        self.llm_hooks = _HooksStub()
        self.proactive = None
        self.interrupt_enabled = True

    def provider_config(self):
        return {"api_key": "test-key"}


def _private_event(user_id: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        event_type="message_private",
        message_type="private",
        message_id=hash(text) & 0xFFFFFF,
        user_id=user_id,
        self_id=1,
        text=text,
        message=[{"type": "text", "data": {"text": text}}],
        bot=SimpleNamespace(),
    )


def _group_event(group_id: int, text: str, self_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        event_type="message_group",
        message_type="group",
        message_id=hash(text) & 0xFFFFFF,
        group=SimpleNamespace(group_id=group_id),
        user_id=2,
        self_id=self_id,
        text=text,
        message=[{"type": "text", "data": {"text": text}}],
        bot=SimpleNamespace(),
    )


def test_stale_job_must_not_refresh_its_own_generation():
    """核心回归：被取代的任务不能把自己的代际改写成最新，否则打断永久失效。

    这是远端日志的直接成因——旧任务在锁上排队时被打断，拿到锁后又"自我复活"。
    """
    pipeline = LlmPipeline(_RuntimeStub())
    session = "private_3688513383"

    # 旧任务先于新消息存在
    old = LlmJob(id="old", group_key=session, generation=0)
    assert pipeline.is_stale(old) is False

    # 新消息到达：打断旧任务
    pipeline.interrupt_session(session)
    assert pipeline.is_stale(old) is True

    # 旧任务拿到锁后**不得**把自己刷新为最新代际
    fresh = pipeline._adopt_generation_if_latest(old)
    assert fresh is False, "被取代的任务不应被判定为最新代际"
    assert pipeline.is_stale(old) is True, "旧任务必须保持过期"


async def test_latest_job_stays_fresh_after_adopting_generation():
    """未被取代的任务认领当前代际后仍然是新鲜的（不能把正常任务误判为过期）。"""
    pipeline = LlmPipeline(_RuntimeStub())
    session = "private_10086"

    task = pipeline.submit(_private_event(10086, "只有这一条"))
    assert task is not None
    # submit 已经为这条真触发的消息抬高过代际，任务据此认领
    job = LlmJob(
        id="only",
        group_key=session,
        generation=pipeline._session_generation[session],
    )

    assert pipeline._adopt_generation_if_latest(job) is True
    assert pipeline.is_stale(job) is False
    task.cancel()


async def test_submit_bumps_generation_for_private_message():
    """私聊消息提交即抬高代际：在跑的任务随后必然观察到 is_stale。"""
    pipeline = LlmPipeline(_RuntimeStub())
    session = "private_3688513383"

    first = pipeline.submit(_private_event(3688513383, "第一条"))
    assert first is not None
    gen_after_first = pipeline._session_generation[session]

    second = pipeline.submit(_private_event(3688513383, "第二条"))
    assert second is not None
    assert pipeline._session_generation[session] == gen_after_first + 1

    first.cancel()
    second.cancel()


async def test_non_triggering_group_message_does_not_interrupt():
    """群聊未被 @/未命中关键词的普通消息不得抬高代际（b86fc18 要防的误打断）。"""
    pipeline = LlmPipeline(_RuntimeStub(trigger_at=True, trigger_keyword=[]))
    session = "group_237881486"

    first = pipeline.submit(_group_event(237881486, "路过看看"))
    assert pipeline._session_generation.get(session, 0) == 0

    second = pipeline.submit(_group_event(237881486, "   "))
    assert pipeline._session_generation.get(session, 0) == 0

    first.cancel()
    second.cancel()


async def test_triggering_group_message_interrupts():
    """群聊 @ 机器人属于真触发，应当抬高代际打断旧任务。"""
    pipeline = LlmPipeline(_RuntimeStub(trigger_at=True, trigger_keyword=[]))
    session = "group_237881486"

    at_event = _group_event(237881486, "在吗")
    at_event.message = [
        {"type": "at", "data": {"qq": "1"}},
        {"type": "text", "data": {"text": "在吗"}},
    ]
    task = pipeline.submit(at_event)
    assert pipeline._session_generation[session] == 1
    task.cancel()


async def test_inflight_stream_is_cut_off_by_new_message(monkeypatch):
    """在飞流式任务被新消息打断后，必须停止继续发剩余句子。

    这是"幽灵消息"的直接体现：旧任务被取代后仍在一条条往外发。
    """
    pipeline = LlmPipeline(_RuntimeStub(stream_send_pool_enabled=False))

    yielded = 0

    async def fake_stream(runtime, event, ctx):
        nonlocal yielded
        for i in range(6):
            yielded += 1
            # 第 2 句产出时模拟"用户又发了一条新消息"
            if i == 1:
                pipeline.submit(_private_event(event.user_id, "新消息"))
            yield f"第{i + 1}句。"

    monkeypatch.setattr("app.agent.loop.chat.stream_response", fake_stream)

    sent: list[str] = []

    async def _capture(ctx, msg):
        sent.append(msg.text)
        await asyncio.sleep(0)

    monkeypatch.setattr(pipeline, "_send", _capture)

    event = _private_event(3688513383, "开始")
    job = LlmJob(id="inflight", group_key="private_3688513383", generation=0)
    ctx = LlmContext(
        event=event,
        runtime=pipeline.runtime,
        bot=SimpleNamespace(),
        session_id="private_3688513383",
        job=job,
    )

    await pipeline._run_stream(ctx)

    assert ctx.state.get("interrupted") is True, "在飞任务应被判定为打断"
    assert len(sent) < 6, f"打断后不应继续发完所有句子，实际发出 {len(sent)} 条"
    # 收尾时不应留下未取消的后台任务
    await asyncio.sleep(0)


async def test_command_job_is_not_dropped_by_generation_gate(monkeypatch):
    """`#llm` 指令不参与打断代际，排队期间被新消息超越也不能被丢掉。

    指令走独立分支（不产生流式回复），``_adopt_generation_if_latest`` 必须排在
    该分支之后；否则"新消息顶掉排队中的指令"会变成新的回归。
    """
    pipeline = LlmPipeline(_RuntimeStub())

    seen: list = []

    async def fake_handle(runtime, event):
        seen.append(event)

    monkeypatch.setattr("app.agent.loop.chat.handle", fake_handle)

    event = _private_event(3688513383, "#llm help")
    job = LlmJob(id="cmd", group_key="private_3688513383", generation=0)
    ctx = LlmContext(
        event=event,
        runtime=pipeline.runtime,
        bot=event.bot,
        session_id="private_3688513383",
        user_text="#llm help",
        job=job,
    )
    job.ctx = ctx

    # 模拟"指令在排队期间，同会话又来了一条真触发的新消息"
    pipeline.interrupt_session("private_3688513383")
    assert pipeline.is_stale(job) is True

    await pipeline._run(job)

    assert seen == [event], "指令必须照常执行，不能被代际判定丢掉"


async def test_command_does_not_interrupt_inflight_reply():
    """`#llm` 指令本身不该抬高代际去打断在跑的回复。"""
    pipeline = LlmPipeline(_RuntimeStub())
    session = "private_3688513383"

    before = pipeline._session_generation.get(session, 0)
    task = pipeline.submit(_private_event(3688513383, "#llm help"))
    assert task is not None
    assert pipeline._session_generation.get(session, 0) == before
    task.cancel()


async def test_stream_not_cut_off_when_interrupt_disabled(monkeypatch):
    """关闭回复打断时保持老行为：在跑的一轮把已生成的句子发完。

    这是功能开关的契约边界——不能因为修 bug 就让"关"变成"开"。
    """
    pipeline = LlmPipeline(_RuntimeStub(stream_send_pool_enabled=False, interrupt_enable=False))

    async def fake_stream(runtime, event, ctx):
        for i in range(4):
            if i == 1:
                pipeline.submit(_private_event(event.user_id, "新消息"))
            yield f"第{i + 1}句。"

    monkeypatch.setattr("app.agent.loop.chat.stream_response", fake_stream)

    sent: list[str] = []

    async def _capture(ctx, msg):
        sent.append(msg.text)
        await asyncio.sleep(0)

    monkeypatch.setattr(pipeline, "_send", _capture)

    event = _private_event(3688513383, "开始")
    job = LlmJob(id="inflight", group_key="private_3688513383", generation=0)
    ctx = LlmContext(
        event=event,
        runtime=pipeline.runtime,
        bot=SimpleNamespace(),
        session_id="private_3688513383",
        job=job,
    )

    await pipeline._run_stream(ctx)

    assert len(sent) == 4, "关闭打断时不应截断在跑的流"
    assert ctx.state.get("interrupted") is not True
