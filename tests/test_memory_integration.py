"""P5 集成测试：generate_response 端到端走通 记忆写入→注入。

- 私聊：「记住我喜欢喝美式」→ 确定性兜底入库 → 下一轮「我喜欢什么」记忆块注入提示词；
- 群聊：小明说过的事实 → 提问「小明喜欢什么」（提及扩展）→ 记忆块带出小明画像；
- 隔离：他人的记忆不注入给提问者。
"""

import asyncio
import types

from app.llm.chat import generate_response
from app.llm.config import DEFAULT_LLM_CONFIG
from app.llm.memory.manager import MemoryManager
from app.llm.session import SessionManager


class FakeConfig:
    def __init__(self, data=None):
        self.data = dict(DEFAULT_LLM_CONFIG)
        if data:
            self.data.update(data)

    def get(self, key, default=None):
        if key in self.data and self.data[key] is not None:
            return self.data[key]
        return default


class _Tools:
    def enabled_specs(self):
        return []


class _Skills:
    def prompt_blocks(self):
        return []


class _Bot:
    def __init__(self, members):
        self.members = members
        self.got = []

    async def get_group_member_list(self, group_id):
        self.got.append(group_id)
        return {"data": self.members}


def _ctx(text):
    return types.SimpleNamespace(session_id="", user_text=text, state={})


def _ev_private(uid):
    return types.SimpleNamespace(
        message_type="private", user_id=uid, group=None, bot=None, self_id=uid
    )


def _ev_group(gid, uid, bot):
    return types.SimpleNamespace(
        message_type="group", user_id=uid,
        group=types.SimpleNamespace(group_id=gid),
        bot=bot, self_id="9",
    )


def _last_chat_call(captured, user_text):
    """定位真实对话请求（排除后台蒸馏请求：其 user 内容是提取器提示词）。"""
    for msgs in reversed(captured):
        for m in msgs:
            if m.get("role") == "user" and m.get("content", "").strip() == user_text:
                return msgs
    return captured[-1]


def _make_runtime(bot_id="bot_e2e", bot=None, data=None):
    rt = types.SimpleNamespace()
    rt.bot_id = bot_id
    rt.config = FakeConfig(data)
    rt.memory = MemoryManager(rt)
    rt.llm_tools = _Tools()
    rt.skills = _Skills()
    rt.scheduler = None
    rt.proactive = None
    rt._bot = bot

    def provider_config():
        return {"api_key": "k"}

    def provider_chain():
        return [{"provider": "openai", "api_key": "k", "model": "m"}]

    rt.provider_config = provider_config
    rt.provider_chain = provider_chain
    return rt


async def test_private_autosave_then_inject(monkeypatch):
    captured = []

    async def fake_chat(chain, messages, **_kw):
        captured.append(messages)
        from app.llm.providers.base import LLMResponse

        contents = [m.get("content", "") for m in messages]
        if any("记忆提取器" in (c or "") for c in contents):
            return LLMResponse(text="无")
        return LLMResponse(text="收到，我记住了～")

    monkeypatch.setattr("app.llm.chat.chat_with_fallback", fake_chat)
    monkeypatch.setattr("app.llm.providers.chat_with_fallback", fake_chat)

    rt = _make_runtime(bot_id="bot_e2e_a")
    await generate_response(rt, _ev_private("5"), _ctx("记住我喜欢喝美式"))
    await asyncio.sleep(0.05)

    # 确定性兜底已入库
    from app.llm.memory.store import owner_private

    assert rt.memory.store.count_by_owner(owner_private(5)) == 1

    await generate_response(rt, _ev_private("5"), _ctx("我喜欢什么"))
    await asyncio.sleep(0.05)

    # 第二轮 messages 里应带长期记忆块
    chat_call = _last_chat_call(captured, "我喜欢什么")
    contents = [m.get("content", "") for m in chat_call]
    assert any(("长期记忆" in c) and ("我喜欢喝美式" in c) for c in contents)
    rt.memory.stop()


async def test_group_mention_injects_member_profile(monkeypatch):
    captured = []

    async def fake_chat(chain, messages, **_kw):
        captured.append(messages)
        from app.llm.providers.base import LLMResponse

        contents = [m.get("content", "") for m in messages]
        if any("记忆提取器" in (c or "") for c in contents):
            return LLMResponse(text="无")
        return LLMResponse(text="他说了他喜欢美式。")

    monkeypatch.setattr("app.llm.chat.chat_with_fallback", fake_chat)
    monkeypatch.setattr("app.llm.providers.chat_with_fallback", fake_chat)

    bot = _Bot([{"user_id": 2, "card": "小明", "nickname": "小明"}])
    rt = _make_runtime(bot_id="bot_e2e_b", bot=bot, data={"group_enable": True})
    from app.llm.memory.store import owner_group_member

    rt.memory.store.upsert_fact(
        "小明喜欢喝美式咖啡", owner_group_member("9", 2), source="manual"
    )

    await generate_response(rt, _ev_group("9", "1", bot), _ctx("小明喜欢什么"))
    await asyncio.sleep(0.05)

    chat_call = _last_chat_call(captured, "小明喜欢什么")
    contents = [m.get("content", "") for m in chat_call]
    assert any("小明喜欢喝美式咖啡" in c for c in contents)
    rt.memory.stop()


async def test_group_not_inject_others_profile_without_mention(monkeypatch):
    captured = []

    async def fake_chat(chain, messages, **_kw):
        captured.append(messages)
        from app.llm.providers.base import LLMResponse

        contents = [m.get("content", "") for m in messages]
        if any("记忆提取器" in (c or "") for c in contents):
            return LLMResponse(text="无")
        return LLMResponse(text="好。")

    monkeypatch.setattr("app.llm.chat.chat_with_fallback", fake_chat)
    monkeypatch.setattr("app.llm.providers.chat_with_fallback", fake_chat)

    # 有成员 小明/小红，但提问没提任何人
    bot = _Bot([{"user_id": 2, "card": "小明", "nickname": "小明"},
                {"user_id": 3, "card": "小红", "nickname": "小红"}])
    rt = _make_runtime(bot_id="bot_e2e_c", bot=bot, data={"group_enable": True})
    from app.llm.memory.store import owner_group_member

    rt.memory.store.upsert_fact("小明喜欢喝美式咖啡", owner_group_member("9", 2), source="manual")
    rt.memory.store.upsert_fact("小红讨厌香菜", owner_group_member("9", 3), source="manual")

    await generate_response(rt, _ev_group("9", "3", bot), _ctx("最近在忙什么"))
    await asyncio.sleep(0.05)

    chat_call = _last_chat_call(captured, "最近在忙什么")
    contents = [m.get("content", "") for m in chat_call]
    # 提问者(小红, user 3)自己的画像可见；但未提及的他人(小明, user 2)画像不得注入
    assert not any("小明喜欢" in c for c in contents)
    rt.memory.stop()
