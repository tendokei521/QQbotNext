"""技能（Skill）注册：把模块能力说明注入 LLM system prompt。

技能不是 function calling，而是“告诉模型何时用、怎么做”的指令块。
- 模块声明 `SKILLS` 字典或 `@skill` 装饰器；
- 框架按模块启停 + `skills_enabled` 配置过滤；
- 渲染成 `### 技能：<name>` 的 prompt 块。
"""

from __future__ import annotations

from typing import Any, Callable

from app.core.logger import logger


def skill(
    *,
    name: str = "",
    description: str = "",
    instructions: str = "",
    tools: list[str] | None = None,
    examples: list[dict] | None = None,
) -> Callable:
    """装饰器：把模块方法声明为一个技能（方法本身不会被调用，仅读取元数据）。

    用法：

    .. code-block:: python

        from app.llm import skill

        class Module(BaseModule):
            @skill(name="周报助手", description="写周报时使用", instructions="1. 收集数据 2. 三节输出")
            async def weekly_report(self): ...
    """

    def decorator(fn):
        setattr(fn, "__skill_meta__", {
            "name": name or fn.__name__,
            "description": description or (fn.__doc__ or "").strip(),
            "instructions": instructions,
            "tools": list(tools or []),
            "examples": list(examples or []),
        })
        return fn

    return decorator


def _format_skill(entry: dict) -> str:
    lines = ["### 技能：" + str(entry.get("name") or "")]
    if entry.get("description"):
        lines.append("适用：" + str(entry["description"]))
    if entry.get("instructions"):
        lines.append("执行步骤：")
        lines.append(str(entry["instructions"]))
    tools = entry.get("tools") or []
    if tools:
        lines.append("可用工具：" + ", ".join(str(t) for t in tools))
    examples = entry.get("examples") or []
    if examples:
        lines.append("示例：")
        for ex in examples:
            if isinstance(ex, dict):
                if ex.get("input") is not None:
                    lines.append(f"输入：{ex['input']}")
                if ex.get("output") is not None:
                    lines.append(f"输出：{ex['output']}")
            else:
                lines.append(str(ex))
    return "\n".join(lines)


class SkillRegistry:
    """按 AgentRuntime 保存模块技能；模块加载/卸载时注册/注销。"""

    def __init__(self, log=None) -> None:
        self._entries: list[dict] = []
        self.log = log or logger

    def register_module(self, module) -> int:
        records: list[dict] = []
        cls = type(module)
        for klass in reversed(cls.__mro__):
            for _name, attr in vars(klass).items():
                meta = getattr(attr, "__skill_meta__", None)
                if meta:
                    records.append(dict(meta))
        for name, raw in (getattr(cls, "SKILLS", {}) or {}).items():
            if isinstance(raw, str):
                records.append({"name": name, "instructions": raw})
            elif isinstance(raw, dict):
                records.append({"name": name, **raw})
            else:
                continue

        made = 0
        for rec in records:
            entry = {
                "module": module,
                "name": str(rec.get("name") or ""),
                "description": str(rec.get("description") or ""),
                "instructions": str(rec.get("instructions") or ""),
                "tools": list(rec.get("tools") or []),
                "examples": list(rec.get("examples") or []),
            }
            if not entry["name"]:
                continue
            self._entries.append(entry)
            made += 1
        if made:
            self.log.debug(f"[Skill] 模块 {module.module_name} 注册 {made} 个技能")
        return made

    def unregister_module(self, module) -> int:
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.get("module") is not module]
        return before - len(self._entries)

    def all_entries(self) -> list[dict]:
        return list(self._entries)

    def prompt_blocks(self) -> list[str]:
        """按「模块启用 + skills_enabled 配置」过滤，返回注入 system prompt 的技能块。"""
        blocks = []
        for entry in self._entries:
            module = entry.get("module")
            if module is None:
                blocks.append(_format_skill(entry))
                continue
            authority = getattr(module, "authority", None)
            if authority is not None and not getattr(authority, "enabled", True):
                continue
            config = getattr(module, "config", None)
            enabled_map = config.get("skills_enabled", {}) if config is not None else {}
            if isinstance(enabled_map, dict) and "all" in enabled_map and not enabled_map.get("all"):
                continue
            if isinstance(enabled_map, dict) and entry["name"] in enabled_map and not enabled_map.get(entry["name"]):
                continue
            blocks.append(_format_skill(entry))
        return blocks
