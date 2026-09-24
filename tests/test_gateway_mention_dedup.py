"""同群多 Bot 去重的「@ 定向」回归测试。

线上问题：群里 @ 机器人完全没反应，而私聊正常（去重只作用于群消息）。

根因：``_wait_for_message`` 原先"谁先完成等待谁抢到处理权"，与消息 @ 了谁无关。
若没被 @ 的账号抢到处理权，它在节点链里 ``is_at_me == False`` 后静默跳过，
而被 @ 的账号又被去重吞掉 → 两个账号都不回复。

本文件固定三条不变量：
1. 消息 @ 了某个账号时，处理权必须归它（无论谁先到、message_id 是否一致）；
2. 没被 @ 的账号不得抢走处理权（== 不产生跨账号吞消息）；
3. 没有任何 @ 时维持原语义：同群恰好一个账号处理。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.infrastructure.cache import Cache
from app.infrastructure.onebot.gateway import OneBotGateway

GROUP = 466052056
BOT_A = 3569937952
BOT_B = 3437542570


def _conn(index: int, bot_id: int, groups=(GROUP,)):
    return SimpleNamespace(index=index, bot_id=bot_id, all_group_list=list(groups))


def _event(message_id: int, t: int, *, mention: int | None) -> SimpleNamespace:
    message = []
    if mention is not None:
        message.append(SimpleNamespace(type="at", data={"qq": str(mention)}))
    return SimpleNamespace(
        group=SimpleNamespace(group_id=GROUP),
        message_id=message_id,
        time=t,
        user_id=1901691195,
        event_type="message_group",
        bot_id=0,
        message=message,
        self_id=mention if mention is not None else 0,
    )


def _gateway():
    gw = OneBotGateway(settings=SimpleNamespace(), cache=Cache())
    a = _conn(1, BOT_A)
    b = _conn(0, BOT_B)
    gw.connections[1] = a
    gw.connections[0] = b
    return gw, a, b


async def _both(gw, a, b, *, mention, first, same_mid=True):
    """两个 bot 几乎同时收到同一条群消息，返回 {账号名: 是否放行}。"""
    order = [("A", a, BOT_A), ("B", b, BOT_B)]
    if first == "B":
        order.reverse()
    passed: dict[str, bool] = {}

    async def one(name, conn, delay):
        await asyncio.sleep(delay)
        mid = 111 if same_mid else (111 if name == "A" else 222)
        res = await gw._wait_for_message(_event(mid, 1789000000, mention=mention), conn)
        passed[name] = res is not None

    await asyncio.gather(one(order[0][0], order[0][1], 0.0), one(order[1][0], order[1][1], 0.10))
    return passed


async def test_mentioned_account_wins_regardless_of_arrival_order():
    """@ 了哪个账号，处理权就必须归它——四种时序组合都要成立。"""
    for mention, who in ((BOT_A, "A"), (BOT_B, "B")):
        for first in ("A", "B"):
            for same_mid in (True, False):
                gw, a, b = _gateway()
                passed = await _both(gw, a, b, mention=mention, first=first, same_mid=same_mid)
                assert passed[who] is True, (
                    f"@ {who} 时该账号必须放行（first={first} same_mid={same_mid}）→ {passed}"
                )


async def test_non_mentioned_account_does_not_steal_when_message_ids_align():
    """message_id 一致（同一 OneBot 实例）时，没被 @ 的账号必须让位、不重复响应。"""
    gw, a, b = _gateway()
    passed = await _both(gw, a, b, mention=BOT_A, first="B", same_mid=True)
    assert passed == {"A": True, "B": False}, f"应恰好被 @ 的 A 处理 → {passed}"

    gw2, a2, b2 = _gateway()
    passed2 = await _both(gw2, a2, b2, mention=BOT_B, first="A", same_mid=True)
    assert passed2 == {"A": False, "B": True}, f"应恰好被 @ 的 B 处理 → {passed2}"


async def test_no_mention_keeps_single_handler_semantics():
    """没有 @ 的普通群消息：仍是「同群恰好一个账号处理」，不因本次修复而重复回复。"""
    gw, a, b = _gateway()

    async def one(conn, delay):
        await asyncio.sleep(delay)
        return await gw._wait_for_message(_event(333, 1789000001, mention=None), conn)

    r1, r2 = await asyncio.gather(one(a, 0.0), one(b, 0.10))
    assert sum(1 for r in (r1, r2) if r is not None) == 1, f"应恰好一个处理者 → {r1}, {r2}"


async def test_mentioned_account_still_wins_when_alone_in_group():
    """被 @ 的账号是唯一跟踪该群的连接时，必须直接放行（不回退到旧判断）。"""
    gw = OneBotGateway(settings=SimpleNamespace(), cache=Cache())
    only = _conn(1, BOT_A)
    gw.connections[1] = only
    res = await gw._wait_for_message(_event(444, 1789000002, mention=BOT_A), only)
    assert res == [1]


def test_mentions_bot_matches_at_segments():
    """at 段判据与 app.llm.trigger.is_at_me 一致（对象段与 dict 段都要认）。"""
    obj_event = _event(1, 1, mention=BOT_A)
    assert OneBotGateway._mentions_bot(obj_event, BOT_A) is True
    assert OneBotGateway._mentions_bot(obj_event, BOT_B) is False

    dict_event = SimpleNamespace(
        message=[{"type": "at", "data": {"qq": str(BOT_B)}}], self_id=BOT_B
    )
    assert OneBotGateway._mentions_bot(dict_event, BOT_B) is True

    assert OneBotGateway._mentions_bot(_event(1, 1, mention=None), BOT_A) is False
    assert OneBotGateway._mentions_bot(obj_event, None) is False
