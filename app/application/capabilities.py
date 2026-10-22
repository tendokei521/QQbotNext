"""框架能力清单：给模块与前端一份「这个框架有什么」的机器可读答案。

用途（也是它存在的理由）：

- 模块自检：``ctx.services.capabilities()`` + ``BaseModule.missing_requirements()``；
- WebUI / 前端：显示「该模块声明的依赖，框架是否满足」，而不是运行到一半才报错；
- 文档与测试的单一事实源，避免"文档说有、实际没有"。

``collect_capabilities`` 只读，不产生副作用；任何子项取不到都降级为空而不是抛异常
（它是诊断接口，不能自己成为新的故障点）。
"""

from __future__ import annotations

from typing import Any

#: 会出现在清单里的服务字段（与 `ServiceAccess` 保持一致；新增服务请补进来）
SERVICE_FIELDS = (
    "cache",
    "config_service",
    "task_manager",
    "settings",
    "scheduler",
    "agent_manager",
    "llm",
    "provider_presets",
    "provider_models",
    "config_profiles",
    "role_presets",
    "resources",
    "features",
    # 数据源（list/dynamic）与 LLM 无关，这里用语义化别名列出
    "data_sources",
    "send_hooks",
    "before_send_hooks",
    "api_hooks",
    "lifecycle_hooks",
    "event_completed_hooks",
    # 模块侧的原料接口（v3 新增）：节点注册表 / 会话 / 运行时只读 / 框架工具集 / 出站管道
    "node_registry",
    "sessions",
    "runtime",
    "tools",
    "outbound",
)


def _framework_version() -> str:
    try:
        from importlib.metadata import version

        return str(version("qqbot-next"))
    except Exception:  # noqa: BLE001 —— 未安装（源码直跑）时不该影响清单
        return "unknown"


def collect_capabilities(*, services: Any = None, features: Any = None, resources: Any = None) -> dict:
    """汇总框架能力。

    Args:
        services: ``ServiceAccess``（用于列出可用服务；缺省则该段为空）。
        features: ``FeatureRegistry``（列出可被接管的能力 id）。
        resources: ``ResourceRegistry``（列出可引用的资源种类）。

    Returns:
        含 ``framework_version`` / ``schema_version`` / ``schema_types`` /
        ``resource_types`` / ``resources`` / ``services`` / ``features`` 的字典。
    """
    from app.plugin.schema_utils import KNOWN_TYPES, RESOURCE_TYPES, SCHEMA_VERSION

    feature_ids: list[str] = []
    if features is not None and callable(getattr(features, "list_features", None)):
        try:
            feature_ids = [str(item.get("feature_id")) for item in features.list_features()]
        except Exception:  # noqa: BLE001
            feature_ids = []

    resource_kinds: list[dict] = []
    if resources is not None and callable(getattr(resources, "kinds", None)):
        try:
            resource_kinds = list(resources.kinds())
        except Exception:  # noqa: BLE001
            resource_kinds = []

    return {
        "framework_version": _framework_version(),
        "schema_version": SCHEMA_VERSION,
        "schema_types": list(KNOWN_TYPES),
        "resource_types": list(RESOURCE_TYPES),
        "resources": resource_kinds,
        "services": [name for name in SERVICE_FIELDS if getattr(services, name, None) is not None],
        "features": feature_ids,
    }
