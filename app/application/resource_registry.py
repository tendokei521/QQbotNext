"""系统资源注册表：把框架里「可被引用的资源」统一成 kind → 候选列表。

**为什么要有它**：模块想在配置界面里「选系统的角色预设 / Provider 模型 / 配置档案」时，
原先只能手抄 ID（或自己写一个 dynamic 数据源转手去取），而前端也没有通用的候选拉取通道。

本注册表提供三件事：

- **统一目录**：``GET /api/resources`` → 已注册的资源种类（供诊断与能力清单）；
- **统一候选**：``GET /api/resources/{kind}`` → ``[{"value", "label"}]``，供 schema 里
  ``type: "resource"`` 字段的下拉框；
- **服务端消费**：``ctx.services.resources`` → 模块也能拿到同一份候选（不必自己拼）。

与 ``ProviderRegistry``（list/dynamic 的**模块数据源**）的区别：那个是「模块自己的列表」
（群 / 好友 / 自定义），这个是「框架资源目录」。顺带把 ``provider`` 这个词的歧义收敛掉：
LLM 侧叫服务 provider（``ctx.services.llm``），数据源侧叫 ``ctx.services.data_sources``。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.logger import logger


@dataclass(frozen=True)
class ResourceKind:
    """一种可被引用的资源。"""

    kind: str
    label: str
    desc: str = ""
    #: 取资源对象的哪个字段作为存储值（默认 ``id``）
    value_field: str = "id"
    #: 取哪个字段作为显示名（默认 ``name``，缺省时依次回退 name/title/value）
    label_field: str = "name"
    #: 该种类是否需要 bot 上下文（例如"某账号下的群"）
    needs_bot: bool = False


@dataclass
class ResourceQuery:
    """一次候选查询的上下文。"""

    kind: str
    bot_id: Any = None
    filters: dict = field(default_factory=dict)


#: 资源 handler：``(ResourceQuery) -> list[dict] | dict``（同步/异步均可）
ResourceHandler = Callable[[ResourceQuery], Any]


class ResourceRegistry:
    """进程内单例，由容器注入（与 ProviderRegistry 同一层级）。"""

    def __init__(self, log=None) -> None:
        self._entries: dict[str, tuple[ResourceKind, ResourceHandler]] = {}
        self.log = log or logger

    # ── 注册 ──────────────────────────────────────────────
    def register(
        self,
        kind: str,
        handler: ResourceHandler,
        *,
        label: str = "",
        desc: str = "",
        value_field: str = "id",
        label_field: str = "name",
        needs_bot: bool = False,
    ) -> ResourceKind:
        """注册一种资源。同名重复注册会覆盖（便于插件替换默认实现）。"""
        key = str(kind or "").strip()
        if not key:
            raise ValueError("资源种类不能为空")
        meta = ResourceKind(
            kind=key,
            label=str(label or key),
            desc=str(desc or ""),
            value_field=str(value_field or "id"),
            label_field=str(label_field or "name"),
            needs_bot=bool(needs_bot),
        )
        self._entries[key] = (meta, handler)
        self.log.debug(f"[Resource] 已注册资源种类: {key}（{meta.label}）")
        return meta

    def unregister(self, kind: str) -> None:
        self._entries.pop(str(kind or "").strip(), None)

    # ── 查询 ──────────────────────────────────────────────
    def has(self, kind: str) -> bool:
        return str(kind or "").strip() in self._entries

    def get(self, kind: str) -> ResourceKind | None:
        entry = self._entries.get(str(kind or "").strip())
        return entry[0] if entry else None

    def kinds(self) -> list[dict]:
        """全部资源种类的元信息（供前端与能力清单）。"""
        return [
            {
                "kind": meta.kind,
                "label": meta.label,
                "desc": meta.desc,
                "needs_bot": meta.needs_bot,
            }
            for meta, _handler in sorted(self._entries.values(), key=lambda item: item[0].kind)
        ]

    async def call(self, kind: str, *, bot_id: Any = None, filters: dict | None = None) -> list[Any]:
        """调用 handler 拿原始资源对象；未注册/异常一律返回空列表并留痕。"""
        key = str(kind or "").strip()
        entry = self._entries.get(key)
        if entry is None:
            self.log.warning(f"[Resource] 未注册的资源种类: {key or '(空)'}")
            return []
        _meta, handler = entry
        try:
            result = handler(ResourceQuery(kind=key, bot_id=bot_id, filters=dict(filters or {})))
            if inspect.isawaitable(result):
                result = await result
        except Exception as e:  # noqa: BLE001 —— 候选拉取失败不该让整个表单/请求炸掉
            self.log.error(f"[Resource] {key} 候选拉取异常: {e}")
            return []
        if isinstance(result, dict):
            result = result.get("items") or result.get("options") or []
        return list(result or [])

    async def options(self, kind: str, *, bot_id: Any = None, filters: dict | None = None) -> list[dict]:
        """调用 handler 并归一化成 ``[{"value", "label"}]``（前端下拉直接可用）。"""
        meta = self.get(kind)
        if meta is None:
            return []
        rows = await self.call(kind, bot_id=bot_id, filters=filters)
        options: list[dict] = []
        for obj in rows:
            if isinstance(obj, str):
                options.append({"value": obj, "label": obj})
                continue
            if not isinstance(obj, dict):
                continue
            value = str(obj.get(meta.value_field) or obj.get("id") or obj.get("value") or "").strip()
            if not value:
                continue
            label = str(
                obj.get(meta.label_field) or obj.get("name") or obj.get("title") or value
            )
            options.append({"value": value, "label": label})
        return options
