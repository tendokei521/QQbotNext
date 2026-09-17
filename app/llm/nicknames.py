"""会话内昵称反查（id → 昵称）的共享实现。

背景：QQ 消息里到处是"骨架"——``@123``、``(123)`` 这类 id，模型看不到"123 是谁"。
把骨架补成血肉（``@三哥(123)``）需要 ``get_group_member_info`` 反查，而这条管道此前
**只存在于触发消息的 @ 解析里**（``enhance._collect_at_info``），群聊背景块里的 @ 仍是
裸 ``@123``（``group_context._segment_text``）——恰恰背景块才是"群里其他人在聊什么"的
唯一可见窗口。

本模块把该能力抽成共享实现，供三处复用：
- ``enhance._collect_at_info``：当前触发消息里 @ 的对象；
- ``group_context.fetch_group_online_history``：群聊背景块 / get_chat_history 的 @ 对象；
- 模型主动按需展开（按 id 查人）的场景。

缓存键为 ``{bot_id}:{group_id}:{qq}``，三处共用：**触发消息里已经查过的昵称，
背景块渲染时直接命中，不会为同一个人重复请求 OneBot**。
失败不写缓存（允许下次重试），但必须留痕（STYLE §7：异常永不裸吞）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterable

from app.llm import logger

# bot_id:group_id:qq -> 昵称（含群名片优先）
_NICK_CACHE: dict[str, str] = {}
# 缓存上限：超过后按插入顺序淘汰（昵称是低价值易失数据，宁可再生也不涨内存）
_CACHE_MAX = 2000


def cache_key(bot_id: Any, group_id: Any, qq: Any) -> str:
    return f"{bot_id}:{group_id}:{qq}"


def cached_nickname(bot_id: Any, group_id: Any, qq: Any) -> str:
    """只读缓存；未命中返回空串。"""
    return _NICK_CACHE.get(cache_key(bot_id, group_id, qq), "")


def remember(bot_id: Any, group_id: Any, qq: Any, nickname: str) -> None:
    """写入缓存（空昵称不写：避免把"取不到"固化成永远取不到）。"""
    if not nickname:
        return
    if len(_NICK_CACHE) >= _CACHE_MAX:
        # 简单淘汰：丢掉最早插入的一批，避免无界增长
        for key in list(_NICK_CACHE)[: _CACHE_MAX // 4]:
            _NICK_CACHE.pop(key, None)
    _NICK_CACHE[cache_key(bot_id, group_id, qq)] = nickname


def clear_cache() -> None:
    """清空缓存（测试与热重载使用）。"""
    _NICK_CACHE.clear()


def _member_data(resp: Any) -> dict:
    """从 OneBot ``get_group_member_info`` 响应里取 data（兼容已解包形态）。"""
    if not isinstance(resp, dict):
        return {}
    data = resp.get("data")
    if isinstance(data, dict):
        return data
    # 兼容调用方已解包的 {"user_id": ...}
    return resp if "user_id" in resp or "nickname" in resp else {}


async def fetch_nickname(bot: Any, group_id: Any, qq: Any, *, bot_id: Any = "") -> str:
    """反查单个群成员的昵称（群名片优先），失败/非法输入返回空串。"""
    if bot is None or group_id in (None, "") or qq in (None, ""):
        return ""
    try:
        user_id = int(str(qq).strip())
    except (TypeError, ValueError):
        return ""

    hit = cached_nickname(bot_id, group_id, qq)
    if hit:
        return hit

    try:
        resp = await bot.get_group_member_info(group_id=int(group_id), user_id=user_id)
    except Exception as e:
        # 反查失败只影响"昵称展示"，不能让整轮渲染失败；留痕便于排查 API/权限异常
        logger.debug(f"[Nickname] 获取群成员昵称失败（group={group_id}, qq={qq}）: {e}")
        return ""
    if isinstance(resp, dict) and resp.get("status") not in (None, "ok"):
        logger.debug(
            f"[Nickname] 获取群成员昵称被拒（group={group_id}, qq={qq}）: "
            f"retcode={resp.get('retcode')} {resp.get('message')}"
        )
        return ""
    data = _member_data(resp)
    nickname = str(data.get("card") or data.get("nickname") or "").strip()
    if nickname:
        remember(bot_id, group_id, qq, nickname)
    return nickname


async def resolve_nicknames(
    bot: Any,
    group_id: Any,
    qqs: Iterable[Any],
    *,
    bot_id: Any = "",
) -> dict[str, str]:
    """批量反查昵称，返回 ``{qq 字符串: 昵称}``（取不到的 qq 不出现在结果里）。

    并发展开：背景块里一次可能有多个人被 @，串行反查会把渲染延迟叠加起来。
    """
    wanted: list[str] = []
    for qq in qqs or ():
        text = str(qq or "").strip()
        if text and text not in wanted:
            wanted.append(text)
    if not wanted:
        return {}

    resolved: dict[str, str] = {}
    pending: list[str] = []
    for qq in wanted:
        hit = cached_nickname(bot_id, group_id, qq)
        if hit:
            resolved[qq] = hit
        else:
            pending.append(qq)

    if pending:
        results = await asyncio.gather(
            *[fetch_nickname(bot, group_id, qq, bot_id=bot_id) for qq in pending]
        )
        for qq, name in zip(pending, results):
            if name:
                resolved[qq] = name
    return resolved
