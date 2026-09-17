"""昵称反查共享实现（app.llm.nicknames）测试。

目的：把"骨架"（``@123``）补成"血肉"（``@三哥(123)``）的反查管道必须**共享缓存**，
否则同一轮里"触发消息展开 → 背景块再展开"会对同一个人重复请求 OneBot。
"""

from __future__ import annotations

import asyncio

from app.llm import nicknames


class _Bot:
    """最小 Bot 替身：记录调用并按映射返回群成员信息。"""

    def __init__(self, members: dict, *, fail: bool = False, status: str = "ok") -> None:
        self.members = members
        self.fail = fail
        self.status = status
        self.calls: list[tuple] = []

    async def get_group_member_info(self, group_id, user_id):
        self.calls.append((group_id, user_id))
        if self.fail:
            raise RuntimeError("连接已断开")
        info = self.members.get(int(user_id))
        if info is None:
            return {"status": "failed", "retcode": 1404, "data": None}
        return {"status": self.status, "retcode": 0, "data": info}


def setup_function(_fn):
    nicknames.clear_cache()


# ---------- 单个反查 ----------


async def test_card_takes_priority_over_nickname():
    bot = _Bot({123: {"user_id": 123, "nickname": "张三", "card": "三哥"}})

    assert await nicknames.fetch_nickname(bot, 778, "123", bot_id="b1") == "三哥"
    assert bot.calls == [(778, 123)]


async def test_second_lookup_hits_cache():
    bot = _Bot({123: {"nickname": "张三"}})

    assert await nicknames.fetch_nickname(bot, 778, "123", bot_id="b1") == "张三"
    assert await nicknames.fetch_nickname(bot, 778, 123, bot_id="b1") == "张三"
    assert len(bot.calls) == 1  # 第二次不再请求


async def test_cache_is_scoped_by_bot_and_group():
    bot = _Bot({123: {"nickname": "张三"}})

    await nicknames.fetch_nickname(bot, 778, "123", bot_id="b1")
    await nicknames.fetch_nickname(bot, 779, "123", bot_id="b1")  # 换群
    await nicknames.fetch_nickname(bot, 778, "123", bot_id="b2")  # 换 bot

    assert bot.calls == [(778, 123), (779, 123), (778, 123)]


async def test_failure_is_not_cached_and_can_retry():
    bot = _Bot({}, fail=True)

    assert await nicknames.fetch_nickname(bot, 778, "123", bot_id="b1") == ""
    bot.fail = False
    bot.members[123] = {"nickname": "张三"}

    assert await nicknames.fetch_nickname(bot, 778, "123", bot_id="b1") == "张三"
    assert len(bot.calls) == 2


async def test_non_ok_status_returns_empty_and_is_not_cached():
    bot = _Bot({}, status="failed")

    assert await nicknames.fetch_nickname(bot, 778, "123", bot_id="b1") == ""
    assert nicknames.cached_nickname("b1", 778, "123") == ""


async def test_missing_member_returns_empty():
    bot = _Bot({})

    assert await nicknames.fetch_nickname(bot, 778, "999", bot_id="b1") == ""


async def test_invalid_inputs_short_circuit():
    bot = _Bot({})

    assert await nicknames.fetch_nickname(None, 778, "123") == ""
    assert await nicknames.fetch_nickname(bot, None, "123") == ""
    assert await nicknames.fetch_nickname(bot, 778, "") == ""
    assert await nicknames.fetch_nickname(bot, 778, "not-a-number") == ""
    assert bot.calls == []


async def test_empty_nickname_is_not_remembered():
    nicknames.remember("b1", 778, "123", "")
    assert nicknames.cached_nickname("b1", 778, "123") == ""


# ---------- 批量反查 ----------


async def test_resolve_nicknames_runs_concurrently():
    """批量反查必须并发：串行会把背景块渲染延迟叠加（一人一次网络往返）。"""
    started: list[str] = []

    class _SlowBot:
        async def get_group_member_info(self, group_id, user_id):
            started.append(str(user_id))
            for _ in range(60):
                if len(started) == 3:
                    break
                await asyncio.sleep(0.01)
            return {"status": "ok", "data": {"nickname": f"n{user_id}"}}

    resolved = await nicknames.resolve_nicknames(_SlowBot(), 778, ["1", "2", "3"], bot_id="b1")

    assert resolved == {"1": "n1", "2": "n2", "3": "n3"}
    assert len(started) == 3  # 三个都同时处于执行中


async def test_resolve_nicknames_uses_cache_and_dedupes():
    bot = _Bot({123: {"nickname": "张三"}})

    resolved = await nicknames.resolve_nicknames(bot, 778, ["123", 123, "123"], bot_id="b1")

    assert resolved == {"123": "张三"}
    assert bot.calls == [(778, 123)]


async def test_resolve_nicknames_skips_unresolvable_without_failing():
    bot = _Bot({123: {"nickname": "张三"}})

    resolved = await nicknames.resolve_nicknames(bot, 778, ["123", "999", "bad", ""], bot_id="b1")

    assert resolved == {"123": "张三"}


async def test_resolve_nicknames_empty_input():
    bot = _Bot({})

    assert await nicknames.resolve_nicknames(bot, 778, [], bot_id="b1") == {}
    assert await nicknames.resolve_nicknames(bot, 778, None, bot_id="b1") == {}
    assert bot.calls == []


# ---------- 缓存上限 ----------


def test_cache_is_bounded():
    for i in range(nicknames._CACHE_MAX + 500):
        nicknames.remember("b1", 778, str(i), f"n{i}")

    assert len(nicknames._NICK_CACHE) <= nicknames._CACHE_MAX
