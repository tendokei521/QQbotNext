"""NapCat / OneBot 通用工具包：数据驱动地把 NapCat API 暴露给 LLM。"""

from __future__ import annotations

from .manifest import NAP_CAT_TOOLS
from .tools import build_napcat_tools

__all__ = ["NAP_CAT_TOOLS", "build_napcat_tools"]
