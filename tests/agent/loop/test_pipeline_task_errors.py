"""步骤 3 的回归测试：任务异常必须被取走并落日志，不能静默消失。"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent.loop.context import LlmContext, LlmJob
from app.agent.loop.pipeline import LlmPipeline


class _Runtime:
    def __init__(self):
        self.bot_id = "1"
        self.config = {"interrupt_enable": False, "private_enable": True}
        self.proactive = None
        self.output = None
        self.llm_hooks = None
        self.telemetry = None

    def provider_config(self):
        return {"api_key": "k"}


def _pipeline() -> LlmPipeline:
    return LlmPipeline(_Runtime())


def _job() -> LlmJob:
    runtime = _Runtime()
    ctx = LlmContext(
        event=SimpleNamespace(
            event_type="message_private",
            message_type="private",
            user_id=1,
            self_id=2,
            message=[],
        ),
        runtime=runtime,
        bot=None,
        session_id="private_1",
        job=None,
        user_text="hi",
    )
    job = LlmJob(id="abcdef123456", group_key="private_1", ctx=ctx)
    ctx.job = job
    return job


def test_run_prologue_is_inside_try():
    """静态守卫：`_run` 里 prologue 不得再出现"try 之前的取属性"（防回归）。"""
    from pathlib import Path

    src = Path("app/agent/loop/pipeline.py").read_text(encoding="utf-8")
    start = src.index("async def _run(self, job: LlmJob)")
    body = src[start : start + 900]
    try_at = body.index("try:")
    prologue = body[:try_at]
    assert "job.ctx" in prologue  # 只允许这一句（finally 需要）
    # 允许：session_lock / lock_acquired / ctx / outcome 四个必要初值
    assert prologue.count("=") <= 4, f"prologue 太多逻辑，应移进 try: {prologue!r}"
