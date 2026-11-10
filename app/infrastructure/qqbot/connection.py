"""官方机器人 IBot 实现：一个已连接的官方账号。

职责边界：

- 收发：把框架的 ``Message`` 段压成官方 ``content``，发送时带被动 ``msg_id/msg_seq``；
- 能力：只实现官方 OpenAPI 真有的动作；其余方法由 ``_make_unsupported`` 统一抛
  :class:`CapabilityUnavailableError`（工具状态机与 API 层会先拦，这里是兜底）；
- 状态：登录信息、连接状态、学习到的群（官方没有群列表接口，只能从事件学习）。

第一版只支持**文本**（图片/语音/文件留到 Phase 2：需要先上传换 ``file_info``）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from app.core.logger import logger
from app.domain.bot import IBot
from app.domain.message import Message, MessageSegment, SegmentLike
from app.domain.protocol import PROTOCOL_QQBOT, CapabilityUnavailableError, unsupported_message

from .api import QqBotAPI, QqBotAPIError
from .inbox import Inbox
from .media import MediaError, MediaUploader
from .ws import QqBotWSClient


def _ok(data: Any = None) -> dict:
    return {"status": "ok", "retcode": 0, "data": data if data is not None else {}}


def _failed(code: Any, message: str) -> dict:
    return {"status": "failed", "retcode": code, "message": str(message or "")}


def _as_message(message: SegmentLike) -> Message:
    if isinstance(message, Message):
        return message
    if isinstance(message, str):
        return Message.from_text(message)
    if isinstance(message, MessageSegment):
        return Message([message])
    if isinstance(message, list):
        return Message(message)
    return Message.from_text(str(message))


class QqBotConnection(IBot):
    """官方账号连接（事件走 WS，发送走 HTTPS OpenAPI）。"""

    protocol = PROTOCOL_QQBOT
    ws_url = ""  # 官方账号没有用户填写的协议端地址（保留字段兼容 introspect/gateway）

    def __init__(
        self,
        *,
        account_id: str,
        account_name: str = "",
        app_id: str = "",
        app_secret: str = "",
        transport: str = "ws",
        auto_connect: bool = False,
        log: Any = None,
    ) -> None:
        self.account_id = str(account_id)
        self.account_name = account_name or self.account_id
        self.app_id = str(app_id or "")
        self.app_secret = str(app_secret or "")
        self.transport = str(transport or "ws")
        self.auto_connect = bool(auto_connect)
        self.log = log or logger

        self.status = "disconnected"
        self.last_error: str | None = None
        self.reconnect_attempts = 0
        self.suppress_auto_reconnect = False
        self.connect_epoch = 0
        self._disconnecting = False
        self._last_notified_status: str | None = None

        self.bot_id: str | None = None
        self.login_info: dict = {}
        self.login_retrieved = False
        self.login_event = asyncio.Event()

        #: 官方被动回复模型：默认不允许主动消息（配额/用户开关不透明，先保守）
        self.allow_active_message = False

        self.inbox = Inbox()
        self.api = QqBotAPI(self.app_id, self.app_secret, session=None, log=self.log)
        self.ws = QqBotWSClient(self.api, log=self.log)
        self.uploader = MediaUploader(self.api, log=self.log)
        self._session: aiohttp.ClientSession | None = None
        self._closed = False

        # 学习到的群（官方不提供「机器人已加入群列表」接口）
        self.learned_groups: dict[str, str] = {}
        self.all_group_list: list = []
        self.all_group_list_info: list = []
        #: 由 bootstrap 注入：从本地 known_groups 缓存补群列表
        self.known_groups_provider: Callable[[], list[dict]] | None = None

        # 发出的消息 id → (scene, target, ts)：官方撤回按「场景 + 消息 id」定位
        self._sent_messages: dict[str, tuple[str, str, float]] = {}
        # 钩子（与 OneBot 连接同形，出站链/插件钩子不用区分协议）
        self.outbound_hook = None
        self.send_hook_registry = None
        self.before_send_hook_registry = None
        self.api_hook_registry = None

    # ---------- 展示 / 身份 ----------
    @property
    def label(self) -> str:
        return self.account_name or self.account_id or "?"

    @property
    def owner_id(self) -> None:  # type: ignore[override]
        """账号级 owner 已废弃；保留 None 让第三方插件不崩。"""
        return None

    @owner_id.setter
    def owner_id(self, value) -> None:  # noqa: ANN001
        return None

    def coerce_id(self, value: Any) -> str:
        """官方 ID 一律字符串：不能把 openid 转成数字（会丢字符）。"""
        return "" if value is None else str(value)

    def supports(self, action: str) -> bool:
        return super().supports(action)

    # ---------- 生命周期 ----------
    async def open(self) -> None:
        """建立 HTTP 会话与 WS 连接（不等待 READY；READY 由网关处理）。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self.api._session = self._session
            self.api.tokens._session = self._session
        await self.ws.connect(resume=bool(self.ws.session_id))

    async def close(self) -> None:
        self._closed = True
        try:
            await self.ws.close()
        finally:
            if self._session is not None and not self._session.closed:
                await self._session.close()
            self._session = None

    async def events(self):
        return self.ws.events()

    def handle_ready(self, data: dict) -> None:
        """READY：记录登录信息并唤醒等待方。"""
        user = (data or {}).get("user") or {}
        self.bot_id = str(user.get("id") or "")
        self.login_info = {
            "user_id": self.bot_id,
            "nickname": str(user.get("username") or ""),
            "bot": True,
            "app_id": self.app_id,
        }
        self.login_retrieved = True
        self.login_event.set()

    async def wait_login(self, timeout: float = 10.0) -> bool:
        if self.login_retrieved:
            return True
        try:
            await asyncio.wait_for(self.login_event.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return bool(self.login_retrieved)

    # ---------- 群学习 ----------
    def learn_group(self, group_openid: str) -> None:
        gid = str(group_openid or "").strip()
        if not gid:
            return
        if gid not in self.learned_groups:
            self.learned_groups[gid] = ""
        self._refresh_group_lists()

    def forget_group(self, group_openid: str) -> None:
        gid = str(group_openid or "").strip()
        self.learned_groups.pop(gid, None)
        self._refresh_group_lists()

    def _refresh_group_lists(self) -> None:
        info: dict[str, dict] = {}
        for row in self.known_groups_provider() if self.known_groups_provider else []:
            gid = str((row or {}).get("group_id") or "").strip()
            if gid:
                info[gid] = {"group_id": gid, "group_name": str(row.get("name") or ""), "in_group": bool(row.get("in_group", True))}
        for gid in self.learned_groups:
            info.setdefault(gid, {"group_id": gid, "group_name": "", "in_group": True})
        self.all_group_list_info = list(info.values())
        self.all_group_list = [row["group_id"] for row in self.all_group_list_info]
        # 同步回 conn 缓存（GroupService 读取）
        self.learned_groups.update({row["group_id"]: row.get("group_name") or "" for row in self.all_group_list_info})

    # ---------- 发送 ----------
    def _render_message(self, message: SegmentLike) -> tuple[str, list[MessageSegment]]:
        msg = _as_message(message)
        parts: list[str] = []
        media: list[MessageSegment] = []
        for seg in msg.segments:
            if seg.type == "text":
                parts.append(str(seg.data.get("text") or ""))
            elif seg.type == "at":
                target = str(seg.data.get("qq") or "")
                parts.append("<@all>" if target == "all" else f"<@{target}>")
            elif seg.type == "reply":
                continue  # 被动回复已经用 msg_id 表达，正文不重复引用标记
            else:
                media.append(seg)
        return "".join(parts).strip(), media

    async def _send_payload(self, scene: str, target: str, payload: dict[str, Any]) -> dict:
        """统一附被动凭证并发一条；窗口外按配置决定是否允许主动消息。"""
        passive = self.inbox.next_passive(scene, target)
        if passive is not None:
            payload["msg_id"], payload["msg_seq"] = passive
        elif not self.allow_active_message:
            return _failed(
                "NO_PASSIVE_WINDOW",
                "被动回复窗口已过/次数用尽（群 5 分钟 5 次，单聊 60 分钟 4 次），本条未发送",
            )
        try:
            if scene == "group":
                data = await self.api.send_group_message(target, payload)
            else:
                data = await self.api.send_c2c_message(target, payload)
        except QqBotAPIError as e:
            if e.passive_expired:
                self.inbox.forget(scene, target)
            return _failed(e.code, f"{e.message}（code={e.code}）")
        message_id = str((data or {}).get("id") or "")
        if message_id:
            self._sent_messages[message_id] = (scene, target, time.time())
        return _ok(data)

    async def _send(self, scene: str, target: str, message: SegmentLike) -> dict:
        content, media = self._render_message(message)
        if not content and not media:
            return _failed("EMPTY_MESSAGE", "消息内容为空，未发送")

        if media:
            # 官方一条消息只能带一个 media：文本作为首条媒体的 content（群聊必填），
            # 其余媒体用空格占位。每个媒体各占一次被动回复额度。
            last: dict = _ok({})
            for index, seg in enumerate(media[:5]):
                try:
                    file_info = await self.uploader.resolve(scene, target, seg)
                except MediaError as e:
                    return _failed("MEDIA_UPLOAD_FAILED", str(e))
                payload: dict[str, Any] = {
                    "msg_type": 7,
                    "media": {"file_info": file_info},
                }
                caption = content if index == 0 else ""
                if scene == "group":
                    payload["content"] = caption or " "
                elif caption:
                    payload["content"] = caption
                last = await self._send_payload(scene, target, payload)
                if last.get("status") != "ok":
                    return last
            return last

        return await self._send_payload(scene, target, {"content": content, "msg_type": 0})

    async def send_group_msg(self, group_id: Any, message: SegmentLike, auto_escape: bool = False) -> dict:
        return await self._send("group", str(group_id), message)

    async def send_private_msg(self, user_id: Any, message: SegmentLike, auto_escape: bool = False) -> dict:
        return await self._send("private", str(user_id), message)

    async def send_msg(
        self,
        message_type: str,
        message: SegmentLike,
        user_id: Any = None,
        group_id: Any = None,
        auto_escape: bool = False,
    ) -> dict:
        if str(message_type) == "group" or group_id not in (None, ""):
            return await self.send_group_msg(str(group_id or ""), message)
        return await self.send_private_msg(str(user_id or ""), message)

    async def delete_msg(self, message_id: Any) -> dict:
        mid = str(message_id or "")
        info = self._sent_messages.get(mid)
        if not info:
            return _failed("UNKNOWN_MESSAGE", "只能撤回本账号刚发出的消息（未找到发送记录）")
        scene, target, _ts = info
        try:
            if scene == "group":
                data = await self.api.delete_group_message(target, mid)
            else:
                data = await self.api.delete_c2c_message(target, mid)
        except QqBotAPIError as e:
            return _failed(e.code, e.message)
        self._sent_messages.pop(mid, None)
        return _ok(data)

    async def get_login_info(self) -> dict:
        return _ok({"user_id": self.bot_id, "nickname": self.login_info.get("nickname", "")})

    async def get_group_list(self) -> dict:
        """官方没有群列表接口：返回从事件/本地缓存学到的群（可能不全）。"""
        self._refresh_group_lists()
        return _ok([
            {"group_id": row["group_id"], "group_name": row.get("group_name") or row["group_id"]}
            for row in self.all_group_list_info
            if row.get("in_group", True)
        ])

    async def get_status(self) -> dict:
        return _ok({"online": self.status == "connected", "good": self.status == "connected"})

    # ---------- 群信息 / 群成员 ----------
    async def get_group_info(self, group_id: Any, no_cache: bool = False) -> dict:
        """官方群基本信息（需要「群信息」接口权限；无权限时返回失败并由调用方降级）。"""
        try:
            data = await self.api.group_info(str(group_id))
        except QqBotAPIError as e:
            return _failed(e.code, e.message)
        name = str((data or {}).get("group_name") or "")
        if name:
            self.learned_groups[str(group_id)] = name
            self._refresh_group_lists()
        return _ok({
            "group_id": str(group_id),
            "group_name": name,
            "member_count": int((data or {}).get("group_member_num") or 0),
            "max_member_count": 0,
        })

    async def get_group_member_list(self, group_id: Any) -> dict:
        """分页拉取群成员（最多 20 页）；无权限（11253）返回失败。"""
        members: list[dict] = []
        cursor = ""
        try:
            for _ in range(20):
                data = await self.api.group_members(str(group_id), cursor)
                for row in (data or {}).get("members") or []:
                    if not isinstance(row, dict):
                        continue
                    openid = str(row.get("member_openid") or "")
                    if not openid:
                        continue
                    members.append({
                        "user_id": openid,
                        "nickname": str(row.get("username") or ""),
                        "card": str(row.get("username") or ""),
                        "role": str(row.get("member_role") or "member"),
                    })
                cursor = str((data or {}).get("next_cursor") or "")
                if not cursor:
                    break
        except QqBotAPIError as e:
            return _failed(e.code, e.message)
        return _ok(members)

    async def set_group_ban(self, group_id: Any, user_id: Any, duration: int = 30 * 60) -> dict:
        """禁言（需要机器人是群管理员）。duration 秒；<=0 视为解除禁言。"""
        try:
            if int(duration or 0) <= 0:
                members = [{"op": "del", "member_openid": str(user_id), "mute_expire_at": ""}]
            else:
                expire = (datetime.now(timezone.utc) + timedelta(seconds=int(duration))).isoformat()
                members = [{"op": "add", "member_openid": str(user_id), "mute_expire_at": expire}]
            await self.api.restrict_chat(str(group_id), members)
        except QqBotAPIError as e:
            return _failed(e.code, e.message)
        return _ok({})

    async def set_group_kick(
        self, group_id: Any, user_id: Any, reject_add_request: bool = False
    ) -> dict:
        """踢人（批量移除接口；需要内邀权限或管理员身份，失败时返回官方错误）。"""
        try:
            data = await self.api.batch_remove_members(
                str(group_id), [str(user_id)], blacklist=bool(reject_add_request)
            )
        except QqBotAPIError as e:
            return _failed(e.code, e.message)
        return _ok(data)

    # ---------- OneBot action 兼容层 ----------
    async def call_api(self, action: str, params: dict | None = None, timeout: float | int | None = None) -> dict | None:
        params = dict(params or {})
        try:
            if action == "send_group_msg":
                return await self.send_group_msg(params.get("group_id"), params.get("message"))
            if action == "send_private_msg":
                return await self.send_private_msg(params.get("user_id"), params.get("message"))
            if action == "send_msg":
                return await self.send_msg(
                    params.get("message_type"), params.get("message"),
                    user_id=params.get("user_id"), group_id=params.get("group_id"),
                )
            if action == "delete_msg":
                return await self.delete_msg(params.get("message_id"))
            if action == "get_login_info":
                return await self.get_login_info()
            if action == "get_group_list":
                return await self.get_group_list()
            if action == "get_group_info":
                return await self.get_group_info(params.get("group_id"))
            if action == "get_group_member_list":
                return await self.get_group_member_list(params.get("group_id"))
            if action == "set_group_ban":
                return await self.set_group_ban(
                    params.get("group_id"), params.get("user_id"), int(params.get("duration") or 0)
                )
            if action == "set_group_kick":
                return await self.set_group_kick(
                    params.get("group_id"), params.get("user_id"),
                    bool(params.get("reject_add_request", False)),
                )
            if action == "get_status":
                return await self.get_status()
        except CapabilityUnavailableError as e:
            return _failed("capability_unsupported", str(e))
        return _failed("capability_unsupported", unsupported_message(self.protocol, action))

    async def _direct_send(self, action: str, params: dict | None = None, timeout_sec: float | int | None = None) -> dict | None:
        """出站节点链的末端（与 OneBot 连接同形）：不支持的 action 返回失败而不是抛。"""
        return await self.call_api(action, params, timeout_sec)

    # ---------- 运行期（网关调用） ----------
    async def ensure_session(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self.api._session = self._session
            self.api.tokens._session = self._session


# ==================== 未支持方法的统一兜底 ====================
#
# IBot 的抽象方法覆盖整个 OneBot API（戳一戳/群文件/合并转发/历史消息…）。
# 官方账号不实现它们：这里按「抽象方法名 = OneBot action 名」批量生成抛错实现，
# 既让类可实例化，又保证绕过工具状态机直接调用时得到明确的能力错误。

def _make_unsupported(name: str):
    async def _method(self, *args: Any, **kwargs: Any) -> dict:
        raise CapabilityUnavailableError(self.protocol, name)

    _method.__name__ = name
    _method.__doc__ = f"官方 QQ 机器人不支持 {name}（见 app.domain.protocol）"
    return _method


for _abstract_name in list(IBot.__abstractmethods__):  # type: ignore[attr-defined]
    if _abstract_name not in QqBotConnection.__dict__:
        setattr(QqBotConnection, _abstract_name, _make_unsupported(_abstract_name))
QqBotConnection.__abstractmethods__ = frozenset()
