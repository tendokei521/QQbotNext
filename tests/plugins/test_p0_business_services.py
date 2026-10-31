"""P0 回归测试：证明"记忆/知识库工具与指令、主动消息记忆召回"确实恢复了。

这些用例**故意不给 runtime 挂 `memory` / `knowledge` 字段**（模拟真实的 v3 运行时），
只挂模块自己的 `memory_service` / `knowledge_service` —— 修复前它们全部失效，
修复后必须工作。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.plugins.llm_knowledge.src.tool import build_knowledge_tools
from app.plugins.llm_memory.src.tool import build_memory_tools


class _FakeMemory:
    """最小记忆管理器：只实现 `tool.py` 真正调用的方法（鸭子类型）。"""

    def __init__(self):
        self.saved: list[dict] = []
        self.enabled_flag = True

    # --- 配置读取（工具用它判断跨群记忆等开关）---
    def _get(self, key, default=None):
        return {"memory_user_cross_group": True}.get(key, default)

    def enabled(self) -> bool:
        return self.enabled_flag

    # --- 指令层要用的 store ---
    @property
    def store(self):
        def _empty(*a, **k):
            return []

        return SimpleNamespace(
            list_for_owners=_empty, list=_empty, count=_empty,
        )

    # --- 指令层 ---
    def scope_owners(self, session_id, user_id):
        return [f"private_{user_id}"]

    def stats(self):
        return {"total": 0}

    # --- 归属 ---
    def own_owner(self, session_id, user_id):
        return f"private_{user_id}" if str(session_id).startswith("private_") else session_id

    # --- 写入（工具实际调用 `save_fact`）---
    def save_fact(self, content, owner, **kwargs):
        self.saved.append({"content": content, "owner": owner, **kwargs})
        return "m1"

    def save(self, **kwargs):
        self.saved.append(kwargs)
        return "m1", "ok"

    async def save_async(self, **kwargs):
        return self.save(**kwargs)

    # --- 召回 ---
    def visible_recall(self, *a, **k):
        return "【长期记忆】用户喜欢猫"

    async def visible_recall_async(self, *a, **k):
        return self.visible_recall(*a, **k)

    def recall(self, *a, **k):
        return [{"id": "m1", "content": "用户喜欢猫", "score": 0.9}]

    # --- 增删改 ---
    def get(self, memory_id):
        return {"id": memory_id, "content": "用户喜欢猫", "owner": "private_5"}

    def delete_by_id(self, memory_id):
        return True

    def mark_denied(self, memory_id, reason=""):
        return True

    def set_content(self, memory_id, content):
        return True


class _FakeKnowledge:
    def enabled(self) -> bool:
        return True

    async def add_text(self, content, title="", source=""):
        return "c1", "ok"

    def search(self, *a, **k):
        return [{"id": "c1", "title": "文档", "text": "内容"}]

    def delete(self, cid):
        return True


def _runtime(**extra):
    """真实 v3 运行时形状：**没有** memory / knowledge 字段。"""
    base = {"bot_id": "12345", "config": {}}
    base.update(extra)
    return SimpleNamespace(**base)


# ---------- 记忆工具 ----------


def test_memory_tools_resolve_the_module_service_handle():
    """修复前：`runtime.memory` 不存在 → 工具全返回"记忆服务不可用"。"""
    memory = _FakeMemory()
    rt = _runtime(memory_service=memory)

    names = {t.name for t in build_memory_tools(rt, "private_5", "5", True)}
    assert {"memory_save", "memory_recall"} <= names


@pytest.mark.asyncio
async def test_memory_save_tool_actually_writes():
    memory = _FakeMemory()
    rt = _runtime(memory_service=memory)
    tools = {t.name: t for t in build_memory_tools(rt, "private_5", "5", True)}

    result = await tools["memory_save"].handler(None, {"content": "用户喜欢猫"})

    assert "error" not in result.lower()
    assert memory.saved, "记忆管理器必须真的收到写入"


@pytest.mark.asyncio
async def test_memory_recall_tool_returns_content():
    memory = _FakeMemory()
    rt = _runtime(memory_service=memory)
    tools = {t.name: t for t in build_memory_tools(rt, "private_5", "5", True)}

    result = await tools["memory_recall"].handler(None, {"query": "猫"})

    assert "猫" in result


@pytest.mark.asyncio
async def test_memory_tools_report_unavailable_without_any_handle():
    """没装模块时应当明确报「不可用」，而不是假装成功或抛异常。"""
    rt = _runtime()
    tools = {t.name: t for t in build_memory_tools(rt, "private_5", "5", True)}
    result = await tools["memory_save"].handler(None, {"content": "随便"})
    assert "error" in result.lower() and "不可用" in result


# ---------- 知识库工具 ----------


def test_knowledge_tools_resolve_the_module_service_handle():
    rt = _runtime(knowledge_service=_FakeKnowledge())
    names = {t.name for t in build_knowledge_tools(rt)}
    assert names, "知识库工具必须能构建出来"


@pytest.mark.asyncio
async def test_knowledge_add_tool_actually_writes():
    kb = _FakeKnowledge()
    rt = _runtime(knowledge_service=kb)
    tools = {t.name: t for t in build_knowledge_tools(rt)}
    name = next(n for n in tools if "add" in n)

    result = await tools[name].handler(None, {"content": "量子计算入门", "title": "笔记"})

    assert "error" not in result.lower()


# ---------- 主动消息 / 定时任务路径的记忆召回 ----------


@pytest.mark.asyncio
async def test_collect_history_materials_uses_the_memory_service_handle():
    """`assembly/inputs.py` 过去读 `runtime.memory`（已删）→ 主动消息/定时任务永不召回记忆。"""
    from app.agent.assembly.inputs import collect_history_materials

    calls: list[str] = []

    class _Mem:
        def enabled(self):
            return True

        async def recall_block_async(self, session_id, user_id, query, bot=None):
            calls.append(session_id)
            return "【长期记忆】用户喜欢猫"

    rt = _runtime(memory_service=_Mem())
    materials = await collect_history_materials(
        runtime=rt,
        session_mgr=None,
        session_id="private_5",
        is_private=True,
        config={},
        query_text="我上次说喜欢什么",
        user_id="5",
        bot=None,
    )

    assert calls, "必须真的去召回"
    assert "猫" in (materials.get("memory_text") or "")

# ---------- `#llm memory` 指令 ----------


@pytest.mark.asyncio
async def test_memory_command_uses_the_module_manager():
    """修复前：`handle_memory_command` 读 `module.memory`（模块没有该属性）→ 指令永远"未启用"。"""
    from app.plugins.llm_memory.src.commands import handle_memory_command

    memory = _FakeMemory()
    module = SimpleNamespace(memory_manager=lambda: memory)
    sent: list[str] = []

    async def _send(text: str) -> None:
        sent.append(text)

    handled = await handle_memory_command(
        module, "private_5", "5", True, True, "memory list", _send
    )

    assert handled is True
    assert sent, "指令必须真的回一句话"
    assert "未启用" not in sent[0], f"不该报未启用：{sent[0]}"


@pytest.mark.asyncio
async def test_memory_command_reports_unavailable_without_module():
    from app.plugins.llm_memory.src.commands import handle_memory_command

    sent: list[str] = []

    async def _send(text: str) -> None:
        sent.append(text)

    await handle_memory_command(
        SimpleNamespace(), "private_5", "5", True, True, "memory list", _send
    )
    assert "未启用" in sent[0]
