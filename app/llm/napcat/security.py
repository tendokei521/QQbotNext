"""NapCat 工具开关/白名单/黑名单校验。"""

from __future__ import annotations

from typing import Any


def is_tool_enabled(runtime, spec: dict) -> bool:
    """判断 NapCat 工具是否允许注入。

    规则：
    - napcat_tools_enable 总开关；
    - napcat_tools_denied 黑名单优先；
    - napcat_tools_allowed 非空时只允许白名单。
    """
    if runtime is None:
        return False
    try:
        config = getattr(runtime, "config", None)
        if config is None:
            return False
        if not bool(config.get("napcat_tools_enable", False)):
            return False
        name = str(spec.get("name", ""))
        denied = list(config.get("napcat_tools_denied", []) or [])
        allowed = list(config.get("napcat_tools_allowed", []) or [])
        if name in denied:
            return False
        if allowed and name not in allowed:
            return False
        return True
    except Exception:
        return False
