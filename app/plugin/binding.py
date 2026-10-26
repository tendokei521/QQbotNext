"""作用域绑定（Binding）：模块配置/权限/开关的分层解析。

一个"事实"只属于一个作用域，解析时**最具体者胜**：

    global  →  account  →  channel

- ``global``：模块默认值（``scope_id=""``），一次配置对所有账号生效；
- ``account``：账号级覆盖（``scope_id=account_id``）；
- ``channel``：会话级覆盖（群号，预留给「某群单独配置」的模块）。

本模块是**纯逻辑**：不读数据库、不碰网关，只接收「某能力的全部 binding」并算出有效视图。
存储由 ``ConfigService`` 提供（见 ``docs/multi-account-design.md`` §4/§5）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

SCOPE_GLOBAL = "global"
SCOPE_ACCOUNT = "account"
SCOPE_CHANNEL = "channel"

#: 解析顺序：从宽到窄（越靠后越具体）
SCOPES: tuple[str, ...] = (SCOPE_GLOBAL, SCOPE_ACCOUNT, SCOPE_CHANNEL)
_SCOPE_RANK = {scope: index for index, scope in enumerate(SCOPES)}

#: 无任何 binding 覆盖时的默认来源标记
SOURCE_DEFAULT = "default"


@dataclass(frozen=True)
class Binding:
    """一条作用域绑定。

    ``enabled=None`` 表示"本层不覆盖"，继续沿用更宽作用域的值（数据库列可空）。
    ``config`` / ``permission`` 为逐键覆盖（深层只覆盖自己声明的键）。
    """

    capability_id: str
    scope_type: str
    scope_id: str = ""
    enabled: bool | None = None
    config: Mapping[str, Any] = field(default_factory=dict)
    permission: Mapping[str, Any] = field(default_factory=dict)
    priority: int = 100

    def __post_init__(self) -> None:
        if self.scope_type not in _SCOPE_RANK:
            raise ValueError(f"未知 binding 作用域: {self.scope_type!r}")

    @property
    def rank(self) -> int:
        return _SCOPE_RANK[self.scope_type]


@dataclass(frozen=True)
class ResolvedBinding:
    """某能力在某账号/某会话下的有效视图（解析结果）。"""

    capability_id: str
    account_id: str | None
    channel_id: str | None
    enabled: bool
    enabled_source: str
    config: dict[str, Any]
    permission: dict[str, Any]
    #: 每个生效配置键来自哪个作用域（``default`` / ``global`` / ``account`` / ``channel``）
    sources: Mapping[str, str]

    def source_of(self, key: str) -> str:
        """某个配置键的来源作用域（UI 显示继承链用）。"""
        return self.sources.get(key, SOURCE_DEFAULT)


#: 取某能力的全部 binding（由 ConfigService 的快照实现；解析器不关心来源）
BindingProvider = Callable[[str], Iterable[Binding]]


class BindingResolver:
    """把「某能力的全部 binding」解析成「某账号/某会话的有效视图」。"""

    def __init__(self, provider: BindingProvider, *, default_enabled: bool = True) -> None:
        self._provider = provider
        self.default_enabled = default_enabled

    # ---------- 匹配 ----------

    @staticmethod
    def _matches(binding: Binding, account_id: str | None, channel_id: str | None) -> bool:
        if binding.scope_type == SCOPE_GLOBAL:
            return True
        if binding.scope_type == SCOPE_ACCOUNT:
            return account_id is not None and binding.scope_id == str(account_id)
        # channel
        return channel_id is not None and binding.scope_id == str(channel_id)

    # ---------- 解析 ----------

    def resolve(
        self,
        capability_id: str,
        *,
        account_id: str | None = None,
        channel_id: str | None = None,
        default_config: Mapping[str, Any] | None = None,
        default_permission: Mapping[str, Any] | None = None,
    ) -> ResolvedBinding:
        """解析有效 binding。

        ``default_config`` / ``default_permission`` 是能力自身的默认值（来自模块声明），
        优先级最低；任何作用域的同名键都会覆盖它。
        """
        config: dict[str, Any] = dict(default_config or {})
        permission: dict[str, Any] = dict(default_permission or {})
        sources: dict[str, str] = {key: SOURCE_DEFAULT for key in config}
        enabled = self.default_enabled
        enabled_source = SOURCE_DEFAULT

        applicable = [
            binding
            for binding in self._provider(capability_id)
            if self._matches(binding, account_id, channel_id)
        ]
        # 宽作用域先应用；同作用域按 priority 稳定排序（PK 保证同作用域只有一行）
        applicable.sort(key=lambda binding: (binding.rank, binding.priority))

        for binding in applicable:
            if binding.enabled is not None:
                enabled = bool(binding.enabled)
                enabled_source = binding.scope_type
            for key, value in binding.config.items():
                config[key] = value
                sources[key] = binding.scope_type
            for key, value in binding.permission.items():
                permission[key] = value

        return ResolvedBinding(
            capability_id=capability_id,
            account_id=str(account_id) if account_id is not None else None,
            channel_id=str(channel_id) if channel_id is not None else None,
            enabled=enabled,
            enabled_source=enabled_source,
            config=config,
            permission=permission,
            sources=sources,
        )

    # ---------- 便捷读取 ----------

    def enabled(
        self,
        capability_id: str,
        *,
        account_id: str | None = None,
        channel_id: str | None = None,
    ) -> bool:
        return self.resolve(capability_id, account_id=account_id, channel_id=channel_id).enabled

    def config(
        self,
        capability_id: str,
        *,
        account_id: str | None = None,
        channel_id: str | None = None,
        default: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.resolve(
            capability_id, account_id=account_id, channel_id=channel_id, default_config=default
        ).config

    def permission(
        self,
        capability_id: str,
        *,
        account_id: str | None = None,
        channel_id: str | None = None,
        default: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.resolve(
            capability_id, account_id=account_id, channel_id=channel_id, default_permission=default
        ).permission
