"""上下文「骨架补全」测试：@ 预展开、未展开标记、缺口可解决性。

背景（要解决的问题）：QQ 消息里到处是"骨架"——``@123``、``[引用]``，模型看不到 123 是谁、
被引用的是什么。而在本仓库里，反查昵称的管道（含缓存）**只作用于触发消息**，
群聊背景块——也就是"群里其他人聊了什么"的唯一可见窗口——一直只输出裸 ``@123``。

本文件锁住三件事：
1. 能展开的就在渲染层直接展开（零模型成本，``@123`` → ``@三哥(123)``，
   复用全局 ``昵称(QQ)`` 约定，不引入新语法）；
2. **只标记可被解决的缺口**：反查失败 → ``【未展开:用户123】``；而图片/语音这类
   无展开手段的内容保持 ``[图片]`` 纯占位（标成未展开只会诱导模型空转或谎称无法回答）；
3. 开关（``fetch_at_nickname`` / ``context_expand_enable``）能整体关掉，退回旧渲染。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.llm import nicknames
from app.llm.group_context import (
    UNRESOLVED_AT,
    UNRESOLVED_FORWARD,
    UNRESOLVED_REPLY,
    collect_at_ids,
    extract_msg_text,
    fetch_group_online_history,
    format_online_history,
)

AT_MESSAGES = [
    {
        "time": 1788342159,
        "sender": {"user_id": 20002, "nickname": "小明", "card": ""},
        "message": [
            {"type": "at", "data": {"qq": "123"}},
            {"type": "text", "data": {"text": " 在吗"}},
        ],
    },
    {
        "time": 1788342200,
        "sender": {"user_id": 30003, "nickname": "小红", "card": ""},
        "message": [
            {"type": "reply", "data": {"id": "456"}},
            {"type": "text", "data": {"text": "收到"}},
        ],
    },
    {
        "time": 1788342260,
        "sender": {"user_id": 40004, "nickname": "小刚", "card": ""},
        "message": [
            {"type": "at", "data": {"qq": "all"}},
            {"type": "image", "data": {"file": "x.jpg"}},
        ],
    },
]


class _FakeBot:
    """同时支持 get_msg_history 与 get_group_member_info 的 Bot 替身。"""

    def __init__(self, response, members=None, *, member_fail: bool = False):
        self.response = response
        self.members = members or {}
        self.member_fail = member_fail
        self.calls: list[dict] = []
        self.member_calls: list[tuple] = []

    async def get_msg_history(self, group_id=0, user_id=0, count=20, reverse_order=False):
        self.calls.append({"group_id": group_id, "user_id": user_id, "count": count})
        return self.response

    async def get_group_member_info(self, group_id, user_id):
        self.member_calls.append((group_id, user_id))
        if self.member_fail:
            raise RuntimeError("连接已断开")
        info = self.members.get(int(user_id))
        if info is None:
            return {"status": "failed", "retcode": 1404, "data": None}
        return {"status": "ok", "retcode": 0, "data": info}


def _envelope(messages):
    return {"status": "ok", "retcode": 0, "data": {"messages": messages}}


def setup_function(_fn):
    nicknames.clear_cache()


# ---------- 段级渲染 ----------


def test_at_segment_is_expanded_with_nickname():
    seg = {"type": "at", "data": {"qq": "123"}}

    assert extract_msg_text([seg], {"123": "三哥"}) == "@三哥(123)"


def test_at_segment_falls_back_to_bare_id_when_not_marking():
    """未开启标记时必须保持旧的裸 ``@123`` 渲染（开关可完全退回）。"""
    seg = {"type": "at", "data": {"qq": "123"}}

    assert extract_msg_text([seg]) == "@123"
    assert extract_msg_text([seg], {"999": "别人"}) == "@123"


def test_at_segment_marks_unresolved_when_requested():
    seg = {"type": "at", "data": {"qq": "123"}}

    assert extract_msg_text([seg], None, True) == UNRESOLVED_AT.format(qq="123")
    assert extract_msg_text([seg], {"999": "别人"}, True) == UNRESOLVED_AT.format(qq="123")


def test_at_all_stays_readable():
    for qq in ("all", "0", ""):
        assert extract_msg_text([{"type": "at", "data": {"qq": qq}}]) == "@所有人"


def test_reply_and_forward_markers():
    reply = [{"type": "reply", "data": {"id": "456"}}]
    forward = [{"type": "forward", "data": {"id": "f1"}}]

    assert extract_msg_text(reply) == "[引用]"
    assert extract_msg_text(reply, None, True) == UNRESOLVED_REPLY.format(id="456")
    assert extract_msg_text(forward) == "[合并转发]"
    assert extract_msg_text(forward, None, True) == UNRESOLVED_FORWARD


def test_reply_without_id_is_not_marked():
    """没有 id 的引用无从展开，保持旧占位而不是打一个无法解决的标记。"""
    assert extract_msg_text([{"type": "reply", "data": {}}], None, True) == "[引用]"


def test_content_type_placeholders_are_never_marked_unresolved():
    """图片/语音/文件等无展开手段的内容保持纯占位（只标记可被解决的缺口）。"""
    segs = [
        {"type": "image", "data": {}},
        {"type": "record", "data": {}},
        {"type": "file", "data": {}},
    ]

    assert extract_msg_text(segs, None, True) == "[图片][语音][文件]"


def test_collect_at_ids_dedupes_and_skips_all():
    msgs = AT_MESSAGES + [{
        "sender": {"user_id": 1},
        "message": [{"type": "at", "data": {"qq": "123"}}, {"type": "at", "data": {"qq": "0"}}],
    }]

    assert collect_at_ids(msgs) == ["123"]
    assert collect_at_ids([{"message": "not-a-list"}, "oops", {}]) == []


# ---------- 背景块渲染 ----------


def test_format_online_history_expands_at_in_lines():
    text = format_online_history(AT_MESSAGES, 10, self_ids={"99999"}, at_names={"123": "三哥"})

    assert "@三哥(123) 在吗" in text
    assert "@所有人" in text


def test_format_online_history_marks_unresolved_rows():
    text = format_online_history(AT_MESSAGES, 10, self_ids=set(), mark_unresolved=True)

    assert UNRESOLVED_AT.format(qq="123") in text
    assert UNRESOLVED_REPLY.format(id="456") in text


# ---------- 拉取时预展开 ----------


async def test_fetch_expands_at_using_member_info():
    bot = _FakeBot(_envelope(AT_MESSAGES), {123: {"nickname": "张三", "card": "三哥"}})

    text = await fetch_group_online_history(bot, 778, count=10, self_ids=set(), bot_id="b1")

    assert "@三哥(123) 在吗" in text
    assert bot.member_calls == [(778, 123)]
    assert "@123" not in text


async def test_fetch_reuses_nickname_cache():
    """同一轮里"触发消息展开 → 背景块再渲染"不得对同一个人重复请求。"""
    bot = _FakeBot(_envelope(AT_MESSAGES), {123: {"nickname": "张三"}})

    await fetch_group_online_history(bot, 778, count=10, self_ids=set(), bot_id="b1")
    await fetch_group_online_history(bot, 778, count=10, self_ids=set(), bot_id="b1")

    assert bot.member_calls == [(778, 123)]


async def test_fetch_can_disable_pre_expansion():
    bot = _FakeBot(_envelope(AT_MESSAGES), {123: {"nickname": "张三"}})

    text = await fetch_group_online_history(bot, 778, count=10, self_ids=set(), resolve_at=False)

    assert "@123 在吗" in text
    assert bot.member_calls == []


async def test_fetch_marks_unresolved_when_member_lookup_fails():
    bot = _FakeBot(_envelope(AT_MESSAGES), member_fail=True)

    text = await fetch_group_online_history(
        bot, 778, count=10, self_ids=set(), mark_unresolved=True, bot_id="b1"
    )

    assert UNRESOLVED_AT.format(qq="123") in text
    assert "@123" not in text


async def test_fetch_failure_returns_empty_and_logs():
    """取不到历史时仍返回空串（调用方把空块当无背景），但不再静默。"""
    bot = _FakeBot(None)

    assert await fetch_group_online_history(bot, 778, count=10) == ""


# ---------- 开关透传 ----------


def test_context_expand_flags_default_on():
    from app.llm.chat import _context_expand_flags

    assert _context_expand_flags({}) == {"resolve_at": True, "mark_unresolved": True}
    assert _context_expand_flags({"fetch_at_nickname": False})["resolve_at"] is False
    assert _context_expand_flags({"context_expand_enable": False})["mark_unresolved"] is False


class _BadCfg(dict):
    def get(self, key, default=None):
        raise RuntimeError("配置后端异常")


def test_context_expand_flags_tolerates_broken_config():
    from app.llm.chat import _context_expand_flags

    assert _context_expand_flags(_BadCfg()) == {"resolve_at": True, "mark_unresolved": True}


async def test_pre_history_block_honours_flags():
    from app.llm.chat import _build_group_pre_history

    event = SimpleNamespace(
        bot=_FakeBot(_envelope(AT_MESSAGES), {123: {"nickname": "张三", "card": "三哥"}}),
        self_id=10001,
        bot_id=10001,
        group=SimpleNamespace(group_name="测试群"),
    )

    expanded = await _build_group_pre_history(event, "778", count=10)
    raw = await _build_group_pre_history(event, "778", count=10, resolve_at=False)

    assert "@三哥(123) 在吗" in expanded
    assert "@123 在吗" in raw
