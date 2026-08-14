"""OneBot API 客户端：BotConnection 实现 IBot。

替代原 webserver/ws_sendapi.py：
- 所有 API 收敛为 BotConnection 实例方法，传输层（websocket）不再穿透业务；
- 基于 echo + asyncio.Future 的请求/响应配对；
- 消息参数统一接受 str / Message / MessageSegment / 段数组。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional

from app.core.logger import api_logger
from app.domain.bot import IBot
from app.domain.message import Message, MessageSegment, SegmentLike


class BotConnection(IBot):
    """单个机器人连接：连接状态 + 全部 OneBot API。"""

    def __init__(
        self,
        websocket=None,
        bot_id: Optional[int] = None,
        owner_id: Optional[int] = None,
        ws_url: str = "",
        auto_connect: bool = False,
    ) -> None:
        self.websocket = websocket
        self.bot_id = bot_id
        self.owner_id = owner_id
        self.ws_url = ws_url
        self.auto_connect = auto_connect
        self.status = "disconnected"
        self.login_info: dict = {}
        self.reconnect_attempts = 0
        self.last_error: Optional[str] = None
        self.all_group_list: list = []
        self.all_group_list_info: list = []
        self.index: Optional[int] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self._last_connect_attempt: float = 0.0
        # 出站拦截钩子（bootstrap 装配）：若设置，_send 先经出站节点链
        self.outbound_hook = None

    # ---------- 底层请求 ----------
    async def _send(self, action: str, params: dict | None = None, timeout_sec: int = 10) -> dict | None:
        """发送 API 请求。若装配了出站链，先经拦截节点（可改写 params / 吞掉发送）。"""
        if self.outbound_hook is not None:
            try:
                return await self.outbound_hook(action, params or {})
            except Exception as e:
                api_logger.error(f"[#{self.index}] 出站链异常 {action}: {e}")
                return None
        return await self._direct_send(action, params, timeout_sec)

    async def _direct_send(self, action: str, params: dict | None = None, timeout_sec: int = 10) -> dict | None:
        """真正发送（绕过 outbound_hook，供 SendNode 调用）。"""
        if not self.websocket:
            api_logger.error(f"[#{self.index}] API 未连接，请求被拒: {action}")
            return None
        echo_id = str(uuid.uuid4())
        payload = {"action": action, "params": params or {}, "echo": echo_id}
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[echo_id] = future
        try:
            async with self._lock:
                await self.websocket.send(json.dumps(payload))
            api_logger.debug(f"[#{self.index}] API(->) {action} | echo:{echo_id[:8]}")
            response = await asyncio.wait_for(future, timeout=timeout_sec)
            if response.get("status") != "ok":
                api_logger.warning(f"[API] {action} 失败: {response.get('retcode')} {response.get('message')}")
            else:
                api_logger.debug(f"[#{self.index}] API(<-) {action} | echo:{echo_id[:8]}")
            return response
        except asyncio.TimeoutError:
            api_logger.error(f"[#{self.index}] API 超时 {action} ({timeout_sec}s)")
            return None
        except Exception as e:
            api_logger.error(f"[#{self.index}] API 异常 {action}: {e}")
            return None
        finally:
            self._pending.pop(echo_id, None)

    def handle_api_response(self, message: dict) -> bool:
        """由消息循环调用：把 API 响应填充到对应 Future。返回是否消费了该消息。"""
        echo_id = message.get("echo")
        if not echo_id:
            return False
        future = self._pending.get(echo_id)
        if not future:
            api_logger.warning(f"[#{self.index}] 未找到对应请求 echo={echo_id[:8]}")
            return False
        if not future.done():
            future.set_result(message)
        return True

    # ---------- 消息发送 ----------
    async def send_group_msg(self, group_id: int, message: SegmentLike, auto_escape: bool = False) -> dict:
        return await self._send("send_group_msg", {
            "group_id": group_id,
            "message": _to_payload(message),
            "auto_escape": auto_escape,
        })

    async def send_private_msg(self, user_id: int, message: SegmentLike, auto_escape: bool = False) -> dict:
        return await self._send("send_private_msg", {
            "user_id": user_id,
            "message": _to_payload(message),
            "auto_escape": auto_escape,
        })

    async def send_msg(
        self, message_type: str, message: SegmentLike,
        user_id: Optional[int] = None, group_id: Optional[int] = None,
        auto_escape: bool = False,
    ) -> dict:
        params: dict = {"message_type": message_type, "message": _to_payload(message), "auto_escape": auto_escape}
        if user_id:
            params["user_id"] = user_id
        if group_id:
            params["group_id"] = group_id
        return await self._send("send_msg", params)

    async def send_poke(self, user_id: int, group_id: Optional[int] = None, target_id: Optional[int] = None) -> dict:
        params: dict = {"user_id": user_id}
        if group_id:
            params["group_id"] = group_id
        if target_id:
            params["target_id"] = target_id
        return await self._send("send_poke", params)

    async def send_forward_msg(self, group_id: int = 0, user_id: int = 0, msgdata: list = None) -> dict:
        msgdata = msgdata or []
        if group_id:
            params = {"group_id": group_id, "messages": msgdata}
            action = "send_group_forward_msg"
        else:
            params = {"user_id": user_id, "messages": msgdata}
            action = "send_private_forward_msg"
        return await self._send(action, params)

    async def get_forward_msg(self, id: str) -> dict:
        return await self._send("get_forward_msg", {"id": id})

    async def send_like(self, user_id: int, times: int = 1) -> dict:
        return await self._send("send_like", {"user_id": user_id, "times": times})

    async def send_group_sign(self, group_id: int) -> dict:
        return await self._send("send_group_sign", {"group_id": group_id})

    # ---------- 群管理 ----------
    async def set_group_kick(self, group_id: int, user_id: int, reject_add_request: bool = False) -> dict:
        return await self._send("set_group_kick", {"group_id": group_id, "user_id": user_id,
                                                   "reject_add_request": reject_add_request})

    async def set_group_ban(self, group_id: int, user_id: int, duration: int = 30 * 60) -> dict:
        return await self._send("set_group_ban", {"group_id": group_id, "user_id": user_id, "duration": duration})

    async def set_group_anonymous_ban(self, group_id: int, anonymous_flag: str, duration: int = 30 * 60) -> dict:
        return await self._send("set_group_anonymous_ban",
                                {"group_id": group_id, "anonymous_flag": anonymous_flag, "duration": duration})

    async def set_group_whole_ban(self, group_id: int, enable: bool = True) -> dict:
        return await self._send("set_group_whole_ban", {"group_id": group_id, "enable": enable})

    async def set_group_admin(self, group_id: int, user_id: int, enable: bool = True) -> dict:
        return await self._send("set_group_admin", {"group_id": group_id, "user_id": user_id, "enable": enable})

    async def set_group_anonymous(self, group_id: int, enable: bool = True) -> dict:
        return await self._send("set_group_anonymous", {"group_id": group_id, "enable": enable})

    async def set_group_card(self, group_id: int, user_id: int, card: str = "") -> dict:
        return await self._send("set_group_card", {"group_id": group_id, "user_id": user_id, "card": card})

    async def set_group_name(self, group_id: int, group_name: str) -> dict:
        return await self._send("set_group_name", {"group_id": group_id, "group_name": group_name})

    async def set_group_leave(self, group_id: int, is_dismiss: bool = False) -> dict:
        return await self._send("set_group_leave", {"group_id": group_id, "is_dismiss": is_dismiss})

    async def set_group_special_title(self, group_id: int, user_id: int, special_title: str = "", duration: int = -1) -> dict:
        return await self._send("set_group_special_title",
                                {"group_id": group_id, "user_id": user_id, "special_title": special_title, "duration": duration})

    # ---------- 请求处理 ----------
    async def set_friend_add_request(self, flag: str, approve: bool = True, remark: str = "") -> dict:
        return await self._send("set_friend_add_request", {"flag": flag, "approve": approve, "remark": remark})

    async def set_group_add_request(self, flag: str, approve: bool = True, reason: str = "") -> dict:
        return await self._send("set_group_add_request", {"flag": flag, "approve": approve, "reason": reason})

    # ---------- 消息操作 ----------
    async def delete_msg(self, message_id: int) -> dict:
        return await self._send("delete_msg", {"message_id": message_id})

    async def get_msg(self, message_id: int) -> dict:
        return await self._send("get_msg", {"message_id": message_id})

    async def set_essence_msg(self, message_id: int) -> dict:
        return await self._send("set_essence_msg", {"message_id": message_id})

    async def delete_essence_msg(self, message_id: int) -> dict:
        return await self._send("delete_essence_msg", {"message_id": message_id})

    async def get_essence_msg_list(self, group_id: int) -> dict:
        return await self._send("get_essence_msg_list", {"group_id": group_id})

    async def set_msg_emoji_like(self, message_id: int, emoji_id: str) -> dict:
        return await self._send("set_msg_emoji_like", {"message_id": message_id, "emoji_id": emoji_id})

    async def get_msg_history(self, group_id: int = 0, user_id: int = 0, count: int = 20,
                              reverse_order: bool = False) -> dict:
        if group_id:
            params = {"group_id": group_id, "count": count, "reverse_order": reverse_order}
            action = "get_group_msg_history"
        elif user_id:
            params = {"user_id": user_id, "count": count, "reverse_order": reverse_order}
            action = "get_friend_msg_history"
        else:
            api_logger.error("get_msg_history: group_id 与 user_id 均为空")
            return {}
        return await self._send(action, params)

    # ---------- 信息获取 ----------
    async def get_login_info(self) -> dict:
        return await self._send("get_login_info", {})

    async def get_stranger_info(self, user_id: int, no_cache: bool = False) -> dict:
        return await self._send("get_stranger_info", {"user_id": user_id, "no_cache": no_cache})

    async def get_friend_list(self) -> dict:
        return await self._send("get_friend_list", {})

    async def get_group_info(self, group_id: int, no_cache: bool = False) -> dict:
        return await self._send("get_group_info", {"group_id": group_id, "no_cache": no_cache})

    async def get_group_list(self) -> dict:
        return await self._send("get_group_list", {})

    async def get_group_member_info(self, group_id: int, user_id: int, no_cache: bool = False) -> dict:
        return await self._send("get_group_member_info",
                                {"group_id": group_id, "user_id": user_id, "no_cache": no_cache})

    async def get_group_member_list(self, group_id: int) -> dict:
        return await self._send("get_group_member_list", {"group_id": group_id})

    async def get_group_honor_info(self, group_id: int, type: str = "all") -> dict:
        return await self._send("get_group_honor_info", {"group_id": group_id, "type": type})

    # ---------- 状态 / 资源 ----------
    async def get_status(self) -> dict:
        return await self._send("get_status", {})

    async def get_version_info(self) -> dict:
        return await self._send("get_version_info", {})

    async def get_image(self, file: str) -> dict:
        return await self._send("get_image", {"file": file})

    async def get_record(self, file: str, out_format: str = "mp3") -> dict:
        return await self._send("get_record", {"file": file, "out_format": out_format})

    async def can_send_image(self) -> dict:
        return await self._send("can_send_image", {})

    async def can_send_record(self) -> dict:
        return await self._send("can_send_record", {})

    async def clean_cache(self) -> dict:
        return await self._send("clean_cache", {})

    async def set_restart(self, delay: int = 0) -> dict:
        return await self._send("set_restart", {"delay": delay})


def _to_payload(message: SegmentLike) -> Any:
    """把 str / Message / 段数组 统一为 OneBot message 参数。"""
    if isinstance(message, Message):
        return message.to_onebot()
    if isinstance(message, MessageSegment):
        return [message.to_dict()]
    if isinstance(message, list):
        return [seg.to_dict() if isinstance(seg, MessageSegment) else seg for seg in message]
    return message
