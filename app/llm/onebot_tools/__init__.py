"""OneBot 通用工具包：数据驱动地把 OneBot API 暴露给 LLM。"""

from __future__ import annotations

from .manifest import ONEBOT_TOOLS
from .tools import build_onebot_tools

__all__ = ["ONEBOT_TOOLS", "build_onebot_tools"]
