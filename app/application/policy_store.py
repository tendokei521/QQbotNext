"""响应权策略（channel_policies）的读取接口与过渡实现。

- ``PolicyStore``：仲裁器依赖的只读协议（P1 会换成表存储 ``ChannelPolicyStore``）；
- ``LegacyPolicyStore``：P0 过渡实现，从旧配置 ``webui.single_service`` +
  ``multi_group.groups[*].service_bot_id`` 派生，行为与旧 ``is_single_service_skipped`` 对齐。

这样「策略从哪来」与「怎么仲裁」解耦：迁移期换 store 即可，仲裁语义只有一份。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from app.plugin.responder import (
    FALLBACK_OPEN,
    POLICY_ALL,
    POLICY_DELEGATE,
    ResponderPolicy,
    policy_from_legacy,
)


class PolicyStore(Protocol):
    """按 (能力, 会话) 取响应权策略。"""

    def get(self, capability_id: str, channel_id: str | None) -> ResponderPolicy | None: ...

    def list_for(self, capability_id: str) -> list[ResponderPolicy]: ...


class LegacyPolicyStore:
    """从旧配置派生策略（P0 过渡；P1 迁移完成后由表存储取代）。

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
    "LegacyPolicyStore",
    "legacy_single_service_map",
    "policies_as_dicts",
    "POLICY_ALL",
]
