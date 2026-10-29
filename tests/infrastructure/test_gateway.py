"""网关消息处理回归测试。

背景：网关曾做「同群多 Bot 同步去重」——一条群消息只放行一个账号进节点链，
其余账号直接 return。这与「LLM 每账号独立」冲突（消息会被别的账号吞掉），
现已移除：每个账号各自派发，是否回复由各自 Agent 的触发规则（@ / 关键词）判定。
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
    def __init__(self, account_id, groups):
        self.account_id = account_id
        self.account_name = f"账号{account_id}"
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


class _NullLogger:
    """吞掉日志的最小 logger 桩（_process_event 需要 bot/recv 两个 logger）。"""

    def debug(self, *_a, **_k):
        pass

    info = debug

    def prefix(self, *_a, **_k):
        return self

    def add_info(self, *_a, **_k):
        return self


async def test_each_account_dispatches_group_message_independently():
    """回归：同群多账号各账号独立派发，不存在跨账号吞消息（去重）。

    线上表现：同群两个账号，只有一个账号能进节点链；另一个账号的消息被「同群去重」
    直接吞掉（历史上还出现过被 @ 的账号反而被吞 → 群里 @ 机器人完全没反应）。
    用户要求 LLM 按账号独立，故网关切掉去重：每个账号自己收到的消息都要派发，
    回不回由各账号 Agent 的触发规则决定。
    """
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    conn0, conn1 = _Conn("a", [999]), _Conn("b", [999])
    gw.connections = {"a": conn0, "b": conn1}

    seen: list[tuple] = []

    async def handler(event):
        seen.append((event.account_id, event.self_id))

    gw.dispatch_handler = handler

    for offset, conn in enumerate((conn0, conn1)):
        event = _event(1)
        event.account_id = conn.account_id
        event.self_id = 1000 + offset
        await gw._process_event(event, conn, _NullLogger(), _NullLogger())

    assert seen == [("a", 1000), ("b", 1001)], f"同群两个账号都必须各自派发 → {seen}"


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
    conn.account_id = "main"
    conn.account_name = "主号"
    gw.connections = {"main": conn}

    reply_ok = asyncio.Event()

    async def handler(event):
        resp = await conn.send_private_msg(100, "hi")
        if resp and resp.get("status") == "ok":
            reply_ok.set()

    gw.dispatch_handler = handler
    task = asyncio.create_task(gw._bot_server("main"))
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
    conn.account_id = "main"
    conn.account_name = "主号"
    gw.connections = {"main": conn}

    got = asyncio.Event()
    result = {}

    async def handler(event):
        resp = await conn.send_private_msg(100, "hi")
        result["resp"] = resp
        got.set()

    gw.dispatch_handler = handler
    task = asyncio.create_task(gw._bot_server("main"))
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


class _AccountConn:
    """最小 Bot 桩：只提供登录信息 + 账号快照所需字段。"""

    def __init__(self, account_id: str):
        self.account_id = account_id
        self.account_name = "主号"
        self.bot_id = 0
        self.login_info = {}
        self.status = "connecting"
        self.ws_url = "ws://127.0.0.1:1"
        self.owner_id = None
        self.auto_connect = False
        self.reconnect_attempts = 0
        self.last_error = None

    async def get_login_info(self):
        return {"status": "ok", "retcode": 0, "data": {"user_id": 987654, "nickname": "小明"}}


async def test_login_records_last_account_snapshot():
    """登录成功 → 写入账号快照；断开后 last_account / account 仍可回退展示。"""
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    conn = _AccountConn("main")
    gw.connections = {"main": conn}

    saved: dict = {}

    async def _saver(account_id, user_id, nickname):
        saved.update(account_id=account_id, user_id=user_id, nickname=nickname)
        return True

    gw.account_saver = _saver
    gw.account_lookup = lambda: {
        "main": {"user_id": str(saved.get("user_id")), "nickname": saved.get("nickname", ""),
                 "last_login_at": 1}
    }

    assert await gw._get_login_info(conn) is True
    assert (saved["account_id"], saved["user_id"], saved["nickname"]) == ("main", 987654, "小明")

    info = gw.get_bots_info()[0]
    assert info["login_info"]["nickname"] == "小明"
    assert info["last_account"]["nickname"] == "小明"
    assert info["account"]["user_id"] == 987654

    # 断开：运行时登录态清空，但快照仍在 → 前端可回退展示上次账号
    gw._reset_conn_state(conn)
    conn.status = "disconnected"
    info = gw.get_bots_info()[0]
    assert info["login_info"] == {}
    assert info["last_account"]["nickname"] == "小明"
    assert info["account"]["nickname"] == "小明"


async def test_login_account_saver_failure_does_not_break_login():
    """快照写入失败不得影响登录主流程（登录回调照常执行）。"""
    gw = OneBotGateway(settings=_Settings(), cache=Cache())
    conn = _AccountConn("main")
    gw.connections = {"main": conn}

    async def _boom(account_id, user_id, nickname):  # noqa: ARG001
        raise RuntimeError("db down")

    gw.account_saver = _boom
    logged_in = asyncio.Event()

    async def _login(conn_):
        logged_in.set()

    gw.login_handler = _login

    assert await gw._get_login_info(conn) is True
    assert logged_in.is_set(), "登录回调必须照常执行"
    assert conn.bot_id == 987654
