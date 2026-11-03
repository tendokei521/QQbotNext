"""参与度模式的「轮次优先」：同群同时只允许一个 LLM 回合。

- legacy：新消息打断旧回合并取代它（现有行为，见 test_llm_interrupt.py）；
- participation：上一个回合没结束时，新消息只观察/落历史，不打断、不请求；
  回合结束后同群才能继续请求。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.agent.loop.pipeline import LlmPipeline


class _HooksStub:
    def get(self, stage, event_type):
        return []


class _ConfigStub(dict):
    def set_session(self, session_id):
        return None

    def clear_session(self):
        return None


class _RuntimeStub:
    bot_id = 1

    def __init__(self, **config):
        config.setdefault("group_enable", True)
        config.setdefault("reply_mode", "participation")
        config.setdefault("participation_level", "low")
        config.setdefault("trigger_at", True)
        self.config = _ConfigStub(config)
        self.llm_hooks = _HooksStub()
        self.proactive = None

    def provider_config(self):
        return {"api_key": "test-key"}


def _group_event(group_id: int, text: str, *, at: bool = False) -> SimpleNamespace:
    message = []
    if at:
        message.append({"type": "at", "data": {"qq": "1"}})
    message.append({"type": "text", "data": {"text": text}})
    return SimpleNamespace(
        event_type="message_group",
        message_type="group",
        message_id=hash(text) & 0xFFFFFF,
        group=SimpleNamespace(group_id=group_id),
        user_id=2,
        self_id=1,
        text=text,
        message=message,
        bot=SimpleNamespace(),
    )


async def test_participation_keeps_first_turn_and_drops_new_message():
    pipeline = LlmPipeline(_RuntimeStub())
    session = "group_123456"

    first_event = _group_event(123456, "第一条")
    first = pipeline.submit(first_event)
    first_job = first_event._llm_job
    assert first is not None
    assert pipeline._inflight.get(session) == first_job.id
    generation_before = pipeline._session_generation.get(session, 0)

    second_event = _group_event(123456, "第二条")
    second = pipeline.submit(second_event)
    second_job = second_event._llm_job
    assert second is not None
    assert second_job.busy_dropped is True, "上一轮未结束，新消息必须只观察不请求"
    assert pipeline._inflight.get(session) == first_job.id, "在飞回合不能被新消息顶掉"
    assert pipeline._session_generation.get(session, 0) == generation_before, (
        "轮次优先模式不能抬高代际/打断旧回合"
    )

    first.cancel()
    second.cancel()
    await asyncio.sleep(0)


async def test_participation_releases_slot_after_turn_finishes():
    pipeline = LlmPipeline(_RuntimeStub())
    session = "group_123456"

    first_event = _group_event(123456, "第一条")
    first = pipeline.submit(first_event)
    first_job = first_event._llm_job
    assert pipeline._inflight.get(session) == first_job.id

    # 模拟回合正常结束：_run 的 finally 会释放槽位
    pipeline._inflight.pop(session, None)
    second_event = _group_event(123456, "第二条")
    second = pipeline.submit(second_event)
    second_job = second_event._llm_job
    assert second_job.busy_dropped is False, "上一轮结束后同群应能继续请求"
    assert pipeline._inflight.get(session) == second_job.id

    first.cancel()
    second.cancel()
    await asyncio.sleep(0)


async def test_busy_drop_task_observes_and_returns_without_starting_llm(monkeypatch):
    pipeline = LlmPipeline(_RuntimeStub())
    session = "group_123456"
    observed: list[str] = []

    async def _fake_observe(ctx):
        observed.append(ctx.session_id)

    monkeypatch.setattr(pipeline, "_observe_busy_drop", _fake_observe)

    first_event = _group_event(123456, "第一条")
    first = pipeline.submit(first_event)
    second_event = _group_event(123456, "第二条")
    second = pipeline.submit(second_event)

    await second
    assert observed == [session], "忙时的新消息只做观察，不进入 LLM"
    assert pipeline._inflight.get(session) == first_event._llm_job.id

    first.cancel()
    await asyncio.sleep(0)


async def test_legacy_mode_still_interrupts():
    pipeline = LlmPipeline(_RuntimeStub(reply_mode="legacy"))
    session = "group_123456"

    first = pipeline.submit(_group_event(123456, "第一条", at=True))
    generation = pipeline._session_generation.get(session, 0)
    second = pipeline.submit(_group_event(123456, "第二条", at=True))

    assert pipeline._session_generation.get(session, 0) > generation, "旧模式仍以最新消息打断旧回合"
    assert second is not None
    first.cancel()
    second.cancel()
    await asyncio.sleep(0)
