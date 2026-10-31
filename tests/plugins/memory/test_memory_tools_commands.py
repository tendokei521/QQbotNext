"""工具与命令契约测试（v3）：模型工具 / 用户命令 / 权限边界。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.plugins.llm_memory.src.commands import handle_memory_command
from app.plugins.llm_memory.src.manager import MemoryManager
from app.plugins.llm_memory.src.tool import build_memory_tools

BASE_CONFIG = {
    "memory_enable": True,
    "memory_private_enable": True,
    "memory_group_enable": True,
    "memory_save_deterministic": True,
    "memory_extract_enable": False,
    "memory_extract_interval_min": 10,
    "memory_max_per_owner": 200,
    "memory_min_confidence": 0.5,
    "memory_audit_enable": True,
    "memory_inject_hedge": True,
    "memory_profile_enable": True,
    "memory_profile_max_chars": 800,
    "memory_recall_mode": "keyword",
    "memory_recall_max": 8,
    "memory_recall_max_chars": 1200,
    "memory_on_reset": "suspend",
    "memory_upgrade_saved_only": True,
    "memory_user_cross_group": True,
}


class FakeModule:
    bot_id = "bot1"

    def __init__(self, config: dict | None = None) -> None:
        self.config = dict(BASE_CONFIG)
        self.config.update(config or {})
        self.ctx = SimpleNamespace(services=None)
        self._runtime = lambda: None

    async def llm_chat(self, messages, **kwargs):
        return SimpleNamespace(ok=False, text="")


def _manager(tmp_path, monkeypatch) -> MemoryManager:
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    return MemoryManager(FakeModule())


def _tools(manager) -> dict:
    runtime = SimpleNamespace(memory_service=manager)
    return {
        spec.name: spec
        for spec in build_memory_tools(runtime, "private_5", "5", True)
    }


def test_tools_save_recall_delete(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    tools = _tools(manager)

    saved = asyncio.run(tools["memory_save"].handler(None, {"content": "我喜欢猫"}))
    assert saved.startswith("success:")

    recalled = asyncio.run(tools["memory_recall"].handler(None, {"query": "猫"}))
    assert "我喜欢猫" in recalled

    deleted = asyncio.run(tools["memory_delete"].handler(None, {"query": "喜欢猫"}))
    assert deleted.startswith("success:")
    assert manager.store.list_by_owner("user_5") == []
    manager.stop()


def test_tools_correct_and_deny(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    tools = _tools(manager)

    asyncio.run(tools["memory_save"].handler(None, {"content": "我喜欢喝美式"}))
    corrected = asyncio.run(
        tools["memory_correct"].handler(
            None, {"old": "美式", "content": "我改喝拿铁了"}
        )
    )
    assert corrected.startswith("success:")
    contents = {row["content"] for row in manager.store.list_by_owner("user_5")}
    assert "我改喝拿铁了" in contents

    denied = asyncio.run(tools["memory_deny"].handler(None, {"query": "拿铁"}))
    assert denied.startswith("success:")
    assert manager.store.list_by_owner("user_5") == []  # 下架后不再注入
    assert any(
        row["status"] == "negative"
        for row in manager.store.list_by_owner("user_5", include_all=True)
    )
    manager.stop()


def test_group_public_requires_admin(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    tools = _tools(manager)  # 私聊会话 → group_public 不可用
    result = asyncio.run(
        tools["memory_save"].handler(None, {"content": "群规：周五不开会", "scope": "group_public"})
    )
    assert result.startswith("error:") and "群聊" in result

    # 群聊 + 无事件（主动/定时路径）放行；群聊 + 非管理员事件拒绝
    runtime = SimpleNamespace(memory_service=manager)
    group_tools = {
        spec.name: spec
        for spec in build_memory_tools(runtime, "group_9", "5", False)
    }
    denied = asyncio.run(
        group_tools["memory_save"].handler(
            SimpleNamespace(event=SimpleNamespace(is_admin=False)),
            {"content": "群规：周五不开会", "scope": "group_public"},
        )
    )
    assert denied.startswith("error:") and "管理员" in denied

    allowed = asyncio.run(
        group_tools["memory_save"].handler(
            SimpleNamespace(event=SimpleNamespace(is_admin=True)),
            {"content": "群规：周五不开会", "scope": "group_public"},
        )
    )
    assert allowed.startswith("success:")
    manager.stop()


def test_tools_report_unavailable_without_service(tmp_path, monkeypatch):
    tools = {
        spec.name: spec
        for spec in build_memory_tools(SimpleNamespace(), "private_5", "5", True)
    }
    result = asyncio.run(tools["memory_save"].handler(None, {"content": "随便"}))
    assert result.startswith("error:") and "不可用" in result


@pytest.mark.asyncio
async def test_commands_list_remember_forget_and_audit(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    module = SimpleNamespace(memory_manager=lambda: manager)
    sent: list[str] = []

    async def send(text: str) -> None:
        sent.append(text)

    await handle_memory_command(module, "private_5", "5", True, False, "remember 我叫小明", send)
    assert sent and "已记住" in sent[-1]

    sent.clear()
    await handle_memory_command(module, "private_5", "5", True, False, "list", send)
    assert sent and "我叫小明" in sent[-1]

    sent.clear()
    await handle_memory_command(module, "private_5", "5", True, False, "forget 小明", send)
    assert sent and "已删除" in sent[-1]

    # audit 仅管理员
    sent.clear()
    await handle_memory_command(module, "private_5", "5", True, False, "audit", send)
    assert sent and "权限不足" in sent[-1]
    sent.clear()
    await handle_memory_command(module, "private_5", "5", True, True, "audit", send)
    assert sent and "权限不足" not in sent[-1]
    manager.stop()
