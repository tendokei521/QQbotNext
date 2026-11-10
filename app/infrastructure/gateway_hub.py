"""协议路由：把 OneBot 与官方 QQ 机器人两个网关拼成同一张账号表。

``BotService`` / WebUI / 插件长期把 ``gateway`` 当成「所有账号的连接表」使用
（``gateway.connections`` / ``add_bot`` / ``get_bots_info`` / ``recent_events`` …）。
新增第二种协议时，最省改动、也最不容易漏改的做法是保留这层门面：

- 对外：仍是一个 gateway 对象，方法名全部不变；
- 对内：按 ``account.protocol`` 路由到 ``OneBotGateway`` 或 ``QqBotGateway``。

两个子网关各自维护自己的生命周期（连接、重连、诊断历史），互不干扰；
``connections`` 返回**合并视图**（新 dict，调用方只读）。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, MutableMapping
from typing import Any

from app.domain.protocol import PROTOCOL_QQBOT, normalize_protocol


class _MergedConnections(MutableMapping):
    """把两个子网关的 ``connections`` 合成一个**活视图**。

    为什么不是每次返回新 dict：历史代码（以及测试）会把 ``gateway.connections``
    当普通 dict 用（``connections["acc"] = conn``、``del``、``.items()``）。
    返回拷贝会让这些写入静默丢失（表现为「账号明明塞进了连接表，API 却说没连接」）。
    """

    def __init__(self, onebot: dict, qqbot: dict) -> None:
        self._onebot = onebot
        self._qqbot = qqbot

    def __getitem__(self, key: str) -> Any:
        key = str(key)
        if key in self._qqbot:
            return self._qqbot[key]
        return self._onebot[key]

    def __setitem__(self, key: str, value: Any) -> None:
        key = str(key)
        protocol = normalize_protocol(getattr(value, "protocol", ""))
        target = self._qqbot if protocol == PROTOCOL_QQBOT else self._onebot
        # 从另一侧移走同名账号，避免同一个 id 出现两次
        self._onebot.pop(key, None) if target is self._qqbot else self._qqbot.pop(key, None)
        target[key] = value

    def __delitem__(self, key: str) -> None:
        key = str(key)
        removed = False
        for mapping in (self._qqbot, self._onebot):
            if key in mapping:
                del mapping[key]
                removed = True
        if not removed:
            raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        yield from self._onebot
        for key in self._qqbot:
            if key not in self._onebot:
                yield key

    def __len__(self) -> int:
        return len(set(self._onebot) | set(self._qqbot))

    def __contains__(self, key: object) -> bool:
        text = str(key)
        return text in self._onebot or text in self._qqbot


class GatewayHub:
    def __init__(self, *, onebot: Any, qqbot: Any) -> None:
        self.onebot = onebot
        self.qqbot = qqbot
        #: 账号配置读取器（BotService 注入）：拆成两份按协议过滤的 provider
        self._bots_provider = None
        self._merged: _MergedConnections | None = None
        self._apply_providers()

    # ==================== 账号表 ====================
    @property
    def connections(self) -> _MergedConnections:
        if self._merged is None:
            self._merged = _MergedConnections(self.onebot.connections, self.qqbot.connections)
        return self._merged

    @property
    def reverse_link(self):
        """只有 OneBot 有反向监听；官方账号永远由框架连出去。"""
        return self.onebot.reverse_link

    @property
    def reverse_servers(self) -> dict:
        return getattr(self.onebot, "reverse_servers", {})

    def get_bot(self, account_id: str):
        return self.qqbot.get_bot(account_id) or self.onebot.get_bot(account_id)

    def find_conn_by_bot_id(self, bot_id: Any):
        return self.qqbot.find_conn_by_bot_id(bot_id) or self.onebot.find_conn_by_bot_id(bot_id)

    def get_last_account(self, account_id: str) -> dict | None:
        gateway = self._gateway_of(account_id)
        return gateway.get_last_account(account_id) if gateway else None

    def _gateway_of(self, account_id: str):
        key = str(account_id)
        if key in self.qqbot.connections:
            return self.qqbot
        if key in self.onebot.connections:
            return self.onebot
        return None

    def _gateway_for_protocol(self, protocol: Any):
        return self.qqbot if normalize_protocol(protocol) == PROTOCOL_QQBOT else self.onebot

    # ==================== 注入属性（同时下发两份） ====================
    @property
    def bots_provider(self):
        return self._bots_provider

    @bots_provider.setter
    def bots_provider(self, fn) -> None:
        self._bots_provider = fn
        self._apply_providers()

    def _apply_providers(self) -> None:
        provider = self._bots_provider

        def _onebot_configs() -> list[dict]:
            rows = provider() if provider else []
            return [r for r in (rows or []) if normalize_protocol((r or {}).get("protocol")) != PROTOCOL_QQBOT]

        def _qqbot_configs() -> list[dict]:
            rows = provider() if provider else []
            return [r for r in (rows or []) if normalize_protocol((r or {}).get("protocol")) == PROTOCOL_QQBOT]

        self.onebot.bots_provider = _onebot_configs
        self.qqbot.bots_provider = _qqbot_configs

    def _forward(self, name: str, value: Any) -> None:
        setattr(self.onebot, name, value)
        setattr(self.qqbot, name, value)

    def __getattr__(self, name: str) -> Any:
        """未显式定义的属性（钩子注册表 / 回调）落到两个子网关，保持同名同义。"""
        if name.startswith("__"):
            raise AttributeError(name)
        onebot = self.__dict__.get("onebot")
        qqbot = self.__dict__.get("qqbot")
        if onebot is not None and hasattr(onebot, name):
            value = getattr(onebot, name)
            # 读属性返回 OneBot 的值（诊断历史等只在一侧有意义）；写属性两边一起写
            return value
        if qqbot is not None and hasattr(qqbot, name):
            return getattr(qqbot, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("onebot", "qqbot", "_bots_provider", "_merged") or name.startswith("__"):
            object.__setattr__(self, name, value)
            return
        object.__setattr__(self, name, value)
        # property 的 setter 自己会决定怎么下发（例如 bots_provider 要按协议过滤成两份）；
        # 这里再无条件转发一遍会把过滤后的 provider 覆盖成未过滤版本 ——
        # 后果是 OneBot 网关把官方账号也建成连接（重启后 /api/bots 出现同 id 幽灵账号）。
        if isinstance(getattr(type(self), name, None), property):
            return
        onebot = self.__dict__.get("onebot")
        qqbot = self.__dict__.get("qqbot")
        if onebot is not None and hasattr(onebot, name):
            setattr(onebot, name, value)
        if qqbot is not None and hasattr(qqbot, name):
            setattr(qqbot, name, value)

    # ==================== 生命周期 ====================
    async def start_all(self, bots_config: list[dict]) -> None:
        rows = list(bots_config or [])
        onebot_rows = [r for r in rows if normalize_protocol((r or {}).get("protocol")) != PROTOCOL_QQBOT]
        qqbot_rows = [r for r in rows if normalize_protocol((r or {}).get("protocol")) == PROTOCOL_QQBOT]
        await self.onebot.start_all(onebot_rows)
        await self.qqbot.start_all(qqbot_rows)

    async def reconcile(self) -> None:
        await self.onebot.reconcile()
        await self.qqbot.reconcile()

    async def add_bot(
        self,
        ws_url: str,
        owner_id: int | None,
        auto_connect: bool = False,
        account_id: str | None = None,
        conn_mode: str = "forward",
        name: str = "",
        *,
        protocol: str = "onebot",
        transport: str = "",
        app_id: str = "",
        app_secret: str = "",
    ) -> str:
        if normalize_protocol(protocol) == PROTOCOL_QQBOT:
            return await self.qqbot.add_bot(
                ws_url, owner_id, auto_connect,
                account_id=account_id, conn_mode=conn_mode, name=name,
                protocol=protocol, transport=transport or "ws",
                app_id=app_id, app_secret=app_secret,
            )
        return await self.onebot.add_bot(
            ws_url, owner_id, auto_connect,
            account_id=account_id, conn_mode=conn_mode, name=name,
        )

    async def del_bot(self, account_id: str) -> None:
        gateway = self._gateway_of(account_id)
        if gateway is not None:
            await gateway.del_bot(account_id)
            return
        await self.onebot.del_bot(account_id)
        await self.qqbot.del_bot(account_id)

    async def readd_bot(self, ws_url: str, owner_id: int | None, account_id: str, conn_mode: str | None = None) -> str:
        """更新已有连接地址（仅 OneBot 有协议端地址；官方账号由 add_bot 更新凭据）。"""
        gateway = self._gateway_of(account_id)
        if gateway is self.qqbot:
            return str(account_id)
        return await self.onebot.readd_bot(ws_url, owner_id, account_id, conn_mode=conn_mode)

    @property
    def event_queue_drops(self) -> dict[str, int]:
        merged: dict[str, int] = {}
        merged.update(getattr(self.onebot, "event_queue_drops", {}) or {})
        merged.update(getattr(self.qqbot, "event_queue_drops", {}) or {})
        return merged

    async def connect_bot(self, account_id: str) -> bool:
        gateway = self._gateway_of(account_id)
        return await gateway.connect_bot(account_id) if gateway else False

    async def disconnect_bot(self, account_id: str, *, manual: bool = False) -> None:
        gateway = self._gateway_of(account_id)
        if gateway is not None:
            await gateway.disconnect_bot(account_id, manual=manual)

    async def reconnect_bot(self, account_id: str) -> bool:
        gateway = self._gateway_of(account_id)
        return await gateway.reconnect_bot(account_id) if gateway else False

    async def shutdown(self) -> None:
        await self.onebot.shutdown()
        await self.qqbot.shutdown()

    # ==================== 查询 ====================
    def get_bots_info(self) -> list[dict]:
        # 顺序以 OneBot 为主，官方账号追加在后（前端按 id 定位，不依赖下标）
        return [*self.onebot.get_bots_info(), *self.qqbot.get_bots_info()]

    def get_bot_info(self, account_id: str) -> dict | None:
        gateway = self._gateway_of(account_id)
        return gateway.get_bot_info(account_id) if gateway else None

    def recent_events(self, account_id: str, limit: int = 50) -> list[dict]:
        gateway = self._gateway_of(account_id)
        return gateway.recent_events(account_id, limit) if gateway else []

    def clear_events(self, account_id: str) -> None:
        gateway = self._gateway_of(account_id)
        if gateway is not None:
            gateway.clear_events(account_id)

    def latest_group_id(self, account_id: str):
        gateway = self._gateway_of(account_id)
        return gateway.latest_group_id(account_id) if gateway else None
