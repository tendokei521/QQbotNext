"""Bot 抽象接口（IBot）。

模块只依赖本接口收发消息，不接触 WebSocket / 传输层。
OneBot 适配器（infrastructure/onebot/client.BotConnection）实现本接口；
未来接入其它协议仅需新增适配器，模块零改动。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.message import SegmentLike


class IBot(ABC):
    """一个已连接的机器人账号。"""

    # ---------- 运行时状态 ----------
    # 仅作类型声明（mutable 默认值会跨实例共享，实例属性一律在 __init__ 初始化）
    bot_id: int | None = None
    index: int | None = None
    owner_id: int | None = None
    ws_url: str = ""
    status: str = "disconnected"
    auto_connect: bool = False
    login_info: dict | None = None
    all_group_list: list | None = None
    all_group_list_info: list | None = None

    # ---------- 消息发送 ----------
    @abstractmethod
    async def send_group_msg(
        self, group_id: int, message: SegmentLike, auto_escape: bool = False
    ) -> dict: ...

    @abstractmethod
    async def send_private_msg(
        self, user_id: int, message: SegmentLike, auto_escape: bool = False
    ) -> dict: ...

    @abstractmethod
    async def send_msg(
        self,
        message_type: str,
        message: SegmentLike,
        user_id: int | None = None,
        group_id: int | None = None,
        auto_escape: bool = False,
    ) -> dict: ...

    @abstractmethod
    async def send_poke(
        self, user_id: int, group_id: int | None = None, target_id: int | None = None
    ) -> dict: ...

    @abstractmethod
    async def send_forward_msg(self, group_id: int = 0, user_id: int = 0, msgdata: list = None) -> dict: ...

    @abstractmethod
    async def get_forward_msg(self, id: str) -> dict: ...

    # ---------- 群管理 ----------
    @abstractmethod
    async def set_group_kick(self, group_id: int, user_id: int, reject_add_request: bool = False) -> dict: ...

    @abstractmethod
    async def set_group_ban(self, group_id: int, user_id: int, duration: int = 30 * 60) -> dict: ...

    @abstractmethod
    async def set_group_whole_ban(self, group_id: int, enable: bool = True) -> dict: ...

    @abstractmethod
    async def set_group_admin(self, group_id: int, user_id: int, enable: bool = True) -> dict: ...

    @abstractmethod
    async def set_group_card(self, group_id: int, user_id: int, card: str = "") -> dict: ...

    @abstractmethod
    async def set_group_name(self, group_id: int, group_name: str) -> dict: ...

    @abstractmethod
    async def set_group_leave(self, group_id: int, is_dismiss: bool = False) -> dict: ...

    # ---------- 请求处理 ----------
    @abstractmethod
    async def set_group_add_request(self, flag: str, approve: bool = True, reason: str = "") -> dict: ...

    @abstractmethod
    async def set_friend_add_request(self, flag: str, approve: bool = True, remark: str = "") -> dict: ...

    # ---------- 消息操作 ----------
    @abstractmethod
    async def delete_msg(self, message_id: int) -> dict: ...

    @abstractmethod
    async def get_msg(self, message_id: int) -> dict: ...

    @abstractmethod
    async def set_essence_msg(self, message_id: int) -> dict: ...

    @abstractmethod
    async def delete_essence_msg(self, message_id: int) -> dict: ...

    @abstractmethod
    async def get_essence_msg_list(self, group_id: int) -> dict: ...

    @abstractmethod
    async def set_msg_emoji_like(self, message_id: int, emoji_id: str) -> dict: ...

    @abstractmethod
    async def get_msg_history(self, group_id: int = 0, user_id: int = 0, count: int = 20,
                              reverse_order: bool = False) -> dict: ...

    # ---------- 信息获取 ----------
    @abstractmethod
    async def get_login_info(self) -> dict: ...

    @abstractmethod
    async def get_stranger_info(self, user_id: int, no_cache: bool = False) -> dict: ...

    @abstractmethod
    async def get_friend_list(self) -> dict: ...

    @abstractmethod
    async def get_group_info(self, group_id: int, no_cache: bool = False) -> dict: ...

    @abstractmethod
    async def get_group_list(self) -> dict: ...

    @abstractmethod
    async def get_group_member_info(self, group_id: int, user_id: int, no_cache: bool = False) -> dict: ...

    @abstractmethod
    async def get_group_member_list(self, group_id: int) -> dict: ...

    @abstractmethod
    async def get_group_honor_info(self, group_id: int, type: str = "all") -> dict: ...

    @abstractmethod
    async def send_group_sign(self, group_id: int) -> dict: ...

    @abstractmethod
    async def send_like(self, user_id: int, times: int = 1) -> dict: ...

    # ---------- 状态 / 资源 ----------
    @abstractmethod
    async def get_status(self) -> dict: ...

    @abstractmethod
    async def get_version_info(self) -> dict: ...

    @abstractmethod
    async def get_image(self, file: str) -> dict: ...

    @abstractmethod
    async def get_record(self, file: str, out_format: str = "mp3") -> dict: ...

    @abstractmethod
    async def can_send_image(self) -> dict: ...

    @abstractmethod
    async def can_send_record(self) -> dict: ...

    @abstractmethod
    async def clean_cache(self) -> dict: ...

    @abstractmethod
    async def set_restart(self, delay: int = 0) -> dict: ...

    # ---------- 通用 API ----------
    @abstractmethod
    async def call_api(self, action: str, params: dict | None = None) -> dict | None: ...
