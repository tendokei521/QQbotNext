"""`#llm help` 指令表测试。

覆盖三件事：

1. 前缀解析（``is_command`` / ``parse_action`` / ``is_help_action``）；
2. 帮助内容与降级路径（合并转发节点、纯文本分块）；
3. **防漂移**：``COMMAND_GROUPS`` 里列的每条指令都必须被 ``handle_commands`` 真正处理
   （否则只回「未知指令」），新增指令时表与实现两处必须同时到位。
"""

from __future__ import annotations

import types

from app.domain.events import MessageSegment
from app.llm import chat
from app.llm.commands import (
    COMMAND_GROUPS,
    PREFIX,
    build_help_nodes,
    build_help_text,
    chunk_text,
    is_command,
    is_help_action,
    iter_commands,
    parse_action,
)


# ---------- 桩件 ----------


class _Cfg(dict):
    """最小配置：记录 set_session / clear_session（chat.handle 用）。"""

    def set_session(self, session_id):
        self["_session"] = session_id

    def clear_session(self):
        self.pop("_session", None)


class _History:
    def save_session(self, *a, **k):
        pass

    def load_history(self, task_id):
        return {}

    def export_text(self, task_id):
        return ""


class _SessionMgr:
    """只覆盖 handle_commands 用到的会话接口。"""

    def __init__(self, *a, **k):
        self.history = _History()

    def get_session(self, session_id):
        return None

    def create_session(self, *a, **k):
        return None

    def destroy_session(self, session_id):
        pass

    def add_message(self, *a, **k):
        pass

    def new_conversation(self, session_id, title=""):
        return {"title": title or "新对话", "task_id": "task-1"}


class _Bot:
    """记录发出的消息；可模拟「不支持合并转发」的两种失败方式。"""

    def __init__(self, *, forward_ok: bool = True, forward_raises: bool = False):
        self.texts: list[str] = []
        self.forwards: list[list] = []
        self._forward_ok = forward_ok
        self._forward_raises = forward_raises

    async def send_private_msg(self, user_id, message):
        self.texts.append(message)
        return {"status": "ok"}

    async def send_group_msg(self, group_id, message):
        self.texts.append(message)
        return {"status": "ok"}

    async def send_forward_msg(self, group_id=0, user_id=0, msgdata=None):
        if self._forward_raises:
            raise RuntimeError("此实现不支持合并转发")
        self.forwards.append(list(msgdata or []))
        if self._forward_ok:
            return {"status": "ok"}
        return {"status": "failed", "message": "unsupported action"}


class _Module:
    bot_id = 778
    name = "agent"

    def __init__(self, *, api_key: str = "sk-test", memory=None, scheduler=None, proactive=None):
        self.config = _Cfg({"private_enable": True, "group_enable": True})
        self._api_key = api_key
        self.memory = memory
        self.scheduler = scheduler
        self.proactive = proactive

    def provider_config(self):
        return {"api_key": self._api_key}


class _PipelineRuntime:
    bot_id = 778

    def __init__(self):
        self.config = _Cfg({"private_enable": True, "group_enable": True})


class _Memory:
    """最小记忆桩：只让 `#llm memory audit` 走到权限判断那一步。"""

    def __init__(self):
        self.store = object()

    def enabled(self):
        return True

    def scope_owners(self, session_id, user_id):
        return []


def _event(text: str, *, message_type: str = "private", bot=None, is_admin: bool = False):
    payload = {
        "message_type": message_type,
        "user_id": 20002,
        "self_id": 778,
        "is_admin": is_admin,
        "message": [MessageSegment("text", {"text": text})],
        "bot": bot,
    }
    if message_type == "group":
        payload["group"] = types.SimpleNamespace(group_id=466052056)
    return types.SimpleNamespace(**payload)


async def _run_command(text, *, bot=None, module=None, message_type="private", is_admin=True):
    """直接调 handle_commands（跳过 chat.handle 的开关/密钥检查）。"""
    bot = bot or _Bot()
    is_private = message_type == "private"
    group_id = None if is_private else "466052056"
    session_id = "private_20002" if is_private else "group_466052056"
    handled = await chat.handle_commands(
        module or _Module(), _SessionMgr(), session_id, group_id, "20002",
        text, is_admin, is_private, _event(text, message_type=message_type, bot=bot),
    )
    return bot, handled


def _runnable(command: str) -> str:
    """把表里的指令变成可执行形式：去掉 <占位符> / [可选] 记号。"""
    return " ".join(t for t in command.split() if not t.startswith(("<", "[")))


# ---------- 前缀解析 ----------


def test_prefix_parsing():
    assert is_command("#llm help")
    assert is_command("#llm")
    assert is_command("  #llm task  ")
    assert not is_command("#llms help")
    assert not is_command("llm help")
    assert not is_command("你好")

    assert parse_action("#llm memory list") == "memory list"
    assert parse_action("#llm   task") == "task"
    assert parse_action("#llm") == ""
    assert parse_action("你好") is None

    assert is_help_action("") and is_help_action("help") and is_help_action("HELP")
    assert is_help_action("?") and is_help_action("--help")
    assert not is_help_action("task")


# ---------- 帮助内容 ----------


def test_help_nodes_structure_and_coverage():
    available = {"schedule": False, "proactive": True, "memory": False}
    nodes = build_help_nodes(uin=778, available=available)
    assert len(nodes) == len(COMMAND_GROUPS) + 1  # 说明节点 + 每个分组一个节点

    for node in nodes:
        assert node["type"] == "node"
        data = node["data"]
        assert data["name"] and data["uin"] == "778"
        assert data["content"][0]["type"] == "text"

    blob = "\n".join(n["data"]["content"][0]["data"]["text"] for n in nodes)
    for cmd, desc in iter_commands():
        assert cmd in blob, cmd
        assert desc in blob, desc

    names = [n["data"]["name"] for n in nodes]
    assert "定时任务（本 bot 未启用）" in names  # schedule=False 才标注
    assert "长期记忆（本 bot 未启用）" in names
    assert "主动消息" in names  # proactive=True 不标注


def test_help_nodes_without_availability_marks_nothing():
    names = [n["data"]["name"] for n in build_help_nodes(uin=778, available=None)]
    assert all("未启用" not in name for name in names)


def test_help_text_fallback_covers_every_command_and_chunks_without_cutting_lines():
    text = build_help_text(available={"schedule": False})
    assert "【定时任务（本 bot 未启用）】" in text
    for cmd, _desc in iter_commands():
        assert cmd in text

    limit = 200
    assert max(len(line) for line in text.splitlines()) <= limit  # 单行不超限，分块才安全
    chunks = chunk_text(text, limit)
    assert len(chunks) > 1
    assert all(len(chunk) <= limit for chunk in chunks)
    assert "\n".join(chunks).splitlines() == text.splitlines()  # 不丢行、不切行


# ---------- 发送行为 ----------


async def test_help_sends_merged_forward_in_private():
    bot, handled = await _run_command("#llm help")
    assert handled is True
    assert bot.texts == []  # 合并转发成功 → 不再发纯文本
    nodes = bot.forwards[0]
    assert nodes[0]["data"]["name"] == "LLM 指令表"
    assert any("#llm memory audit" in n["data"]["content"][0]["data"]["text"] for n in nodes)


async def test_bare_prefix_equals_help_in_group():
    bot, handled = await _run_command("#llm", message_type="group")
    assert handled is True
    assert bot.forwards and bot.texts == []


async def test_help_falls_back_to_text_when_forward_unsupported():
    for bot in (_Bot(forward_raises=True), _Bot(forward_ok=False)):
        bot, handled = await _run_command("#llm help", bot=bot)
        assert handled is True
        blob = "\n".join(bot.texts)
        assert blob, "降级必须真的发出去"
        assert "#llm memory audit" in blob
        assert all(text.startswith("# ") for text in bot.texts)  # 回复统一「# 」前缀


async def test_unknown_command_hints_help():
    bot, handled = await _run_command("#llm 不存在的动作")
    assert handled is True
    assert bot.forwards == []  # 未知指令不铺一整张表
    assert "未知指令" in bot.texts[0]
    assert "#llm help" in bot.texts[0]


# ---------- 入口与流水线接线 ----------


async def test_handle_help_works_without_api_key(monkeypatch):
    """`#llm help` 是静态表：没配密钥也要能看（其余指令仍要密钥）。"""
    monkeypatch.setattr(chat, "SessionManager", lambda *a, **k: _SessionMgr())
    bot = _Bot()
    await chat.handle(_Module(api_key=""), _event("#llm help", bot=bot))
    assert bot.forwards and bot.texts == []

    idle = _Bot()
    await chat.handle(_Module(api_key=""), _event("#llm task", bot=idle))
    assert idle.forwards == [] and idle.texts == []  # 非 help 指令静默返回


async def test_handle_respects_scene_switch(monkeypatch):
    """群聊未开启「群聊回复」时不响应指令（help 也不例外，与其它指令一致）。"""
    monkeypatch.setattr(chat, "SessionManager", lambda *a, **k: _SessionMgr())
    module = _Module()
    module.config["group_enable"] = False
    bot = _Bot()
    await chat.handle(module, _event("#llm help", message_type="group", bot=bot))
    assert bot.forwards == [] and bot.texts == []


async def test_pipeline_routes_llm_commands_without_at(monkeypatch):
    """流水线必须把 `#llm ...` 分流给 chat.handle（群聊里不需要 @）。"""
    from app.llm.context import LlmContext, LlmJob
    from app.llm.pipeline import LlmPipeline

    seen: list = []

    async def fake_handle(module, event):
        seen.append((module, event))

    monkeypatch.setattr("app.llm.chat.handle", fake_handle)

    runtime = _PipelineRuntime()
    pipeline = LlmPipeline(runtime)
    event = _event("#llm help", message_type="group", bot=_Bot())
    ctx = LlmContext(
        event=event, runtime=runtime, bot=event.bot,
        session_id="group_466052056", user_text="#llm help",
    )
    ctx.job = LlmJob(id="job1", group_key="group_466052056", ctx=ctx)

    await pipeline._run(ctx.job)

    assert [e for _m, e in seen] == [event]
    assert runtime.config.get("_session") is None  # 会话档案已清理


# ---------- 防漂移 ----------


async def test_every_listed_command_is_handled():
    """表里列的每条指令都必须被真正处理，不能落到「未知指令」。"""
    module = _Module()
    for command, _desc in iter_commands():
        text = _runnable(command)
        bot, handled = await _run_command(text, module=module)
        assert handled is True, command
        joined = "\n".join(bot.texts)
        assert "未知指令" not in joined, f"{command} -> {joined}"


async def test_listed_admin_commands_are_gated():
    """表里标注「管理员」的指令在非管理员下必须被拒。"""
    admin_only = [cmd for cmd, desc in iter_commands() if "管理员" in desc]
    assert admin_only, "指令表里应至少有一条管理员指令"
    module = _Module(memory=_Memory())
    for command in admin_only:
        bot, _handled = await _run_command(_runnable(command), module=module, is_admin=False)
        assert "权限不足" in "\n".join(bot.texts), command
