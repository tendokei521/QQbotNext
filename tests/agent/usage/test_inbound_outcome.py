"""回归测试：入站结局必须在"做决定的地方"判定，不能靠提交时挂的 `_llm_job`。

历史 bug：`pipeline.submit()` 同步给事件挂 `_llm_job`，随后入站节点据此判"llm"，
于是**每一条收到的群消息都被算成"需响应"**（96 收到 → 96 需响应）。

现在的契约：
- `submit()` 只打 `_stat_owner="pipeline"` 标记，入站节点不记账
- `pipeline._run` 用局部 `outcome` 精确判定：command / llm / ignored
- 入站节点只在"没交给流水线"时记账（module / ignored）
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agent.usage.messages import STAT_OWNER_ATTR, record_inbound
from app.agent.usage.recorder import StatRecorder, set_recorder
from app.infrastructure.persistence.database import Database


@pytest.fixture
async def env(tmp_path):
    db = Database(tmp_path / "inbound.db")
    await db.connect()
    rec = StatRecorder(db, flush_max=1000, flush_interval=0)
    set_recorder(rec)
    yield rec, db
    await rec.close()
    set_recorder(None)
    await db.close()


def _group_event(text="随便聊聊", *, stopped=False, llm_stop=False):
    from app.domain.events import GroupMessageEvent, MessageSegment, UserInfo

    ev = GroupMessageEvent(
        event_type="message_group",
        message_type="group",
        time=1792670176,
        user_id=1901691195,
        self_id=99,
        message=[MessageSegment("text", {"text": text})],
        user=UserInfo(user_id=1901691195, nickname="小明同学真好看"),
        group=SimpleNamespace(group_id=466052056, group_name="测试群名称很长"),
    )
    ev._stopped = stopped
    ev._llm_stop = llm_stop
    return ev


# ==================== 记账去重与归属 ====================


async def test_record_inbound_writes_once_per_event(env):
    rec, db = env
    ev = _group_event()
    assert record_inbound(ev, "llm") is True
    assert record_inbound(ev, "llm") is False, "同一个事件只该记一条"
    await rec.flush()
    rows = await db.fetchall("SELECT * FROM stat_event WHERE kind='msg_in'")
    assert len(rows) == 1


async def test_pipeline_owned_event_is_not_double_counted(env):
    """`submit()` 打标后，入站节点不应再记（否则一条消息记两次）。"""
    rec, db = env
    ev = _group_event()
    setattr(ev, STAT_OWNER_ATTR, "pipeline")

    # 入站节点的记账函数看到归属标记就直接返回
    from app.plugin.nodes.inbound import AgentNode
    from app.plugin.nodes.base import MessageContext

    node = AgentNode(agent_manager=None, config_service=None, gateway=None)
    node._record_inbound(SimpleNamespace(event=ev))
    await rec.flush()
    rows = await db.fetchall("SELECT COUNT(*) AS c FROM stat_event")
    assert rows[0]["c"] == 0, "交给流水线的消息，入站节点不该记"


async def test_inbound_records_module_outcome_when_not_submitted(env):
    """模块拦下的消息（没交给流水线）→ outcome=module。"""
    rec, db = env
    ev = _group_event(stopped=True)
    from app.plugin.nodes.inbound import AgentNode

    node = AgentNode(agent_manager=None, config_service=None, gateway=None)
    node._record_inbound(SimpleNamespace(event=ev))
    await rec.flush()
    row = (await db.fetchall("SELECT * FROM stat_event WHERE kind='msg_in'"))[0]
    assert row["outcome"] == "module"


async def test_inbound_records_ignored_for_gate_blocked(env):
    """门控拦下的消息 → outcome=ignored（收到但不需要响应）。"""
    rec, db = env
    ev = _group_event()
    from app.plugin.nodes.inbound import AgentNode

    node = AgentNode(agent_manager=None, config_service=None, gateway=None)
    node._record_inbound(SimpleNamespace(event=ev))
    await rec.flush()
    row = (await db.fetchall("SELECT * FROM stat_event WHERE kind='msg_in'"))[0]
    assert row["outcome"] == "ignored"


# ==================== pipeline 的 outcome 分支 ====================


def _pipeline():
    from app.agent.loop.pipeline import LlmPipeline

    runtime = SimpleNamespace(
        bot_id="1",
        config={"interrupt_enable": False, "private_enable": True, "group_enable": True, "trigger_at": True},
        proactive=None,
        output=None,
        llm_hooks=None,
        telemetry=None,
        # 流水线会在请求前检查 API key，给个假的
        provider_config=lambda: {"api_key": "sk-test"},
    )
    return LlmPipeline(runtime)


def _job_of(event):
    from app.agent.loop.context import LlmContext, LlmJob

    ctx = LlmContext(
        event=event, runtime=_pipeline().runtime, bot=None,
        session_id="group_466052056", job=None, user_text="随便聊聊",
    )
    job = LlmJob(id="abcdef123456", group_key="group_466052056", ctx=ctx)
    ctx.job = job
    return job


async def test_pipeline_marks_command_outcome(env, monkeypatch):
    """指令消息 → outcome=command。"""
    rec, db = env
    pipeline = _pipeline()
    event = _group_event("#llm help")
    job = _job_of(event)
    job.ctx.user_text = "#llm help"

    async def _fake_handle(runtime, ev):
        return None

    import app.agent.loop.chat as chat_mod

    monkeypatch.setattr(chat_mod, "handle", _fake_handle)
    await pipeline._run(job)
    await rec.flush()
    rows = await db.fetchall("SELECT outcome FROM stat_event WHERE kind='msg_in'")
    assert [r["outcome"] for r in rows] == ["command"]


async def test_pipeline_marks_ignored_when_not_triggered(env, monkeypatch):
    """群消息没 @ 也没关键词 → 不触发，outcome=ignored（不再被算成需响应）。"""
    rec, db = env
    pipeline = _pipeline()
    event = _group_event("随便聊聊")
    job = _job_of(event)

    # 群消息只在 check_trigger 里判定；这里不 mock，让它自然返回未触发
    await pipeline._run(job)
    await rec.flush()
    rows = await db.fetchall("SELECT outcome FROM stat_event WHERE kind='msg_in'")
    assert [r["outcome"] for r in rows] == ["ignored"], "未触发的消息不该算需响应"


async def test_pipeline_marks_llm_when_actually_requested(env, monkeypatch):
    """真的进入 LLM 请求 → outcome=llm。"""
    rec, db = env
    pipeline = _pipeline()
    event = _group_event("@机器人 你好")
    event.message = [SimpleNamespace(type="at", data={"qq": "99"}),
                     SimpleNamespace(type="text", data={"text": " 你好"})]
    job = _job_of(event)
    job.ctx.user_text = "你好"

    called: list[str] = []

    async def _fake_drive(ctx, strategy):
        called.append("drive")

    monkeypatch.setattr(pipeline, "_drive", _fake_drive)
    await pipeline._run(job)
    await rec.flush()
    rows = await db.fetchall("SELECT outcome FROM stat_event WHERE kind='msg_in'")
    assert called == ["drive"], "应真的走到请求阶段"
    assert [r["outcome"] for r in rows] == ["llm"]
