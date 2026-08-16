"""网关去重/消息处理时延回归测试。

背景：原 _eventable_sync 存在 off-by-one，每消息在 Windows 上白白等待 ~1.6s。
修复后 _wait_for_message 必须即时返回。
"""

import asyncio
import json
import time

from app.domain.events import GroupMessageEvent, MessageSegment
from app.infrastructure.cache import Cache
from app.infrastructure.onebot.client import BotConnection
from app.infrastructure.onebot.gateway import OneBotGateway, _message_log_text


class _Settings:
    ws_ping_interval = 30
    ws_ping_timeout = 10
    ws_connect_timeout = 3


class _Conn:
    def __init__(self, index, groups):
        self.index = index
        self.all_group_list = groups
        self.auto_connect = False

    def __getattr__(self, item):
        return None


def _event(msg_id: int, group_id=999, t=None):
    return GroupMessageEvent(
        event_type="message_group", message_type="group",
        time=t if t is not None else 1000 + msg_id,
        user_id=100, self_id=1, message=[],
        group=type("G", (), {"group_id": group_id})(),
    )


def _gateway():
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    gw.connections = {1: _Conn(1, [999])}
    return gw


async def test_wait_for_message_returns_immediately():
    """单 bot 场景：连续快速消息，每次去重判定即时返回（回归：曾等待 ~1.6s）。"""
    gw = _gateway()
    for i in range(10):
        t0 = time.perf_counter()
        idx = await gw._wait_for_message(_event(i), gw.connections[1])
        elapsed = time.perf_counter() - t0
        assert idx is not None, "单 bot 消息不应被去重跳过"
        assert elapsed < 0.1, f"去重耗时异常: {elapsed:.3f}s"


async def test_multi_bot_exactly_one_handler():
    """同群多 bot：恰好一个 bot 被放行处理，其余跳过（_done 标记）。"""
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    gw.connections = {0: _Conn(0, [999]), 1: _Conn(1, [999])}

    # 两个 bot 并发到达同一消息 → 恰好一个返回非 None
    results = await asyncio.gather(
        gw._wait_for_message(_event(1), gw.connections[1]),
        gw._wait_for_message(_event(1), gw.connections[0]),
    )
    handlers = [r for r in results if r is not None]
    assert len(handlers) == 1, f"应恰好一个处理者，实际 {len(handlers)}"


async def test_empty_tracking_passes_through():
    """无 bot 跟踪该群（启动初期/新群）→ 放行而非丢弃消息。"""
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    gw.connections = {1: _Conn(1, [])}  # bot 群列表为空

    idx = await gw._wait_for_message(_event(5), gw.connections[1])
    assert idx is not None, "空跟踪不应丢弃消息"


async def test_timeout_fallback_processes():
    """同群另一 bot 永不到达 → 等待超时后由等待方处理（不再永久丢弃）。"""
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    gw.connections = {0: _Conn(0, [999]), 1: _Conn(1, [999])}

    t0 = time.perf_counter()
    idx = await gw._wait_for_message(_event(9), gw.connections[1])  # bot 0 永不到达
    elapsed = time.perf_counter() - t0
    assert idx is not None
    assert elapsed < 5, f"超时兜底应 <5s，实际 {elapsed:.2f}s"


def _msg_event(segs):
    return GroupMessageEvent(
        event_type="message_group", message_type="group", time=1,
        user_id=100, self_id=1, message=segs,
        group=type("G", (), {"group_id": 999})(),
    )


class _ScriptedWs:
    """脚本化假 websocket：先投递一条私聊消息，等 API 请求发出后回投其响应。"""

    def __init__(self):
        self.sent = []
        self._step = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._step == 0:
            self._step = 1
            return json.dumps({
                "post_type": "message", "message_type": "private",
                "user_id": 100, "self_id": 1, "time": int(time.time()),
                "message": [{"type": "text", "data": {"text": "hi"}}],
            })
        if self._step == 1:
            self._step = 2
            deadline = time.monotonic() + 2
            while not any(p.get("action") == "send_private_msg" for p in self.sent):
                if time.monotonic() > deadline:
                    break
                await asyncio.sleep(0.01)
            echo = next((p["echo"] for p in self.sent if p.get("action") == "send_private_msg"), None)
            if echo:
                return json.dumps({"status": "ok", "retcode": 0, "echo": echo, "data": {"message_id": 9}})
            return ""
        raise StopAsyncIteration

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def close(self):
        pass


async def test_dispatch_does_not_block_api_response():
    """回归：派发在 worker 中运行，模块内 await API 响应时接收循环仍能消费响应。

    旧实现把 _dispatch 内联 await 在接收循环里 → 模块 await send_private_msg 的 echo 时
    接收循环被阻塞、无法消费响应 → 请求/响应自锁 → 10s 超时。修复后 2s 内必须成功。
    """
    gw = OneBotGateway(settings=_Settings(), cache=Cache())

    async def _noop_login(conn):
        return False

    gw._get_login_info = _noop_login
    gw._get_login_groups = _noop_login

    ws = _ScriptedWs()
    conn = BotConnection(websocket=ws, bot_id=123)
    conn.index = 1
    gw.connections = {1: conn}

    reply_ok = asyncio.Event()

    async def handler(event):
        resp = await conn.send_private_msg(100, "hi")
        if resp and resp.get("status") == "ok":
            reply_ok.set()

    gw.dispatch_handler = handler
    task = asyncio.create_task(gw._bot_server(1))
    try:
        await asyncio.wait_for(reply_ok.wait(), 2)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert reply_ok.is_set(), "模块 await API 响应应在 2s 内成功（接收循环未被派发阻塞）"
    assert any(p.get("action") == "send_private_msg" for p in ws.sent), "请求应已正常发送"


class _FailedRespWs:
    """脚本化假 websocket：私聊消息后回投 status=failed 的 API 失败响应。"""

    def __init__(self):
        self.sent = []
        self._step = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._step == 0:
            self._step = 1
            return json.dumps({
                "post_type": "message", "message_type": "private",
                "user_id": 100, "self_id": 1, "time": int(time.time()),
                "message": [{"type": "text", "data": {"text": "hi"}}],
            })
        if self._step == 1:
            self._step = 2
            deadline = time.monotonic() + 2
            while not any(p.get("action") == "send_private_msg" for p in self.sent):
                if time.monotonic() > deadline:
                    break
                await asyncio.sleep(0.01)
            echo = next((p["echo"] for p in self.sent if p.get("action") == "send_private_msg"), None)
            if echo:
                return json.dumps({"status": "failed", "retcode": 110,
                                   "echo": echo, "message": "发送失败"})
            return ""
        raise StopAsyncIteration

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def close(self):
        pass


async def test_failed_api_response_consumed_not_timeout():
    """回归：status=failed 的 API 响应应被 handle_api_response 消费并返回给调用方，
    而不是被误判为连接失败 break 导致 10s 超时。"""
    gw = OneBotGateway(settings=_Settings(), cache=Cache())

    async def _noop_login(conn):
        return False

    gw._get_login_info = _noop_login
    gw._get_login_groups = _noop_login

    ws = _FailedRespWs()
    conn = BotConnection(websocket=ws, bot_id=123)
    conn.index = 1
    gw.connections = {1: conn}

    got = asyncio.Event()
    result = {}

    async def handler(event):
        resp = await conn.send_private_msg(100, "hi")
        result["resp"] = resp
        got.set()

    gw.dispatch_handler = handler
    task = asyncio.create_task(gw._bot_server(1))
    try:
        await asyncio.wait_for(got.wait(), 2)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert result.get("resp") is not None, "失败响应应在 2s 内返回（而非 10s 超时）"
    assert result["resp"].get("status") == "failed"


def test_message_log_text_summary():
    """日志摘要：image/video 长字段简化为 [type]，at/reply 短字段带值，纯 image 可显示。"""
    # 纯文字
    assert _message_log_text(_msg_event([MessageSegment("text", {"text": "你好"})])) == "你好"
    # 纯图片（此前不显示）
    assert _message_log_text(_msg_event([MessageSegment("image", {"file": "a.jpg"})])) == "[image]"
    # 视频
    assert _message_log_text(_msg_event([MessageSegment("video", {"file": "v.mp4"})])) == "[video]"
    # at / reply 短字段带值
    assert _message_log_text(_msg_event([MessageSegment("at", {"qq": "456"})])) == "[at:456]"
    assert _message_log_text(_msg_event([MessageSegment("reply", {"id": "1001"})])) == "[reply:1001]"
    # 混合
    mixed = _msg_event([
        MessageSegment("text", {"text": "看"}),
        MessageSegment("image", {"file": "a.jpg"}),
        MessageSegment("at", {"qq": "456"}),
    ])
    assert _message_log_text(mixed) == "看[image][at:456]"


def test_message_log_text_other_types_default_to_abbrev():
    """record/face/json/forward 等其它类型一律按长字段略缩为 [type]。"""
    cases = [
        MessageSegment("record", {"file": "voice.amr"}),
        MessageSegment("face", {"id": 123}),
        MessageSegment("json", {"data": '{"app":"com.tencent"}'}),
        MessageSegment("forward", {"id": "1002"}),
        MessageSegment("node", {"name": "x", "uin": 1, "content": []}),
        MessageSegment("music", {"type": "qq", "id": "1"}),
    ]
    for seg in cases:
        assert _message_log_text(_msg_event([seg])) == f"[{seg.type}]", seg.type
