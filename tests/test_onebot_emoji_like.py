"""表情回应工具测试：语义解析、贴前先读、幂等、记账、错误可纠正。"""

from __future__ import annotations

from types import SimpleNamespace

from app.llm import emoji_lexicon, qq_faces
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


# ==================== 全量映射（客户端系统表情表） ====================


def test_face_table_is_full_and_numeric():
    """表是客户端表的快照（近 300 条），不是十几个常见表情的摘录。"""
    assert len(qq_faces.SYSFACE_IDS) >= 250
    assert all(v.isdigit() for v in qq_faces.SYSFACE_IDS.values())
    # 网上那份"微笑=1"的列表是错的：QQ 实际是 撇嘴=1、微笑=14
    assert qq_faces.SYSFACE_IDS["撇嘴"] == "1"
    assert qq_faces.SYSFACE_IDS["微笑"] == "14"


def test_lexicon_covers_every_face_in_client_table():
    """全量映射：客户端表里每个名字都能解析到自己的 id。"""
    for name, emoji_id in qq_faces.SYSFACE_IDS.items():
        assert emoji_lexicon.resolve(name) == (emoji_id, "table"), name


def test_lexicon_ids_match_client_table():
    """抽查一批名字的 id（改错数字 = 贴错表情，且不可撤回）。"""
    tags = emoji_lexicon.DEFAULT_TAGS
    assert tags["爱心"] == "66"
    assert tags["赞"] == "76"
    assert tags["笑哭"] == "182"
    assert tags["doge"] == "179"
    assert tags["惊恐"] == "26"
    assert tags["疑问"] == "32"
    assert tags["吃瓜"] == "271"
    assert tags["比心"] == "319"
    assert tags["捂脸"] == "264"
    assert emoji_lexicon.label_for("76") == "赞"
    assert emoji_lexicon.label_for("66") == "爱心"


def test_alias_tags_bridge_colloquial_names():
    """口语别名桥到表内正式名（不造 id）。"""
    assert emoji_lexicon.resolve("问号")[0] == qq_faces.SYSFACE_IDS["疑问"]
    assert emoji_lexicon.resolve("无语")[0] == qq_faces.SYSFACE_IDS["面无表情"]
    assert emoji_lexicon.resolve("加油")[0] == qq_faces.SYSFACE_IDS["打call"]
    assert emoji_lexicon.resolve("狗头")[0] == qq_faces.SYSFACE_IDS["doge"]
    assert emoji_lexicon.resolve("大兵")[0] == qq_faces.SYSFACE_IDS["悠闲"]
    # 口语"点个赞"桥到经典赞(76)；表内同名表情（点赞 201）不被别名覆盖
    assert emoji_lexicon.resolve("点个赞！")[0] == qq_faces.SYSFACE_IDS["赞"]
    assert emoji_lexicon.resolve("点赞")[0] == qq_faces.SYSFACE_IDS["点赞"]


def test_single_char_names_only_match_exactly():
    """单字名不做包含匹配，否则"很困难"会被当成贴「困」。"""
    assert emoji_lexicon.resolve("很困难") == ("", "")
    assert emoji_lexicon.resolve("喝茶") == ("", "")
    assert emoji_lexicon.resolve("我快哭了")[0] == qq_faces.SYSFACE_IDS["快哭了"]
    assert emoji_lexicon.resolve("困")[0] == qq_faces.SYSFACE_IDS["困"]


def test_large_faces_are_marked_for_calibration():
    """大表情/动态表情（faceType 2/3）标记出来，供真机校准。"""
    assert qq_faces.LARGE_FACES
    assert emoji_lexicon.is_large_face("捂脸")
    assert emoji_lexicon.is_large_face(qq_faces.SYSFACE_IDS["捂脸"])
    assert not emoji_lexicon.is_large_face("赞")


def test_numeric_face_name_is_treated_as_name_not_id():
    """「666」既是客户端表里的表情名、又不是表里的 id → 按名字解析（避免贴不存在的 id）。"""
    assert emoji_lexicon.resolve("666") == (qq_faces.SYSFACE_IDS["666"], "table")
    # 真 id 仍然优先走 explicit（上下文里 [♡66] 的数字要能原样复用）
    assert emoji_lexicon.resolve("66") == ("66", "explicit")


def test_available_tags_reports_total():
    text = emoji_lexicon.available_tags()
    assert "赞" in text and "共" in text


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


async def test_modern_face_name_becomes_real_id():
    """全量词表里的"新表情"（大表情）同样能落到正确 id 上。"""
    bot = FakeBot()
    await _call(bot, {"reaction": "吃瓜", "message_id": 777})
    assert bot.calls[0][1]["emoji_id"] == qq_faces.SYSFACE_IDS["吃瓜"]


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
