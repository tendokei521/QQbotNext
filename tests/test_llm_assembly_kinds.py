"""四条装配路径的块结构一致性测试。

背景：chat（流式/非流式）、主动消息、定时任务此前各有各的组装方式：
主动/定时路径**缺"消息格式说明"块**、焦点行位置靠手工拼接、历史不压缩。
接入 ``assembly`` 后四条路径共用同一张块表，本文件把该不变量钉住。
"""

from __future__ import annotations

import types

from app.llm.assembly import BLOCKS, PromptRequest, describe
from app.llm.prompt import LEGACY_MESSAGE_META_INSTRUCTION
from app.llm.providers import PROVIDERS
from tests.test_llm_assembly_paths import _ProbeProvider, setup_function  # noqa: F401

EXPECTED_BLOCKS = [b.name for b in BLOCKS]


def _rows(req: PromptRequest) -> list[dict]:
    return describe(req)


def test_declared_block_order_covers_all_kinds():
    """块表是唯一的顺序来源：chat / proactive / schedule / summary 共用同一张表。"""
    assert EXPECTED_BLOCKS == [
        "persona",
        "schedule",
        "proactive",
        "message_meta",
        "skills",
        "memory",
        "background",
        "history",
        "user",
    ]


def test_proactive_prompt_has_same_block_structure_as_chat():
    """主动消息的块序列与 chat 一致（含此前缺失的格式说明块）。"""
    from app.llm.assembly import PromptRequest

    req = PromptRequest(
        config={"system_prompt": "人设", "meta_sender_style": "legacy"},
        session_id="group_1",
        kind="proactive",
        user_text="（主动开口）",
        raw_user_text="（主动开口）",
        history=[{"role": "assistant", "content": "上次说了晚安"}],
        referent_text="### 当前对话焦点",
        skill_blocks=["技能块"],
        memory_text="记忆块",
        available_tools={"get_chat_history"},
        schedule_enable=False,
    )
    rows = _rows(req)
    assert [r["block"] for r in rows] == EXPECTED_BLOCKS
    # 定时协议按 schedule_enable=False 关闭，但格式说明必须在（此前主动消息没有这块）
    assert dict((r["block"], r) for r in rows)["schedule"]["messages"] == 0
    meta_row = [r for r in rows if r["block"] == "message_meta"][0]
    assert meta_row["chars"] == len(LEGACY_MESSAGE_META_INSTRUCTION)


def test_schedule_prompt_has_same_block_structure_as_chat():
    """定时任务的块序列与 chat 一致，且本轮 user 是任务模板内容。"""
    from app.llm.assembly import PromptRequest

    req = PromptRequest(
        config={"system_prompt": "人设", "meta_sender_style": "legacy"},
        session_id="group_2",
        kind="schedule",
        user_text="【定时任务】到点提醒: 吃药",
        raw_user_text="【定时任务】到点提醒: 吃药",
        history=[],
        referent_text="### 当前对话焦点",
        pre_history_text="### 当前聊天环境",
        skill_blocks=[],
        memory_text="",
        available_tools=None,
        schedule_enable=False,
    )
    rows = _rows(req)
    assert [r["block"] for r in rows] == EXPECTED_BLOCKS
    user_row = [r for r in rows if r["block"] == "user"][0]
    assert user_row["chars"] == len("【定时任务】到点提醒: 吃药")
    # 焦点 + 环境合并进同一块 background
    background = [r for r in rows if r["block"] == "background"][0]
    assert background["messages"] == 1


async def test_scheduler_build_messages_uses_assembler(monkeypatch, tmp_path):
    """scheduler._build_messages 真的走装配器：块序列与 chat 一致，且含格式说明。"""
    from app.llm.scheduler import DEFAULT_SCHEDULE_PROMPT, TaskScheduler

    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))

    svc = TaskScheduler.__new__(TaskScheduler)  # 不跑 __init__ 的定时器/文件读写
    svc.session_mgr = _SessionMgr()
    svc.bot = None
    svc.bot_id = 778
    svc.module = _Module()
    entry = types.SimpleNamespace(
        id="job12345678",
        session_id="group_466052056",
        is_group=True,
        target="466052056",
        content="提醒吃药",
        repeat="once",
        fired_count=0,
    )

    messages = await svc._build_messages(entry)
    assert messages[0]["content"] == "人设"
    assert any(m["content"] == LEGACY_MESSAGE_META_INSTRUCTION for m in messages)
    assert any("定时任务" in str(m["content"]) for m in messages)
    assert messages[-1]["role"] == "user"


def _register(monkeypatch, name: str) -> None:
    monkeypatch.setitem(PROVIDERS, name, _ProbeProvider)


class _SessionMgr:
    """最小会话管理器替身：无历史、无归档。"""

    def get_history(self, session_id, limit=50):
        return [{"role": "user", "content": "上次说的事", "nickname": "小明", "user_id": "20002"}]

    def get_session(self, session_id):
        return types.SimpleNamespace(task_id="t", data=types.SimpleNamespace(history=[]))


class _Module:
    def __init__(self):
        self.bot_id = 778
        self.config = _Cfg({
            "system_prompt": "人设",
            "meta_sender_style": "legacy",
            "history_rounds": 50,
        })
        self.memory = None

    def provider_chain(self):
        return [{"provider": "sched_probe", "model": "m", "modalities": ["text"]}]


class _Cfg(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)
