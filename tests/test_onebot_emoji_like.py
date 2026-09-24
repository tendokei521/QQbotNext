"""表情回应工具测试：语义解析、贴前先读、幂等、记账、错误可纠正。"""

from __future__ import annotations

from types import SimpleNamespace

from app.llm import emoji_lexicon
from app.llm.group_log.store import GroupLogStore
from app.llm.onebot_tools.manifest import ONEBOT_TOOLS
from app.llm.onebot_tools.tools import _handler
from app.llm.tool import ToolContext

ACTION = "set_msg_emoji_like"
GROUP_SCOPE = "group:1001"


class FakeBot:
    def __init__(self, status: str = "ok") -> None:
        self.calls: list[tuple[str, dict]] = []
        self.status = status

    async def call_api(self, action: str, params: dict | None = None):
        self.calls.append((action, dict(params or {})))
        if self.status != "ok":
            return {"status": "failed", "retcode": 1, "message": "boom"}
        return {"status": "ok", "data": {}}


def _tool_def() -> dict:
    return next(t for t in ONEBOT_TOOLS if t["name"] == ACTION)


def _ctx(bot: FakeBot, store: GroupLogStore | None = None, *, message_id: int = 777,
         trigger: str = "", group_id: int = 1001) -> ToolContext:
    runtime = SimpleNamespace(bot_id=1, config={})
    if store is not None:
        runtime.group_log = store
    event = SimpleNamespace(
        event_type="message_group",
        message_id=message_id,
        group_id=group_id,
        group=SimpleNamespace(group_id=group_id),
        user_id=20002,
    )
    return ToolContext(runtime=runtime, bot=bot, session_id=f"group_{group_id}",
                       event=event, user_id=20002, group_id=group_id,
                       extra={"trigger_message_id": trigger or str(message_id)})


async def _call(bot: FakeBot, args: dict, store: GroupLogStore | None = None, **kw):
    ctx = _ctx(bot, store, **kw)
    return await _handler(ctx.runtime, _tool_def(), ctx, args), ctx


# ==================== 词表 ====================


def test_lexicon_resolves_tag_and_explicit_id():
    assert emoji_lexicon.resolve("赞")[0] == emoji_lexicon.DEFAULT_TAGS["赞"]
    assert emoji_lexicon.resolve("66") == ("66", "explicit")
    assert emoji_lexicon.resolve("点个赞！")[0] == emoji_lexicon.DEFAULT_TAGS["赞"]


def test_lexicon_refuses_unknown_tag():
    assert emoji_lexicon.resolve("这个不存在的标签") == ("", "")
    assert emoji_lexicon.resolve("😀") == ("", "")


def test_lexicon_marks_observed_source():
    """本群真的用过的 id → 来源标注为 observed（比静态表更可信）。"""
    tag_id = emoji_lexicon.DEFAULT_TAGS["赞"]
    assert emoji_lexicon.resolve("赞", observed=[tag_id]) == (tag_id, "observed")
    assert emoji_lexicon.resolve("赞", observed=["999"]) == (tag_id, "table")


def test_valid_emoji_id_shape():
    assert emoji_lexicon.is_valid_emoji_id("66")
    assert emoji_lexicon.is_valid_emoji_id("128077")
    assert not emoji_lexicon.is_valid_emoji_id("赞")
    assert not emoji_lexicon.is_valid_emoji_id("")
    assert not emoji_lexicon.is_valid_emoji_id("-1;rm -rf")


# ==================== 工具声明 ====================


def test_manifest_declares_semantic_schema():
    tool = _tool_def()
    props = tool["parameters"]["properties"]
    assert "reaction" in props and "emoji_id" in props and "message_id" in props
    # message_id 不再必填：默认给当前这条，模型不需要（也拿不到）猜 id
    assert "required" not in tool["parameters"]
    assert "无法撤回" in tool["description"]


# ==================== 调用路径 ====================


async def test_semantic_tag_becomes_real_api_call():
    bot = FakeBot()
    result, _ = await _call(bot, {"reaction": "赞", "message_id": 777})
    assert bot.calls == [(ACTION, {"message_id": 777, "emoji_id": emoji_lexicon.DEFAULT_TAGS["赞"]})]
    assert result.startswith("{")  # 成功返回 OneBot data


async def test_message_id_defaults_to_current_message():
    bot = FakeBot()
    await _call(bot, {"reaction": "赞"}, trigger="555")
    assert bot.calls[0][1]["message_id"] == 555


async def test_explicit_emoji_id_is_reused():
    """上下文里出现过 [♡66] → 模型可以精确复用它（不复用就重新猜）。"""
    bot = FakeBot()
    await _call(bot, {"emoji_id": "66"})
    assert bot.calls[0][1]["emoji_id"] == "66"


async def test_unknown_tag_returns_correctable_error_without_calling_api():
    bot = FakeBot()
    result, _ = await _call(bot, {"reaction": "这个标签不存在"})
    assert result.startswith("error:")
    assert "可用语义标签" in result
    assert "不要猜" in result
    assert bot.calls == []


async def test_missing_reaction_returns_correctable_error():
    bot = FakeBot()
    result, _ = await _call(bot, {})
    assert result.startswith("error:")
    assert "reaction" in result
    assert bot.calls == []


async def test_existing_reaction_is_not_duplicated():
    """贴前先读：这条消息上已经有同一个表情 → 不重复贴，也不下行。"""
    from app.llm.group_log.events import KIND_EMOJI, LogEvent

    tag_id = emoji_lexicon.DEFAULT_TAGS["赞"]
    store = GroupLogStore(bot_id=1, retention_hours=0)
    store.append_many([LogEvent(
        ts=1_700_000_000, kind=KIND_EMOJI, scope=GROUP_SCOPE, group_id="1001",
        message_id="777", user_id="30003", nickname="三哥",
        payload={"emoji_id": tag_id, "is_add": True},
    )])
    bot = FakeBot()
    result, _ = await _call(bot, {"reaction": "赞", "message_id": 777}, store=store)
    assert bot.calls == []
    assert "无需重复" in result


async def test_successful_reaction_is_recorded_as_mine():
    store = GroupLogStore(bot_id=1, retention_hours=0)
    bot = FakeBot()
    await _call(bot, {"reaction": "赞", "message_id": 777, "reason": "用户说了个好消息"},
                store=store)
    tag_id = emoji_lexicon.DEFAULT_TAGS["赞"]
    assert store.reactions_of(GROUP_SCOPE, "777")[0]["emoji_id"] == tag_id
    assert store.my_actions_on(GROUP_SCOPE, "777") == [f"emoji:{tag_id}"]


async def test_failed_reaction_is_not_recorded():
    """失败不能记成"我贴过"，否则幂等判断会误判为已完成。"""
    store = GroupLogStore(bot_id=1, retention_hours=0)
    bot = FakeBot(status="failed")
    result, _ = await _call(bot, {"reaction": "赞", "message_id": 777}, store=store)
    assert result.startswith("error:")
    assert store.reactions_of(GROUP_SCOPE, "777") == []
    assert store.my_actions_on(GROUP_SCOPE, "777") == []


async def test_non_numeric_message_id_is_rejected():
    bot = FakeBot()
    result, _ = await _call(bot, {"reaction": "赞", "message_id": "abc"})
    assert result.startswith("error:")
    assert bot.calls == []
