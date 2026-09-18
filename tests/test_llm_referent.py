"""指代消解（app.llm.referent）测试。

回归目标（真实日志 group_466052056 / 2026-09-18 21:37）：
用户说「那你能做到消息里的那个样子吗」——纯回指句，没有 id、没有引用段。
修复前模型去背景块里挑了"群里最新那条转发"（1000165352），而真正该看的是上一轮讨论过的
576048059。本文件锁住判定规则与"候选不明就都取回"的策略。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.llm import focus, referent


def setup_function(_fn):
    focus.clear_all()


def _event(segments=None, *, user_id=1901691195, self_id=10001):
    return SimpleNamespace(
        message=segments or [],
        user_id=user_id,
        self_id=self_id,
        bot_id=self_id,
    )


# ---------- 判定规则 ----------


def test_explicit_reply_wins():
    plan = referent.resolve("你知道这个发了什么吗", _event([{"type": "reply", "data": {"id": "576048059"}}]))

    assert plan.kind == "reply"
    assert [c.ref for c in plan.messages] == ["576048059"]
    assert plan.messages[0].confidence == "explicit"
    assert plan.recent_count == 0  # 有明确指向就不需要按位置取


def test_at_targets_become_user_candidates():
    plan = referent.resolve("在吗", _event([{"type": "at", "data": {"qq": "123"}}]))

    assert [c.ref for c in plan.users] == ["123"]
    assert plan.users[0].confidence == "explicit"


def test_at_self_is_ignored():
    plan = referent.resolve("在吗", _event([{"type": "at", "data": {"qq": "10001"}}]), self_ids={"10001"})

    assert plan.users == []


def test_text_ids_are_candidates_but_not_qq_number():
    """增强块里的 QQ 号不该被当成消息 id（调用方传原始文本即可避免）。"""
    plan = referent.resolve("看看 576048059 这条", _event())

    assert [c.ref for c in plan.messages] == ["576048059"]


def test_relative_reference_maps_to_recent():
    for text in ["上一条消息说了什么", "刚才那条呢", "消息里的那个样子像吗", "最新一条是啥"]:
        plan = referent.resolve(text, _event())
        assert plan.recent_count >= 1, text
        assert plan.kind in ("relative", "anaphora"), text


def test_anaphora_uses_focus_when_no_new_entity():
    focus.begin_turn("b", "g")
    focus.note("b", "g", "576048059", kind="message", label="合并转发576048059",
               summary="成人向对话记录", source="expand")
    items = focus.items("b", "g")

    plan = referent.resolve("那你能做到消息里的那个样子吗", _event(), focus_items=items, self_ids={"10001"})

    assert plan.kind == "anaphora"
    assert [c.ref for c in plan.messages] == ["576048059"]
    assert plan.recent_count >= 1          # 同时看位置上的最近一条
    assert plan.ambiguous is True          # 两个候选 → 都取
    assert "焦点" in plan.note or "理解为" in plan.note


def test_anaphora_without_focus_is_not_actionable():
    plan = referent.resolve("那个样子呢", _event())

    assert plan.kind == "anaphora"
    assert plan.messages == []


def test_person_reference_uses_focus_user_else_sender():
    focus.note("b", "g", "123", kind="user", label="用户123", summary="三哥", source="expand")
    items = focus.items("b", "g")

    with_focus = referent.resolve("他是谁呀", _event(), focus_items=items)
    without_focus = referent.resolve("他是谁呀", _event())

    assert [c.ref for c in with_focus.users] == ["123"]
    assert [c.ref for c in without_focus.users] == ["1901691195"]  # 退回当前说话的人


def test_plain_chat_is_not_actionable():
    for text in ["在吗", "今天天气不错", "帮我写个周报", "1+1 等于几"]:
        plan = referent.resolve(text, _event())
        assert not plan.actionable, text
        assert plan.kind == "none", text


def test_explicit_id_in_text_ignores_self_id():
    plan = referent.resolve("查一下 10001 这条", _event(), self_ids={"10001"})

    assert plan.messages == []


# ---------- 组装块 ----------


class _Bot:
    def __init__(self, history=None, messages=None):
        self.history = history or []
        self.messages = messages or {}
        self.calls: list[tuple] = []

    async def get_msg_history(self, group_id=0, user_id=0, count=20, reverse_order=False):
        self.calls.append(("history", group_id, count))
        return {"status": "ok", "data": {"messages": self.history[-count:]}}

    async def get_msg(self, message_id):
        self.calls.append(("get_msg", message_id))
        data = self.messages.get(str(message_id))
        return {"status": "ok", "data": data} if data else {"status": "failed", "data": None}

    async def get_forward_msg(self, id):
        return {"status": "failed", "retcode": 1404, "data": None}

    async def get_group_member_info(self, group_id, user_id):
        return {"status": "failed", "retcode": 1404, "data": None}


def _runtime(config=None, bot_id="10001"):
    cfg = {"referent_resolve_enable": True, "referent_prefetch_enable": True}
    cfg.update(config or {})
    return SimpleNamespace(bot_id=bot_id, config=cfg)


def _ctx(bot, event=None, session_id="group_466052056"):
    runtime = _runtime()
    return SimpleNamespace(bot=bot, runtime=runtime, session_id=session_id,
                           group_id=466052056, user_id=1901691195, event=event)


async def test_build_block_prefetches_focus_candidate_and_recent():
    """纯回指 + 焦点候选 → fetch_all：焦点那条与最近一条都进块。

    焦点那条本会话已取过 → 命中登记，**不再调用 API**（零重复读取）；
    最近一条按位置取回。
    """
    runtime = _runtime()
    focus.note("10001", "group_466052056", "576048059", kind="message",
               label="合并转发576048059", summary="上一轮那段的摘要", source="expand")
    focus.begin_turn("10001", "group_466052056")
    bot = _Bot(
        messages={"576048059": {
            "sender": {"user_id": 1901691195, "nickname": "桉"},
            "message": [{"type": "text", "data": {"text": "不该被重新取"}}],
        }},
        history=[{"message_id": 1000165352, "sender": {"user_id": 3569937952, "nickname": "小鳥遊ホシノ"},
                  "message": [{"type": "text", "data": {"text": "B站《学校的8种违法行为》"}}]}],
    )
    ctx = _ctx(bot, _event([{"type": "at", "data": {"qq": "10001"}}]))

    block = await referent.build_block(runtime, "group_466052056", "那你能做到消息里的那个样子吗", ctx)

    assert "当前对话焦点" in block
    assert "576048059" in block and "1000165352" in block
    assert "上一轮那段的摘要" in block          # 复用登记摘要
    assert not any(c[0] == "get_msg" for c in bot.calls)     # 不重复读取
    assert any(c[0] == "history" for c in bot.calls)         # 按位置取回最近一条


async def test_build_block_skips_prefetch_when_disabled():
    runtime = _runtime({"referent_prefetch_enable": False})
    focus.note("10001", "g", "576048059", kind="message", summary="S", source="expand")
    bot = _Bot()
    ctx = _ctx(bot, _event(), session_id="g")

    block = await referent.build_block(runtime, "g", "那个样子呢", ctx)

    assert "当前对话焦点" in block
    assert bot.calls == []          # 只给焦点，不预取
    assert "理解为" in block         # 但仍告诉模型框架的判定


async def test_build_block_off_when_referent_disabled():
    runtime = _runtime({"referent_resolve_enable": False})
    focus.note("10001", "g", "1", kind="message", summary="S", source="expand")

    assert await referent.build_block(runtime, "g", "那个样子", _ctx(_Bot(), session_id="g")) == ""


async def test_ambiguous_policy_ask_does_not_prefetch():
    runtime = _runtime({"referent_ambiguous_policy": "ask"})
    focus.note("10001", "g", "576048059", kind="message", summary="S", source="expand")
    focus.begin_turn("10001", "g")
    bot = _Bot(history=[{"message_id": 2, "sender": {"user_id": 1}, "message": [{"type": "text", "data": {"text": "x"}}]}])
    ctx = _ctx(bot, _event(), session_id="g")

    block = await referent.build_block(runtime, "g", "那个样子呢", ctx)

    assert bot.calls == []
    assert "先问用户" in block or "拿不准" in block


async def test_focus_first_policy_skips_recent():
    runtime = _runtime({"referent_ambiguous_policy": "focus_first"})
    focus.note("10001", "g", "576048059", kind="message", summary="S", source="expand")
    focus.begin_turn("10001", "g")
    bot = _Bot(messages={"576048059": {
        "sender": {"user_id": 1, "nickname": "n"},
        "message": [{"type": "text", "data": {"text": "内容"}}],
    }})
    ctx = _ctx(bot, _event(), session_id="g")

    block = await referent.build_block(runtime, "g", "那个样子呢", ctx)

    assert not any(c[0] == "history" for c in bot.calls)   # 不取最近
    assert "内容" in block


async def test_focus_only_block_for_initiative_paths():
    focus.note("10001", "g", "576048059", kind="message", summary="上一轮那段", source="expand")
    bot = _Bot()

    block = await referent.focus_only_block(_runtime(), "g")

    assert "当前对话焦点" in block
    assert bot.calls == []


async def test_build_block_survives_prefetch_failure():
    """预取失败（连接异常等）不能影响回复主流程。"""

    class _BrokenBot(_Bot):
        async def get_msg_history(self, **kw):
            raise RuntimeError("连接已断开")

        async def get_msg(self, message_id):
            raise RuntimeError("连接已断开")

    runtime = _runtime()
    focus.note("10001", "g", "576048059", kind="message", summary="S", source="expand")
    focus.begin_turn("10001", "g")
    ctx = _ctx(_BrokenBot(), _event(), session_id="g")

    block = await referent.build_block(runtime, "g", "那个样子呢", ctx)

    assert "当前对话焦点" in block    # 焦点行仍在


# ---------- chat 侧接入 ----------


async def test_chat_referent_block_uses_raw_user_text():
    """判定必须用原始文本：增强块里的 QQ 号不能被当成消息 id。"""
    from app.llm.chat import _referent_block

    focus.note("10001", "g", "576048059", kind="message", summary="S", source="expand")
    focus.begin_turn("10001", "g")
    bot = _Bot(messages={"576048059": {
        "sender": {"user_id": 1, "nickname": "n"},
        "message": [{"type": "text", "data": {"text": "内容"}}],
    }})
    event = _event([{"type": "text", "data": {"text": "那个样子呢"}}])
    ctx = SimpleNamespace(bot=bot, runtime=_runtime(), session_id="g", group_id=1, event=event)

    block = await _referent_block(_runtime(), "g", "发送者：用户1901691195\n发送了：那个样子呢", ctx)

    assert "当前对话焦点" in block
    assert all(c[0] != "get_msg" or c[1] != 1901691195 for c in bot.calls)
