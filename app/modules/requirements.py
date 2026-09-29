"""模块依赖自检：``BaseModule.requires`` 声明 → 加载时校验，缺失只警告不阻断。

**为什么**：模块用到 ``ctx.services.llm`` / 某个 feature 时，若框架版本不提供，
原先要等运行到那一行才 AttributeError/静默降级，排查成本高。声明一次，加载时点名。

名字解析口径（与 ``collect_capabilities`` 的清单一致）：

1. ``ctx.services.<name>`` 非空 → 满足（服务，如 ``llm`` / ``role_presets``）；
2. 否则 ``FeatureRegistry`` 注册了同名 feature → 满足（能力，如 ``assembly``）；
3. 都没有 → 记入缺失（调用方只警告，模块可据此在自己的逻辑里降级）。
"""

from __future__ import annotations

from typing import Any


def declared_requirements(module: Any) -> list[str]:
    """模块声明的依赖名（去空白、去重保序）。"""
    raw = getattr(module, "requires", ()) or ()
    names: list[str] = []
    for item in raw:
        name = str(item or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def missing_requirements(module: Any, *, services: Any = None, features: Any = None) -> list[str]:
    """返回框架**没有提供**的依赖名（空列表 = 全部满足，或模块没声明依赖）。"""
    names = declared_requirements(module)
    if not names:
        return []

    if services is None:
        services = getattr(getattr(module, "ctx", None), "services", None)
    if features is None and services is not None:
        features = getattr(services, "features", None)

    missing: list[str] = []
    for name in names:
        if getattr(services, name, None) is not None:
            continue
        get_feature = getattr(features, "get", None)
        if callable(get_feature) and get_feature(name) is not None:
            continue
        missing.append(name)
    return missing
