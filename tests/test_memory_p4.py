"""P4 隐式蒸馏测试：parse_facts / 提取调用 / 限频 / force 归档 / session 归档钩子。"""

import asyncio

from app.llm.config import DEFAULT_LLM_CONFIG
from app.llm.memory.extract import parse_facts, render_extract_prompt
from app.llm.memory.manager import MemoryManager
from app.llm.memory.store import owner_private
from app.llm.session import SessionManager


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

    def provider_chain(self):
        return [{"provider": "openai", "api_key": "k", "model": "m"}]


# ---- parse_facts ----
def test_parse_facts():
    text = (
        "0.9 用户喜欢喝美式咖啡\n"
        "0.5 用户无意中说了一句\n"
        "高重要度 无前缀行\n"
        "无\n"
        "1.0 |用户习惯早睡\n"
    )
    facts = parse_facts(text)
    contents = [f["content"] for f in facts]
    assert "用户喜欢喝美式咖啡" in contents
    assert "用户习惯早睡" in contents
    by_imp = {f["content"]: f["importance"] for f in facts}
    assert by_imp["用户喜欢喝美式咖啡"] == 0.9
    assert by_imp["用户习惯早睡"] == 1.0
    empty = parse_facts("无")
    assert empty == []


def test_render_extract_prompt_contains_lines():
    p = render_extract_prompt("5", ["我喜欢喝美式"], is_group=False)
    assert "我喜欢喝美式" in p
    assert "私聊用户" in p
    g = render_extract_prompt("5", ["我喜欢喝美式"], is_group=True)
    assert "群成员" in g


# ---- 提取调用（monkeypatch provider） ----
async def test_extract_facts_for_user_async(monkeypatch):
    class Resp:
        ok = True
        text = "0.9 用户喜欢喝美式咖啡\n0.4 用户闲聊一句"

    async def fake(chain, messages, **_kw):
        return Resp()

    import app.llm.memory.extract as em

    monkeypatch.setattr("app.llm.providers.chat_with_fallback", fake)

    rt = FakeRuntime()
    facts = await em.extract_facts_for_user_async(rt, "5", ["我喜欢喝美式"], is_group=False)
    assert len(facts) == 2
    assert facts[0]["content"] == "用户喜欢喝美式咖啡"


# ---- 限频 / 归档 force ----
async def test_consolidate_throttle_and_force(monkeypatch):
    calls = []

    async def fake(runtime, uid, texts, **kw):
        calls.append(uid)
        return [{"content": "我喜欢喝美式", "importance": 0.9}]

    monkeypatch.setattr("app.llm.memory.extract.extract_facts_for_user_async", fake)

    rt = FakeRuntime()
    mgr = rt.memory
    msgs = [{"role": "user", "content": "记住我喜欢喝美式", "user_id": "5"}]

    mgr.maybe_consolidate("private_5", False, msgs, source="chat")
    await asyncio.sleep(0.05)
    await asyncio.sleep(0.05)
    assert len(calls) == 1  # 首次已蒸馏
    assert mgr.store.count_by_owner(owner_private(5)) == 1

    # 限频：同一会话 interval 内再触发 → 不新增
    mgr.maybe_consolidate("private_5", False, msgs, source="chat")
    await asyncio.sleep(0.05)
    assert len(calls) == 1

    # force（归档）：跳过限频 → 再次蒸馏
    await mgr.consolidate_archived("private_5", False, msgs)
    await asyncio.sleep(0.05)
    await asyncio.sleep(0.05)
    assert len(calls) == 2


async def test_consolidate_ignores_empty_or_nouser(monkeypatch):
    calls = []

    async def fake(runtime, uid, texts, **kw):
        calls.append(uid)
        return [{"content": "不应出现", "importance": 0.9}]

    monkeypatch.setattr("app.llm.memory.extract.extract_facts_for_user_async", fake)

    rt = FakeRuntime()
    mgr = rt.memory
    # 无 user_id 的 user 消息、assistant 消息、空内容 → 都应被过滤
    msgs = [
        {"role": "user", "content": "没有id", "user_id": ""},
        {"role": "assistant", "content": "机器人说话"},
        {"role": "user", "content": "  ", "user_id": "9"},
    ]
    mgr.maybe_consolidate("private_5", False, msgs, source="chat")
    await asyncio.sleep(0.05)
    assert calls == []


async def test_extract_enabled_false_skips(monkeypatch):
    calls = []

    async def fake(runtime, uid, texts, **kw):
        calls.append(uid)

    monkeypatch.setattr("app.llm.memory.extract.extract_facts_for_user_async", fake)
    rt = FakeRuntime(data={"memory_extract_enable": False})
    rt.memory.maybe_consolidate(
        "private_5", False, [{"role": "user", "content": "x", "user_id": "5"}]
    )
    await asyncio.sleep(0.05)
    assert calls == []


# ---- session 归档钩子 ----
def test_session_manager_on_archive_called():
    sm = SessionManager("botmem")
    seen = []
    sm.on_archive = lambda s: seen.append(s.id)
    s = sm.create_session("private_9", "private", 60)
    sm.add_message("private_9", "user", "我喜欢喝美式", user_id="9")
    sm.destroy_session("private_9")
    assert "private_9" in seen
    sm.stop_cleanup()
