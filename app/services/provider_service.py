"""配置动态数据源（Provider）注册表。

为 WebUI 的 list / dynamic 字段提供后端数据：
- 内置数据源：groups（群列表）、friends（好友列表）—— 直接基于 Bot 连接获取；
- 模块可通过在 module.py 声明 LIST_PROVIDERS / DYNAMIC_PROVIDERS 注册自定义数据源。

数据源方法（定义在 Module 类上，注册表在模块加载时自动绑定）：

    class Module(BaseModule):
        LIST_PROVIDERS = {"groups": "list_groups"}
        DYNAMIC_PROVIDERS = {"providers": "dynamic_providers"}

        async def list_groups(self, field, bot) -> dict:
            # field: 字段 schema；bot: IBot 或 None
            return {"items": [...]}          # 也可用 groups / friends 别名

        async def dynamic_providers(self, field, bot, value=None) -> dict:
            if value is None:                # 拉取下拉框选项
                return {"options": [{"value": "...", "label": "..."}]}
            return {"fields": [{"key": "...", "type": "...", "label": "..."}]}  # 某选项的子字段
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, Optional

from app.core.logger import logger

ListHandler = Callable[[Any, Any], Any]          # (module, field, bot)
DynamicHandler = Callable[[Any, Any, Any], Any]  # (module, field, bot, value)


async def _builtin_groups(module, field, bot) -> dict:
    """内置群列表数据源。"""
    if not bot:
        return {"items": []}
    resp = await bot.get_group_list()
    items = []
    for g in (resp or {}).get("data", []) or []:
        items.append({
            "group_id": str(g.get("group_id", "")),
            "group_name": g.get("group_name", ""),
            "member_count": g.get("member_count", 0),
            "max_member_count": g.get("max_member_count", 0),
        })
    return {"items": items}


async def _builtin_friends(module, field, bot) -> dict:
    """内置好友列表数据源。"""
    if not bot:
        return {"items": []}
    resp = await bot.get_friend_list()
    items = []
    for f in (resp or {}).get("data", []) or []:
        items.append({
            "user_id": str(f.get("user_id", "")),
            "nickname": f.get("nickname", ""),
        })
    return {"items": items}


# 框架内置数据源（任何模块无需声明即可用）
_BUILTIN_LIST: Dict[str, ListHandler] = {
    "groups": _builtin_groups,
    "friends": _builtin_friends,
}


class ProviderRegistry:
    """数据源注册表（进程内单例，由容器注入）。"""

    def __init__(self, log=None) -> None:
        self._list: Dict[tuple, ListHandler] = {}
        self._dynamic: Dict[tuple, DynamicHandler] = {}
        self.log = log or logger

    # ── 注册 ──────────────────────────────────────────────

    def register_list(self, module_name: str, endpoint: str, handler: ListHandler) -> None:
        self._list[(module_name, endpoint)] = handler

    def register_dynamic(self, module_name: str, endpoint: str, handler: DynamicHandler) -> None:
        self._dynamic[(module_name, endpoint)] = handler

    def register_module(self, module) -> int:
        """从模块实例自动注册其声明的数据源方法。"""
        module_name = module.module_name
        cls = type(module)
        count = 0
        for endpoint, method_name in getattr(cls, "LIST_PROVIDERS", {}).items():
            self.register_list(module_name, endpoint, getattr(cls, method_name))
            count += 1
        for endpoint, method_name in getattr(cls, "DYNAMIC_PROVIDERS", {}).items():
            self.register_dynamic(module_name, endpoint, getattr(cls, method_name))
            count += 1
        if count:
            self.log.debug(f"[Provider] 模块 {module_name} 已注册 {count} 个数据源")
        return count

    # ── 查询 / 调用 ───────────────────────────────────────

    def get(self, module_name: str, endpoint: str, kind: str = "list") -> Optional[Callable]:
        if kind == "dynamic":
            return self._dynamic.get((module_name, endpoint))
        return self._list.get((module_name, endpoint)) or _BUILTIN_LIST.get(endpoint)

    async def call(self, module_name: str, endpoint: str, kind: str, module, bot, field, value=None) -> dict:
        """调用数据源。module 为对应 bot_id 的模块实例。"""
        handler = self.get(module_name, endpoint, kind)
        if handler is None:
            return {}
        try:
            if kind == "dynamic":
                result = handler(module, field, bot, value)
            else:
                result = handler(module, field, bot)
            if inspect.isawaitable(result):
                result = await result
            return result or {}
        except Exception as e:
            self.log.error(f"[Provider] {module_name}/{endpoint}({kind}) 调用异常: {e}")
            return {}
