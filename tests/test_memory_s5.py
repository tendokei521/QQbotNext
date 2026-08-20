"""S5 纠错闭环测试：命令 correct/deny/confirm/list --all + 工具 memory_correct/memory_deny。"""

from app.llm.config import DEFAULT_LLM_CONFIG
from app.llm.memory.commands import handle_memory_command
from app.llm.memory.manager import MemoryManager
from app.llm.memory.store import owner_private
from app.llm.memory.tool import build_memory_tools


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
    def __init__(self, bot_id="bots5"):
        self.bot_id = bot_id
        self.config = FakeConfig()
        self.memory = MemoryManager(self)


async def _dispatch(rt, session_id, user_id, action, is_admin=True, is_private=True):
    sent = []

    async def send(t):
        sent.append(t)

    await handle_memory_command(rt, session_id, user_id, is_admin, is_private, action, send)
    return "\n".join(sent)


async def test_command_deny_hides_from_injection():
    rt = FakeRuntime(bot_id="bots5a")
    mgr = rt.memory
    mgr.store.upsert_fact("我提过喜欢美式", owner_private(5), confidence=0.8)
    out = await _dispatch(rt, "private_5", "5", "memory deny 美式", is_private=True)
    assert "已完成下架" in out or "已下架" in out
    block = mgr.recall_block("private_5", user_id="5")
    assert "喜欢美式" not in block
    # --all 可见 negative
    out2 = await _dispatch(rt, "private_5", "5", "memory list --all", is_private=True)
    assert "negative" in out2


async def test_command_correct_replaces():
    rt = FakeRuntime(bot_id="bots5b")
    mgr = rt.memory
    mgr.store.upsert_fact("我喜欢喝美式", owner_private(5), confidence=0.8)
    out = await _dispatch(rt, "private_5", "5", "memory correct 美式 我喝不惯美式",
                          is_private=True)
    assert "已纠正" in out
    active = mgr.store.list_by_owner(owner_private(5))
    assert len(active) == 1
    assert active[0]["content"] == "我喝不惯美式"

    # 注入只含新说法
    block = mgr.recall_block("private_5", user_id="5")
    assert "喝不惯美式" in block
    assert "我喜欢喝美式" not in block


async def test_command_confirm_raises_confidence():
    rt = FakeRuntime(bot_id="bots5c")
    mgr = rt.memory
    mid = mgr.store.upsert_fact("我喜欢拿铁", owner_private(5), confidence=0.55)
    out = await _dispatch(rt, "private_5", "5", "memory confirm 拿铁", is_private=True)
    assert "已确认" in out
    row = mgr.store.get(mid)
    assert row["confirmed"] == 1
    assert row["confidence"] > 0.55


async def test_tools_correct_and_deny():
    rt = FakeRuntime(bot_id="bots5d")
    mgr = rt.memory
    mgr.store.upsert_fact("我喜欢喝美式", owner_private(5), confidence=0.8)
    tools = build_memory_tools(rt, "private_5", "5", True)
    names = {t.name for t in tools}
    assert {"memory_correct", "memory_deny"} <= names

    corr = next(t for t in tools if t.name == "memory_correct")
    out = await corr.handler(None, {"old": "美式", "content": "我喝不惯美式"})
    assert "已纠正" in out
    assert mgr.store.count_active_by_owner(owner_private(5)) == 1

    deny = next(t for t in tools if t.name == "memory_deny")
    out2 = await deny.handler(None, {"query": "喝不惯"})
    assert "下架" in out2
    assert mgr.store.count_active_by_owner(owner_private(5)) == 0
