"""P3 群聊隔离与审计测试：提及扩展 / 注入隔离 / 工具召回他人画像 / #llm memory audit。"""

from app.agent.config import DEFAULT_LLM_CONFIG
from app.plugins.llm_memory.src.commands import handle_memory_command
from app.plugins.llm_memory.src.manager import MemoryManager
from app.plugins.llm_memory.src.store import owner_group, owner_group_member


class FakeConfig:
    def __init__(self, data=None):
        self.data = dict(DEFAULT_LLM_CONFIG)
        self.data.update({"memory_enable": True, "experimental_long_term_memory": True})
        if data:
            self.data.update(data)

    def get(self, key, default=None):
        if key in self.data and self.data[key] is not None:
            return self.data[key]
        return default


class FakeRuntime:
    def __init__(self, data=None, bot_id="botmem"):
        self.bot_id = bot_id
        self.config = FakeConfig(data)
        self.memory = MemoryManager(self)


class FakeBot:
    def __init__(self, members=None):
        self.members = members or []

    async def get_group_member_list(self, group_id):
        return {"data": self.members}


def _m(uid, card, nickname):
    return {"user_id": uid, "card": card, "nickname": nickname}


async def test_tool_recall_finds_mentioned_member_profile():
    from app.plugins.llm_memory.src.tool import build_memory_tools

    rt = FakeRuntime()
    bot = FakeBot(members=[_m(2, "小明", "小明")])
    mgr = rt.memory
    mgr.store.upsert_fact("小明喜欢喝美式", owner_group_member("9", 2), source="manual")
    tools = build_memory_tools(rt, "group_9", "1", False)
    spec = next(t for t in tools if t.name == "memory_recall")
    # 无 bot（ctx=None）→ 不提及其他成员
    out_none = await spec.handler(None, {"query": "小明"})
    assert "未找到" in out_none
    # 有 bot（ctx.bot）→ 提及扩展命中
    from types import SimpleNamespace

    out = await spec.handler(SimpleNamespace(bot=bot), {"query": "小明喜欢什么"})
    assert "小明喜欢喝美式" in out


async def test_group_public_tool_scope():
    from app.plugins.llm_memory.src.tool import build_memory_tools

    rt = FakeRuntime()
    tools = build_memory_tools(rt, "group_9", "1", False)
    spec = next(t for t in tools if t.name == "memory_save")
    out = await spec.handler(None, {"content": "全体禁烟", "scope": "group_public"})
    assert "success" in out
    assert rt.memory.store.count_by_owner(owner_group("9")) == 1


async def _dispatch(rt, session_id, user_id, action, is_admin=True):
    sent = []

    async def send(t):
        sent.append(t)

    await handle_memory_command(rt, session_id, user_id, is_admin, False, action, send)
    return "\n".join(sent)


async def test_audit_command_admin_only():
    rt = FakeRuntime()
    mgr = rt.memory
    mgr.save_fact("我住上海", "user_5", source="manual", source_user="5")
    mgr.visible_recall("private_5", "5", query="上海", audit=True)
    mgr.delete_own("private_5", "5", "上海")

    ok = await _dispatch(rt, "private_5", "5", "memory audit", is_admin=True)
    assert "write" in ok and "read" in ok and "forget" in ok
    deny = await _dispatch(rt, "private_5", "5", "memory audit", is_admin=False)
    assert "权限不足" in deny


async def test_inject_audit_when_enabled():
    rt = FakeRuntime(data={"memory_audit_inject": True})
    mgr = rt.memory
    mgr.store.upsert_fact("我住上海", "user_5")
    mgr.recall_block("private_5", user_id="5", audit_inject=True)
    actions = [r["action"] for r in mgr.store.recent_audit(owner="user_5")]
    assert "inject" in actions
