"""系统级会话上下文工具测试：get_current_session / get_chat_history。

get_chat_history 取代了旧的 get_session_history：零参数、自动定位当前会话，
本地记录不足时自动补拉 QQ 记录，取不到时给明确路标。
"""

from types import SimpleNamespace

import pytest

from app.llm.session import SessionManager
from app.llm.session_tools import build_session_tools


@pytest.fixture
def llm_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    return tmp_path / "llm"


class _FakeBot:
    """记录 get_msg_history 调用，返回构造好的 OneBot 历史消息。"""

    def __init__(self, messages=None, group_name="测试群", *, member=True, groups=None):
        self.calls: list[dict] = []
        self.member_calls: list[dict] = []
        self.messages = messages if messages is not None else [
            {
                "time": 1788342159,
                "sender": {"user_id": 20002, "nickname": "小明", "card": ""},
                "message": [{"type": "text", "data": {"text": "晚上一起打游戏吗"}}],
            },
            {
                "time": 1788342200,
                "sender": {"user_id": 10001, "nickname": "我", "card": ""},
                "message": [{"type": "text", "data": {"text": "好呀"}}],
            },
        ]
        self.group_name = group_name
        self.member = member
        self.groups = groups if groups is not None else [
            {"group_id": 778459818, "group_name": "蛋挞空间站"},
            {"group_id": 12345, "group_name": "测试群"},
        ]

    async def get_msg_history(self, group_id=0, user_id=0, count=20, reverse_order=False):
        self.calls.append({"group_id": group_id, "user_id": user_id, "count": count})
        return {"status": "ok", "retcode": 0, "data": {"messages": self.messages}}

    async def get_group_info(self, group_id, no_cache=False):
        return {"status": "ok", "data": {"group_name": self.group_name}}

    async def get_group_list(self):
        return {"status": "ok", "data": list(self.groups)}

    async def get_group_member_info(self, group_id, user_id, no_cache=False):
        self.member_calls.append({"group_id": group_id, "user_id": user_id})
        if not self.member:
            return {"status": "failed", "retcode": 1404, "message": "群成员不存在", "data": None}
        return {"status": "ok", "data": {"user_id": user_id, "nickname": "成员"}}


def _ctx(runtime, bot, session_id, *, user_id=None, group_id=None, event=None):
    return SimpleNamespace(
        runtime=runtime,
        bot=bot,
        session_id=session_id,
        event=event,
        user_id=user_id,
        group_id=group_id,
    )


def _tool(runtime, ctx, name):
    return next(t for t in build_session_tools(runtime, ctx) if t.name == name)


async def test_get_current_session_group_info(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_group")
    event = SimpleNamespace(
        user_id=10001,
        permission_role="admin",
        role="admin",
        user=SimpleNamespace(user_id=10001, nickname="小明", card="小明明"),
        group=SimpleNamespace(group_id=12345, group_name="测试群"),
    )
    ctx = _ctx(runtime, SimpleNamespace(), "group_12345", user_id=10001, group_id=12345, event=event)

    spec = _tool(runtime, ctx, "get_current_session")
    result = await spec.handler(ctx, {})

    assert "会话类型：群聊" in result
    assert "会话ID：group_12345" in result
    assert "Bot ID：bot_group" in result
    assert "当前用户 QQ：10001" in result
    assert "当前用户群名片：小明明" in result
    assert "当前用户角色：admin" in result
    assert "群号：12345" in result
    assert "群名：测试群" in result


async def test_get_current_session_private(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_private")
    ctx = _ctx(
        runtime,
        SimpleNamespace(),
        "private_888",
        user_id=888,
        event=SimpleNamespace(user_id=888, permission_role="owner", role="owner"),
    )

    spec = _tool(runtime, ctx, "get_current_session")
    result = await spec.handler(ctx, {})

    assert "会话类型：私聊" in result
    assert "会话ID：private_888" in result
    assert "对方 QQ：888" in result
    assert "当前用户角色：owner" in result


async def test_session_history_tool_replaced_by_chat_history(llm_data_dir):
    """三个历史工具（本地 + 两个 NapCat）收敛为一个零参数入口。"""
    runtime = SimpleNamespace(bot_id="bot_names", config={})
    ctx = _ctx(runtime, SimpleNamespace(), "group_1", user_id=1, group_id=1)
    names = {t.name for t in build_session_tools(runtime, ctx)}

    assert "get_chat_history" in names
    assert "get_session_history" not in names


async def test_chat_history_returns_local_records(llm_data_dir):
    bot_id = "bot_local"
    manager = SessionManager(bot_id)
    try:
        manager.create_session("group_9", "group", 60)
        manager.add_message("group_9", "user", "你好", user_id="100", nickname="小明")
        manager.add_message("group_9", "assistant", "你好呀")

        runtime = SimpleNamespace(bot_id=bot_id, config={"history_auto_qq_min_local": 20})
        bot = _FakeBot()
        ctx = _ctx(runtime, bot, "group_9", user_id=100, group_id=9)

        result = await _tool(runtime, ctx, "get_chat_history").handler(ctx, {"limit": 10})

        assert "【本地会话记录】" in result
        assert "小明" in result
        assert "你好呀" in result
        # 本地条数（2）< 阈值（20）→ auto 会补拉 QQ
        assert bot.calls and bot.calls[0]["group_id"] == 9
        assert "【QQ 聊天记录】" in result
    finally:
        manager.stop_cleanup()


async def test_chat_history_scope_local_never_touches_qq(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_scope_local", config={})
    bot = _FakeBot()
    ctx = _ctx(runtime, bot, "group_5", user_id=1, group_id=5)

    result = await _tool(runtime, ctx, "get_chat_history").handler(ctx, {"scope": "local"})

    assert bot.calls == []
    assert "QQ 记录：未查询" in result or "未查询" in result


async def test_chat_history_auto_skips_qq_when_local_enough(llm_data_dir):
    bot_id = "bot_enough"
    manager = SessionManager(bot_id)
    try:
        manager.create_session("group_7", "group", 60)
        for i in range(6):
            manager.add_message("group_7", "user", f"消息{i}", user_id="100", nickname="小明")

        runtime = SimpleNamespace(bot_id=bot_id, config={"history_auto_qq_min_local": 3})
        bot = _FakeBot()
        ctx = _ctx(runtime, bot, "group_7", user_id=100, group_id=7)

        result = await _tool(runtime, ctx, "get_chat_history").handler(ctx, {})

        assert "本地记录 6 条" in result
        assert bot.calls == []  # 本地充足 → 不补拉，省一次接口调用
    finally:
        manager.stop_cleanup()


async def test_chat_history_private_uses_current_peer(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_private_h", config={})
    bot = _FakeBot()
    ctx = _ctx(runtime, bot, "private_888", user_id=888)

    result = await _tool(runtime, ctx, "get_chat_history").handler(
        ctx, {"scope": "qq", "user_id": 888}
    )

    assert bot.calls == [{"group_id": 0, "user_id": 888, "count": 30}]
    assert "晚上一起打游戏吗" in result
    assert "【QQ 聊天记录】" in result


# ---------- 跨会话查询（私聊问“群里说了什么”） ----------


async def test_private_session_can_query_group_the_requester_is_in(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_cross_ok", config={})
    bot = _FakeBot()
    ctx = _ctx(runtime, bot, "private_888", user_id=888)

    result = await _tool(runtime, ctx, "get_chat_history").handler(
        ctx, {"group_id": 778459818}
    )

    # 先校验发起人是否该群成员，再取该群历史
    assert bot.member_calls == [{"group_id": 778459818, "user_id": 888}]
    assert bot.calls == [{"group_id": 778459818, "user_id": 0, "count": 30}]
    assert "跨会话查询" in result
    assert "晚上一起打游戏吗" in result


async def test_private_session_can_query_group_by_name(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_cross_name", config={})
    bot = _FakeBot()
    ctx = _ctx(runtime, bot, "private_888", user_id=888)

    result = await _tool(runtime, ctx, "get_chat_history").handler(
        ctx, {"group_name": "蛋挞空间站"}
    )

    assert bot.calls and bot.calls[0]["group_id"] == 778459818
    assert "跨会话查询" in result


async def test_ambiguous_group_name_asks_for_group_id(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_cross_amb", config={})
    bot = _FakeBot(groups=[
        {"group_id": 1, "group_name": "测试群一号"},
        {"group_id": 2, "group_name": "测试群二号"},
    ])
    ctx = _ctx(runtime, bot, "private_888", user_id=888)

    result = await _tool(runtime, ctx, "get_chat_history").handler(
        ctx, {"group_name": "测试群"}
    )

    assert "匹配到多个群" in result
    assert bot.calls == []  # 歧义时不查任何记录


async def test_cross_query_refused_when_requester_not_in_group(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_cross_deny", config={})
    bot = _FakeBot(member=False)
    ctx = _ctx(runtime, bot, "private_888", user_id=888)

    result = await _tool(runtime, ctx, "get_chat_history").handler(
        ctx, {"group_id": 778459818}
    )

    assert "不是群 778459818 的成员" in result
    assert bot.calls == []  # 校验失败不取任何记录


async def test_cross_query_refused_in_group_session(llm_data_dir):
    """群聊里查别的群会把内容泄漏给不在该群的人，直接拒绝。"""
    runtime = SimpleNamespace(bot_id="bot_cross_group", config={})
    bot = _FakeBot()
    ctx = _ctx(runtime, bot, "group_12345", user_id=10001, group_id=12345)

    result = await _tool(runtime, ctx, "get_chat_history").handler(
        ctx, {"group_id": 778459818}
    )

    assert "群聊里不支持查询其它群" in result
    assert bot.calls == [] and bot.member_calls == []


async def test_cross_query_refused_when_disabled(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_cross_off", config={"history_cross_query_enable": False})
    bot = _FakeBot()
    ctx = _ctx(runtime, bot, "private_888", user_id=888)

    result = await _tool(runtime, ctx, "get_chat_history").handler(
        ctx, {"group_id": 778459818}
    )

    assert "跨会话查询已关闭" in result
    assert bot.calls == []


async def test_cannot_read_other_private_chats(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_peer_deny", config={})
    bot = _FakeBot()
    ctx = _ctx(runtime, bot, "private_888", user_id=888)

    result = await _tool(runtime, ctx, "get_chat_history").handler(
        ctx, {"user_id": 777}
    )

    assert "只能查询当前会话这条私聊" in result
    assert bot.calls == []


async def test_cross_query_rejects_two_targets_and_bad_ids(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_cross_args", config={})
    bot = _FakeBot()
    ctx = _ctx(runtime, bot, "private_888", user_id=888)
    spec = _tool(runtime, ctx, "get_chat_history")

    assert "一次只能查一个目标" in await spec.handler(ctx, {"group_id": 1, "user_id": 888})
    assert "必须是纯数字群号" in await spec.handler(ctx, {"group_id": "群一"})
    assert bot.calls == []


async def test_cross_query_local_scope_tells_model_to_use_qq(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_cross_local", config={})
    bot = _FakeBot()
    ctx = _ctx(runtime, bot, "private_888", user_id=888)

    result = await _tool(runtime, ctx, "get_chat_history").handler(
        ctx, {"group_id": 778459818, "scope": "local"}
    )

    assert "跨会话查询请用 scope=qq" in result
    assert bot.calls == []


async def test_current_group_and_peer_targets_still_allowed(llm_data_dir):
    """传了当前会话自己的群号/QQ 时不算跨会话，也不触发成员校验。"""
    runtime = SimpleNamespace(bot_id="bot_same", config={})
    bot = _FakeBot()
    ctx = _ctx(runtime, bot, "group_12345", user_id=10001, group_id=12345)

    result = await _tool(runtime, ctx, "get_chat_history").handler(
        ctx, {"group_id": 12345, "scope": "qq"}
    )

    assert bot.member_calls == []
    assert bot.calls and bot.calls[0]["group_id"] == 12345
    assert "跨会话查询" not in result


async def test_chat_history_empty_gives_next_step_hint(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_empty", config={})
    ctx = _ctx(runtime, SimpleNamespace(), "group_404", user_id=1, group_id=404)
    ctx.bot = None

    result = await _tool(runtime, ctx, "get_chat_history").handler(ctx, {})

    assert "本地记录 0 条" in result
    assert "不要假装记得" in result


async def test_chat_history_scope_local_empty_tells_model_to_query_qq(llm_data_dir):
    runtime = SimpleNamespace(bot_id="bot_hint", config={})
    ctx = _ctx(runtime, _FakeBot(), "group_404", user_id=1, group_id=404)

    result = await _tool(runtime, ctx, "get_chat_history").handler(ctx, {"scope": "local"})

    assert "scope=qq" in result
