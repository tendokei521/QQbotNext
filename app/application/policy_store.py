"""响应权策略（channel_policies）的读取接口与实现。

- ``PolicyStore``：仲裁器依赖的只读协议；
- ``ChannelPolicyStore``：正式实现，读 ``ConfigService`` 的 ``channel_policies`` 表；
- ``LegacyPolicyStore``：过渡/回滚实现，从旧 ``webui.single_service`` +
  ``multi_group.groups[*].service_bot_id`` 派生（与旧 ``is_single_service_skipped`` 行为一致）。

这样「策略从哪来」与「怎么仲裁」解耦：换 store 只改 bootstrap 一行，仲裁语义只有一份。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from app.plugin.responder import (
    FALLBACK_OPEN,
    POLICY_ALL,
    POLICY_DELEGATE,
    ResponderPolicy,
    normalize_fallback,
    normalize_policy,
    policy_from_legacy,
)


class PolicyStore(Protocol):
    """按 (能力, 会话) 取响应权策略。"""

    def get(self, capability_id: str, channel_id: str | None) -> ResponderPolicy | None: ...

    def list_for(self, capability_id: str) -> list[ResponderPolicy]: ...


def _policy_from_row(row: dict, capability_id: str, channel_id: str) -> ResponderPolicy:
    """把存储行规整成策略值对象（脏配置不抛错：非法值退化为 all/open）。"""
    primary = row.get("primary_account_id")
    return ResponderPolicy(
        capability_id=str(capability_id),
        channel_id=str(channel_id),
        policy=normalize_policy(row.get("policy")),
        primary_account_id=None if primary in (None, "") else str(primary),
        fallback=normalize_fallback(row.get("fallback")),
    )


class ChannelPolicyStore:
    """正式实现：策略来自 ``ConfigService``（SQLite ``channel_policies`` 表）。"""

    def __init__(self, config_service: Any) -> None:
        self.config_service = config_service

    def get(self, capability_id: str, channel_id: str | None) -> ResponderPolicy | None:
        if not channel_id:
            return None
        try:
            row = self.config_service.get_channel_policy(capability_id, channel_id)
        except Exception:  # noqa: BLE001 —— 读策略失败按"无策略"处理，不影响消息主流程
            return None
        if not row:
            return None
        return _policy_from_row(row, capability_id, str(channel_id))

    def list_for(self, capability_id: str) -> list[ResponderPolicy]:
        try:
            rows = self.config_service.list_channel_policies(capability_id)
        except Exception:  # noqa: BLE001
            return []
        return [_policy_from_row(row, capability_id, row["channel_id"]) for row in rows]


class LegacyPolicyStore:
    """从旧配置派生策略（迁移期/回滚用）。

    旧行为不变式：
    - 全局开关关闭 → 无策略（不过滤）；
    - 开关打开但群未指定服务账号 → 无策略（不过滤，保持旧部署行为）；
    - 开关打开且群指定了服务账号 → ``delegate`` + 该 primary。
    """

    def __init__(self, config_service: Any) -> None:
        self.config_service = config_service

    def _webui(self) -> dict:
        try:
            return self.config_service.get_webui_config() or {}
        except Exception:  # noqa: BLE001 —— 读配置失败按"无策略"处理，不让消息处理崩掉
            return {}

    def get(self, capability_id: str, channel_id: str | None) -> ResponderPolicy | None:
        if not channel_id:
            return None
        webui = self._webui()
        enabled = bool((webui.get("single_service") or {}).get(capability_id))
        if not enabled:
            return None
        groups = (webui.get("multi_group") or {}).get("groups") or {}
        group_config = groups.get(str(channel_id)) or {}
        service_account = group_config.get("service_bot_id")
        if service_account in (None, ""):
            return None
        legacy = policy_from_legacy(True, service_account)
        return ResponderPolicy(
            capability_id=capability_id,
            channel_id=str(channel_id),
            policy=legacy.policy,
            primary_account_id=legacy.primary_account_id,
            fallback=FALLBACK_OPEN,
        )

    def list_for(self, capability_id: str) -> list[ResponderPolicy]:
        webui = self._webui()
        enabled = bool((webui.get("single_service") or {}).get(capability_id))
        if not enabled:
            return []
        groups = (webui.get("multi_group") or {}).get("groups") or {}
        policies: list[ResponderPolicy] = []
        for channel_id, group_config in groups.items():
            service_account = (group_config or {}).get("service_bot_id")
            if service_account in (None, ""):
                continue
            policies.append(
                ResponderPolicy(
                    capability_id=capability_id,
                    channel_id=str(channel_id),
                    policy=POLICY_DELEGATE,
                    primary_account_id=str(service_account),
                    fallback=FALLBACK_OPEN,
                )
            )
        return policies


def legacy_single_service_map(config_service: Any) -> dict[str, bool]:
    """旧 API 兼容：返回 ``{module: 是否开启单一服务}``（只读）。"""
    try:
        webui = config_service.get_webui_config() or {}
    except Exception:  # noqa: BLE001
        return {}
    single = webui.get("single_service") or {}
    return {str(module): bool(value) for module, value in single.items()}


def policies_as_dicts(policies: Iterable[ResponderPolicy]) -> list[dict]:
    """序列化策略列表（WebUI 预览/兼容接口复用）。"""
    return [
        {
            "capability_id": policy.capability_id,
            "channel_id": policy.channel_id,
            "policy": policy.policy,
            "primary_account_id": policy.primary_account_id,
            "fallback": policy.fallback,
        }
        for policy in policies
    ]


__all__ = [
    "PolicyStore",
    "ChannelPolicyStore",
    "LegacyPolicyStore",
    "legacy_single_service_map",
    "policies_as_dicts",
    "POLICY_ALL",
]
