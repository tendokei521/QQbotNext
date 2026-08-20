"""S3 蒸馏“复述而非捏造”测试：解析/信度词→置信度/推断词丢弃/入库 confidence。"""

import asyncio

from app.llm.config import DEFAULT_LLM_CONFIG
from app.llm.memory.extract import parse_facts, render_extract_prompt
from app.llm.memory.manager import MemoryManager
from app.llm.memory.store import owner_private


class FakeConfig:
    def __init__(self, data=None):
        self.data = dict(DEFAULT_LLM_CONFIG)
        if data:
            self.data.update(data)

    def get(self, key, default=None):
        if key in self.data and self.data[key] is not None:
            return self.data[key]
        return default


class FakeRuntime:
    def __init__(self, bot_id="bots3", data=None):
        self.bot_id = bot_id
        self.config = FakeConfig(data)
        self.memory = MemoryManager(self)

    def provider_chain(self):
        return [{"provider": "openai", "api_key": "k", "model": "m"}]


def test_render_extract_prompt():
    p = render_extract_prompt("5", ["我喜欢喝美式"], is_group=False)
    assert "我喜欢喝美式" in p
    assert "禁止把推断" in p  # v2 加了禁推断规则
    g = render_extract_prompt("5", ["x"], is_group=True)
    assert "群成员" in g


def test_parse_facts_tags_to_confidence():
    text = (
        "0.9 [很确定] 用户说过喜欢喝美式咖啡\n"
        "0.6 [好像] 用户提到过想养一只猫\n"
        "0.5 [不确定] 用户说过喜欢苏打水\n"
        "0.8 用户习惯早睡\n"
    )
    facts = parse_facts(text)
    by = {f["content"]: f for f in facts}
    assert by["用户说过喜欢喝美式咖啡"]["confidence"] == 0.65
    assert by["用户提到过想养一只猫"]["confidence"] == 0.55
    assert by["用户说过喜欢苏打水"]["confidence"] == 0.45
    assert by["用户习惯早睡"]["confidence"] == 0.55  # 无语气默认
    assert by["用户说过喜欢喝美式咖啡"]["importance"] == 0.9


def test_parse_facts_drops_inference():
    text = (
        "0.8 用户看起来想换工作\n"
        "0.8 用户应该会不会来开会\n"
        "0.7 用户说过喜欢喝美式\n"
    )
    facts = parse_facts(text)
    contents = [f["content"] for f in facts]
    assert "用户说过喜欢喝美式" in contents
    assert all("看起来" not in c and "应该会" not in c for c in contents)
    assert parse_facts("无") == []


async def test_extract_facts_for_user_async_keeps_confidence(monkeypatch):
    class Resp:
        ok = True
        text = "0.9 [很确定] 用户说过喜欢喝美式\n0.4 [好像] 用户提到过想养猫"

    async def fake(chain, messages, **_kw):
        return Resp()

    import app.llm.memory.extract as em

    monkeypatch.setattr("app.llm.providers.chat_with_fallback", fake)
    rt = FakeRuntime()
    facts = await em.extract_facts_for_user_async(rt, "5", ["我喜欢喝美式"], is_group=False)
    by = {f["content"]: f["confidence"] for f in facts}
    assert by["用户说过喜欢喝美式"] == 0.65
    assert by["用户提到过想养猫"] == 0.55


async def test_distill_saves_confidence_into_store(monkeypatch):
    async def fake(runtime, uid, texts, **kw):
        return [{"content": "用户说过喜欢喝美式", "importance": 0.9, "confidence": 0.65}]

    monkeypatch.setattr("app.llm.memory.extract.extract_facts_for_user_async", fake)
    rt = FakeRuntime(bot_id="bots3a")
    rt.memory.maybe_consolidate(
        "private_5", False,
        [{"role": "user", "content": "记住我喜欢喝美式", "user_id": "5"}],
        source="chat",
    )
    await asyncio.sleep(0.05)
    await asyncio.sleep(0.05)
    rows = rt.memory.store.list_by_owner(owner_private(5))
    assert len(rows) == 1
    row = rows[0]
    assert row["confidence"] == 0.65
    assert row["source"] == "extract"
    assert row["status"] == "active"
