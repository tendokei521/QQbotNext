"""对话策略的纯解析逻辑（无 IO，可单测、可给 UI 预览复用）。

## 概念

「回不回 / 怎么回 / 谁能触发」分三层回答：

1. **身份（谁）**：全局 owner/manager 名册 + 群角色 + 用户例外；
2. **场所（在哪）**：``enabled``——这个群/私聊让不让它说话；
3. **规则（怎么回）**：触发方式、引用/@、频控、静默……

本模块只做第 2、3 层的**合并**：默认值（能力自身配置）
→ 账号层覆盖（``channel_id=""``）→ 群层覆盖（``channel_id=<群号>``），最具体者胜。
每个键都记录来源（``default`` / ``account`` / ``channel``），UI 据此显示继承链。

## 与 ``domain/binding.py`` 的关系

``bindings`` 解决"能力 × 作用域"的配置，但它的 channel 档**没有账号维度**
（主键是 capability + scope），无法表达"账号 A 在群 X 允许、账号 B 不允许"。
所以群级覆盖单独一张表（``channel_overrides``，含 account_id），解析在这里；
两者分工见 docs/permission-dialogue-policy-design.md §3。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

SOURCE_DEFAULT = "default"
SOURCE_ACCOUNT = "account"
SOURCE_CHANNEL = "channel"

#: 账号层/群层可覆盖的配置键白名单（capability → keys）。
#:
#: **必须显式声明**：否则又会变成"任意键可覆盖、没人知道生效的是谁"。
#: 未在名单里的键，写接口直接拒绝（``POLICY_KEY_NOT_OVERRIDABLE``）。
SCOPE_OVERRIDABLE: dict[str, tuple[str, ...]] = {
    "agent": (
        "trigger_at",             # 是否响应 @
        "trigger_keyword",        # 关键词触发列表
        "reply_cooldown",         # 同会话最小回复间隔（秒）
        "message_debounce_seconds",   # 消息抖动窗口
        "message_debounce_max_wait",  # 抖动最长等待
        "interrupt_enable",       # 新消息是否打断旧回复
        "outbound_directive_enable",  # 是否允许 [reply]/[@QQ]
    ),
}

#: 权限键同样按层覆盖：谁能触发 / 谁被排除
PERMISSION_KEYS: tuple[str, ...] = ("role_min", "user_mode", "user_list")


def is_overridable(capability: str, key: str) -> bool:
    """该键是否允许在账号/群层覆盖。"""
    return key in SCOPE_OVERRIDABLE.get(str(capability), ())


def overridable_keys(capability: str) -> tuple[str, ...]:
    return SCOPE_OVERRIDABLE.get(str(capability), ())


@dataclass(frozen=True)
class ResolvedPolicy:
    """某账号在某群（或私聊/默认层）的生效策略。"""

    capability: str
    account_id: str
    channel_id: str | None
    enabled: bool
    enabled_source: str
    config: dict[str, Any]
    permission: dict[str, Any]
    #: 配置键 → 来源（default/account/channel）；UI 显示继承链用
    sources: Mapping[str, str] = field(default_factory=dict)

    def source_of(self, key: str) -> str:
        return str(self.sources.get(key, SOURCE_DEFAULT))


def _filter_keys(capability: str, data: Mapping[str, Any] | None) -> dict[str, Any]:
    """只保留白名单里的配置键（脏数据/旧键不进解析结果）。"""
    if not isinstance(data, Mapping):
        return {}
    return {key: value for key, value in data.items() if is_overridable(capability, key)}


def resolve_policy(
    capability: str,
    *,
    account_id: object,
    channel_id: object | None = None,
    defaults: Mapping[str, Any] | None = None,
    default_enabled: bool = True,
    account_row: Mapping[str, Any] | None = None,
    channel_row: Mapping[str, Any] | None = None,
) -> ResolvedPolicy:
    """按「默认 → 账号 → 群」合并出生效策略。

    Args:
        capability: ``agent`` 或模块名（决定白名单）。
        account_id: 账号 id。
        channel_id: 群号；``None`` 表示私聊/默认层解析。
        defaults: 能力自身配置（agent: ``runtime.config``；模块: 模块配置）。
        default_enabled: 没有 enabled 覆盖时的默认开关。
        account_row / channel_row: ``channel_overrides`` 的行
            （``{"enabled": bool|None, "config": dict, "permission": dict}``）。
    """
    config: dict[str, Any] = _filter_keys(capability, defaults)
    sources: dict[str, str] = {key: SOURCE_DEFAULT for key in config}
    permission: dict[str, Any] = {}
    enabled = bool(default_enabled)
    enabled_source = SOURCE_DEFAULT

    for row, source in ((account_row, SOURCE_ACCOUNT), (channel_row, SOURCE_CHANNEL)):
        if not isinstance(row, Mapping):
            continue
        if row.get("enabled") is not None:
            enabled = bool(row["enabled"])
            enabled_source = source
        for key, value in _filter_keys(capability, row.get("config")).items():
            config[key] = value
            sources[key] = source
        row_permission = row.get("permission")
        if isinstance(row_permission, Mapping):
            for key in PERMISSION_KEYS:
                if key in row_permission:
                    permission[key] = row_permission[key]
                    sources[key] = source

    return ResolvedPolicy(
        capability=str(capability),
        account_id=str(account_id),
        channel_id=None if channel_id in (None, "") else str(channel_id),
        enabled=enabled,
        enabled_source=enabled_source,
        config=config,
        permission=permission,
        sources=sources,
    )
