"""OneBot API 超时分级回归测试。

背景（远端日志 2026-09-19 18:57）：B站解析的 720P 合并转发在 NapCat 侧要先上传再组装，
耗时 > 10s，而所有 API 共用固定 10s 超时，于是出现

    API(->) send_group_forward_msg → API 超时 (10s) → [发送->] 失败: 超时
    ...14s 后：未找到对应请求 echo=5dcdbaa5

即「发送其实可能已成功，调用方却收到失败，且迟到响应被静默丢弃」。
本文件锁住三件事：分级表取值、显式覆盖优先级、超时后迟到响应的留痕。
"""

from __future__ import annotations

import asyncio
import json

from app.infrastructure.onebot.client import (
    API_TIMEOUT_SECONDS,
    DEFAULT_API_TIMEOUT,
    BotConnection,
    _callable_accepts_timeout,
    resolve_api_timeout,
)
from app.nodes.outbound import OutboundPipeline, SendNode


class _SilentWS:
    """只记录发送内容、从不回响应的 websocket 替身（模拟服务端卡住）。"""

    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


# ---------- 分级表 ----------


def test_slow_actions_get_longer_timeouts():
    """会传文件/取大列表的 action 必须比默认更宽松。"""
    for action in (
        "send_forward_msg",
        "send_group_forward_msg",
        "send_private_forward_msg",
        "get_forward_msg",
        "get_image",
        "get_record",
        "get_group_member_list",
        "get_group_msg_history",
        "get_friend_msg_history",
        "clean_cache",
    ):
        assert API_TIMEOUT_SECONDS[action] > DEFAULT_API_TIMEOUT, action


def test_forward_send_gets_at_least_60s():
    """回归：720P 单节点转发实测 > 10s，必须给足组装/上传时间。"""
    assert resolve_api_timeout("send_group_forward_msg") >= 60
    assert resolve_api_timeout("send_forward_msg") >= 60


def test_explicit_timeout_wins_and_invalid_falls_back():
    assert resolve_api_timeout("send_group_msg", 3) == 3
    assert resolve_api_timeout("send_group_forward_msg", 5) == 5
    assert resolve_api_timeout("send_group_forward_msg", None) == API_TIMEOUT_SECONDS["send_group_forward_msg"]
    # 0 / 负数 / 脏值视为未指定，避免「立刻超时」的误用
    assert resolve_api_timeout("send_group_msg", 0) == DEFAULT_API_TIMEOUT
    assert resolve_api_timeout("send_group_msg", -1) == DEFAULT_API_TIMEOUT
    assert resolve_api_timeout("send_group_msg", "abc") == DEFAULT_API_TIMEOUT
    # 表外 action 回退默认
    assert resolve_api_timeout("unknown_action") == DEFAULT_API_TIMEOUT


# ---------- 超时后的行为 ----------


async def test_timeout_records_orphan_echo_and_late_response_is_not_matched():
    """超时后 echo 记为孤儿；迟到响应只留痕，不再配对给已放弃的请求。"""
    conn = BotConnection(websocket=_SilentWS())
    conn.index = 0

    # bot_id 为空时不触发用户日志分支，避免测试噪音；这里只验证请求/留痕逻辑
    resp = await conn._direct_send("send_group_forward_msg", {"group_id": 1, "messages": []}, 0.05)
    assert resp is None
    assert len(conn._orphan_echoes) == 1

    echo_id = conn._orphan_echoes[0]
    late = {"status": "ok", "echo": echo_id, "action": "send_group_forward_msg"}
    # 迟到响应：不消费（返回 False），不抛异常
    assert conn.handle_api_response(late) is False


async def test_normal_response_still_matched():
    """未超时的请求仍能正常配对（超时改造不能破坏主路径）。"""
    conn = BotConnection(websocket=_SilentWS())
    conn.index = 0

    task = asyncio.create_task(conn._direct_send("get_login_info", {}, 1))
    await asyncio.sleep(0.01)
    echo_id = next(iter(conn._pending))
    assert conn.handle_api_response({"status": "ok", "echo": echo_id, "data": {"user_id": 1}}) is True
    resp = await task
    assert resp["data"]["user_id"] == 1
    assert list(conn._orphan_echoes) == []


# ---------- 超时沿出站链下传 ----------


def test_hook_signature_detection():
    async def legacy(action, params):
        return None

    async def modern(action, params, timeout=None):
        return None

    async def varargs(*args):
        return None

    assert _callable_accepts_timeout(legacy) is False
    assert _callable_accepts_timeout(modern) is True
    assert _callable_accepts_timeout(varargs) is True


async def test_timeout_reaches_direct_send_through_outbound_pipeline():
    """出站链必须把 timeout 带到 _direct_send，否则模块无法为慢 API 单独放宽。"""
    seen: list = []

    class _Bot:
        async def _direct_send(self, action, params, timeout=None):
            seen.append((action, timeout))
            return {"status": "ok"}

    pipe = OutboundPipeline([SendNode()])
    await pipe.run(_Bot(), "send_group_forward_msg", {"group_id": 1}, 60)
    assert seen == [("send_group_forward_msg", 60)]


async def test_bot_connection_passes_timeout_to_legacy_two_arg_hook():
    """旧式两参 hook 仍可用（不因超时改造而抛错）。"""
    seen: list = []

    class _Hook:
        def __init__(self):
            self.calls = 0

        async def __call__(self, action, params):
            self.calls += 1
            seen.append(action)
            return {"status": "ok"}

    conn = BotConnection()
    conn.index = 0
    conn.outbound_hook = _Hook()
    resp = await conn._send("send_group_msg", {"group_id": 1, "message": "hi"})
    assert resp == {"status": "ok"}
    assert seen == ["send_group_msg"]
