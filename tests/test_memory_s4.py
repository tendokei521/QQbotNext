"""S4 记忆重置（休眠模块级）测试：suspend/clear/keep、#chat memory reset 命令。"""

import time

from app.llm.config import DEFAULT_LLM_CONFIG
from app.llm.memory.commands import handle_memory_command
from app.llm.memory.manager import MemoryManager
from app.llm.memory.store import owner_private


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
    def __init__(self, bot_id="bots4", data=None):
        self.bot_id = bot_id
        self.config = FakeConfig(data)
        self.memory = MemoryManager(self)

    def provider_chain(self):
        return [{"provider": "openai", "api_key": "k", "model": "m"}]


def _seed(mgr, owner):
    auto = mgr.store.upsert_fact("旧话题自动记忆", owner, confidence=0.55, source="extract")
    saved = mgr.store.upsert_fact("我叫小明", owner, confidence=0.8, source="deterministic")
    # 回拨到 1 小时前：模拟“重置前的旧记忆”（且未超龄）
    mgr.store._execute(
        "UPDATE memories SET updated_at=? WHERE id IN (?, ?)",
        (int(time.time()) - 3600, auto, saved),
    )
    return auto, saved


def test_reset_suspend_hides_auto_keeps_saved():
    mgr = FakeRuntime(bot_id="bots4a").memory
    owner = owner_private(5)
    auto, saved = _seed(mgr, owner)
    msg = mgr.on_session_reset("private_5", user_id="5")
    assert "suspend" in msg or "挂起" in msg
    assert mgr.store.get_reset(owner) > 0
    block = mgr.recall_block("private_5", user_id="5")
    # 自动型挂起、保存型保留
    assert "旧话题自动记忆" not in block
    assert "我叫小明" in block
    _ = auto
    _ = saved


def test_reset_clear_removes():
    mgr = FakeRuntime(bot_id="bots4b", data={"memory_on_reset": "clear"}).memory
    owner = owner_private(5)
    _seed(mgr, owner)
    msg = mgr.on_session_reset("private_5", user_id="5")
    assert "清除" in msg
    assert mgr.store.count_by_owner(owner) == 0


def test_reset_keep_noop():
    mgr = FakeRuntime(bot_id="bots4c", data={"memory_on_reset": "keep"}).memory
    owner = owner_private(5)
    _seed(mgr, owner)
    msg = mgr.on_session_reset("private_5", user_id="5")
    assert "keep" in msg
    assert mgr.store.count_by_owner(owner) == 2
    assert mgr.store.get_reset(owner) == 0


async def test_command_reset_suspend_and_hard():
    rt = FakeRuntime(bot_id="bots4e")
    owner = owner_private(5)
    _seed(rt.memory, owner)
    sent = []

    async def send(t):
        sent.append(t)

    await handle_memory_command(rt, "private_5", "5", True, True, "memory reset", send)
    assert rt.memory.store.get_reset(owner) > 0
    assert rt.memory.store.count_by_owner(owner) == 2  # suspend 不清数据
    sent.clear()
    await handle_memory_command(rt, "private_5", "5", True, True, "memory reset hard", send)
    assert rt.memory.store.count_by_owner(owner) == 0  # hard 物理清除
