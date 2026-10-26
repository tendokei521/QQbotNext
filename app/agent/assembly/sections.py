"""提示词 section 注册表：让「加一段提示词」不必接管整个装配器。

## 为什么有这个模块

改造前 ``assembler.py`` 用一张硬编码块表（12 个默认段 + 兼容的 ``extra`` 通道），
插件想加一段提示词只有两条路：塞 ``PromptRequest.extra_blocks``（位置固定在历史之前、
不能选顺序），或整体替换装配器（``supersedes=("assembly",)``，等于把既有 12 块全抄一遍）。

现在默认 12 段迁到 ``builtin_sections.py`` 并注册进本表，``PromptAssembler.build()``
改为「解析注册表 → 逐段生成 → 折叠/清洗」，插件只需 ``register_section(runtime, Section(...))``
加自己那一段，不必接管整个装配器。

```
runtime.prompt_sections（按 runtime 实例隔离）      ← registry_of(runtime)
      ↓ resolve()
[Section(order 升序, agent 遮蔽同名 global)]
      ↓ 逐段 builder(req)
[(段, messages)] → 折叠(single) → place_images → sanitize
```

## 口径（写死在这里，测试锁定）

- **order 决定顺序**，升序；同 order 按 name 排（同一输入必然同一字节，便于快照/前缀缓存）；
- **agent 遮蔽 global**：同 name 的 agent 段覆盖 global 段。global 段适合「所有 bot 通用」
  的注入（例如统一的安全提醒），单个 bot 想改就用同名 agent 段覆盖；
- **complete 段独占整条 system**：一次解析里最多允许一个。两条「整条 system」同时进
  messages 只会拼出畸形 prompt，所以 ``resolve()`` 直接抛 ``ValueError``（装配器再中止本轮）；
- **注册表挂在 ``runtime.prompt_sections`` 上**，按 runtime 实例隔离（同一个 bot 的插件注册
  互不串、不同 bot 也不串）。没有 runtime（单测/直调的 ``PromptRequest(runtime=None)``）
  或运行时挂不上属性时，退回**进程级默认表**——只含默认段，不装任何 bot 的插件注册。

## 失败语义

- ``resolve()`` 发现 complete 冲突 → 抛 ``ValueError``；调用方（装配器）负责 ``logger.error``
  留痕并中止本轮，绝不发畸形 prompt；
- 单段 builder 抛异常**不归注册表处理**：注册表只负责「谁、什么顺序、是否冲突」，
  builder 失败由装配器按「跳过该段 + error 日志」兜底（理由见 ``assembler.build_all_blocks``）。

## 卸载纪律

``register_section`` 之后必须成对调用 ``unregister_section``（通常在模块 ``on_unload``/
``on_disable`` 里），否则单账号重载模块会越积越多段。卸载只撤同名段；若该名字是覆盖了
默认段的，撤掉覆盖后自动恢复内置版本（默认段不会因为一次覆盖而永久消失）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.logger import logger

#: agent 作用域：只影响声明它的那个 runtime（默认）
SCOPE_AGENT = "agent"
#: global 作用域：同 runtime 上所有解析都可见，可被同名 agent 段遮蔽
SCOPE_GLOBAL = "global"
SCOPES: tuple[str, ...] = (SCOPE_AGENT, SCOPE_GLOBAL)

#: 挂到 runtime 上的属性名（与 ``runtime.capabilities`` 相同的「挂实例」思路）
RUNTIME_ATTR = "prompt_sections"


@dataclass(frozen=True)
class Section:
    """一段提示词：名称 + 顺序 + 生成函数。

    - ``name``：稳定标识，``describe()`` 与日志都用它；同名同 scope 视为同一段（覆盖）；
    - ``order``：升序排列，10 的倍数留给内置段，插件从 200 起（见 MODULE_DEV §4.7.4）；
    - ``builder``：``(req) -> list[dict]``，返回该段消息；不适用时返回 ``[]``（不产生空消息）；
    - ``scope``：``agent`` / ``global``，同名时 agent 遮蔽 global；
    - ``complete``：``True`` 表示该段声明自己产出整条 system（一次解析最多一个，冲突即报错）；
    - ``source``：声明者（模块名 / 内置来源），只用于日志与排查，不参与排序。
    """

    name: str
    order: int
    builder: Callable[[Any], list[dict]]
    scope: str = SCOPE_AGENT
    complete: bool = False
    source: str = ""

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        if not name:
            raise ValueError("Section.name 不能为空")
        try:
            order = int(self.order)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Section.order 必须是整数: {self.order!r}") from e
        if self.scope not in SCOPES:
            raise ValueError(f"Section.scope 必须是 {SCOPES} 之一: {self.scope!r}")
        if not callable(self.builder):
            raise TypeError(f"Section.builder 必须可调用: {self.name}")
        # frozen：规范化后的值只能走 object.__setattr__
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "order", order)
        object.__setattr__(self, "scope", str(self.scope))

    @property
    def key(self) -> tuple[str, str]:
        """去重/覆盖用的键：``(scope, name)``。"""
        return (self.scope, self.name)


class SectionRegistry:
    """一组 section：按 ``(scope, name)`` 去重、按 order 解析成有序列表。

    ``defaults=True``（默认）时，首次使用（add / remove / resolve）惰性注入
    ``builtin_sections.default_sections()`` —— 保证「默认段总是存在」，但注入不覆盖
    调用方在注入前显式注册的同名段（插件替换内置段时以插件为准）。
    """

    def __init__(self, *, defaults: bool = True) -> None:
        self._sections: dict[tuple[str, str], Section] = {}
        self._builtin: dict[tuple[str, str], Section] = {}
        self._defaults_loaded = False
        self._with_defaults = defaults

    # ── 默认段注入 ──────────────────────────────────────

    def _ensure_defaults(self) -> None:
        """首次使用时注入默认段（只注入一次；调用方同名段优先）。"""
        if self._defaults_loaded:
            return
        # 先置位：注入过程若再触发 add/remove 不会递归
        self._defaults_loaded = True
        if not self._with_defaults:
            return
        from app.agent.assembly.builtin_sections import default_sections

        self._builtin = {section.key: section for section in default_sections()}
        for key, section in self._builtin.items():
            self._sections.setdefault(key, section)

    # ── 增删 ────────────────────────────────────────────

    def add(self, section: Section) -> None:
        """注册（或覆盖）一段：同名同 scope 幂等覆盖，不产生重复段。"""
        if not isinstance(section, Section):
            raise TypeError(f"add() 需要 Section，收到 {type(section).__name__}")
        self._ensure_defaults()
        self._sections[section.key] = section

    def remove(self, name: str, *, scope: str | None = None) -> int:
        """撤掉同名段，返回实际撤掉的条数（``scope=None`` = 两个作用域都撤）。

        撤掉的是「对默认段的覆盖」时，自动恢复内置版本——默认段不会因为一次覆盖而
        永久消失；显式撤掉内置段本身则真的移除（该注册表内不再出现）。
        """
        self._ensure_defaults()
        name = str(name or "").strip()
        if not name:
            return 0
        keys = [
            key
            for key in self._sections
            if key[1] == name and (scope is None or key[0] == scope)
        ]
        for key in keys:
            removed = self._sections.pop(key, None)
            builtin = self._builtin.get(key)
            if removed is not None and builtin is not None and removed is not builtin:
                self._sections[key] = builtin
        return len(keys)

    # ── 解析 ────────────────────────────────────────────

    def resolve(self, *, scope: str = SCOPE_AGENT) -> list[Section]:
        """解析出有序、无冲突的段列表。

        - ``scope="agent"``（默认）：global 段 + agent 段，同名 agent 遮蔽 global；
        - ``scope="global"``：只看 global 段；
        - 按 ``(order, name)`` 升序（name 兜底让同 order 也稳定）；
        - 解析结果里 ``complete=True`` 超过一个 → ``ValueError``。

        返回的是新列表（调用方可自由排序/过滤），Section 本身不可变。
        """
        if scope not in SCOPES:
            raise ValueError(f"resolve(scope=...) 必须是 {SCOPES} 之一: {scope!r}")
        self._ensure_defaults()
        if scope == SCOPE_GLOBAL:
            visible = [s for s in self._sections.values() if s.scope == SCOPE_GLOBAL]
        else:
            # global 打底，agent 按 name 覆盖
            by_name = {
                s.name: s for s in self._sections.values() if s.scope == SCOPE_GLOBAL
            }
            by_name.update(
                {s.name: s for s in self._sections.values() if s.scope == SCOPE_AGENT}
            )
            visible = list(by_name.values())
        resolved = sorted(visible, key=lambda section: (section.order, section.name))
        complete = [section for section in resolved if section.complete]
        if len(complete) > 1:
            names = ", ".join(f"{s.name}(order={s.order})" for s in complete)
            raise ValueError(
                f"complete 段（独占整条 system）最多只能有一个，当前解析到 "
                f"{len(complete)} 个: {names}"
            )
        return resolved

    def __len__(self) -> int:
        self._ensure_defaults()
        return len(self._sections)


# ── 运行时取用（都带「没有就挂一个」的兜底）─────────────────

#: 进程级默认表：只在「没有 runtime 可挂」时使用（单测 / 直调路径）
_PROCESS_DEFAULT: SectionRegistry | None = None


def _process_default() -> SectionRegistry:
    global _PROCESS_DEFAULT
    if _PROCESS_DEFAULT is None:
        _PROCESS_DEFAULT = SectionRegistry()
    return _PROCESS_DEFAULT


def registry_of(runtime: Any) -> SectionRegistry:
    """取该 runtime 的 section 注册表（没有就建一个挂上去）。

    与 ``capabilities.registry_of`` 同一写法：注册表跟着 runtime 实例走，天然按 bot 隔离。
    ``runtime is None``（无运行时上下文的直调/单测）时退回进程级默认表；挂不上属性
    （只读替身等）时返回临时表并留 debug 日志，保证组装不因注册表挂载失败而炸掉。
    """
    if runtime is None:
        return _process_default()
    registry = getattr(runtime, RUNTIME_ATTR, None)
    if isinstance(registry, SectionRegistry):
        return registry
    registry = SectionRegistry()
    try:
        setattr(runtime, RUNTIME_ATTR, registry)
    except Exception as e:  # noqa: BLE001 —— 只读替身上挂不了属性：用临时的
        logger.debug(f"[Section] 运行时挂不上 section 注册表（用临时表继续）: {e}")
    return registry


def register_section(runtime: Any, section: Section) -> None:
    """给该 runtime 注册一段提示词（同 runtime 内同名同 scope 覆盖；幂等）。"""
    registry_of(runtime).add(section)
    logger.debug(
        f"[Section] 注册提示词段 {section.name}(order={section.order}, "
        f"scope={section.scope}, complete={section.complete}, source={section.source or '-'})"
    )


def unregister_section(runtime: Any, name: str, *, scope: str | None = None) -> int:
    """撤掉该 runtime 上同名段，返回撤掉的条数（``scope=None`` 撤两个作用域）。"""
    removed = registry_of(runtime).remove(name, scope=scope)
    if removed:
        logger.debug(f"[Section] 撤销提示词段 {name}(scope={scope or 'any'}): {removed} 条")
    return removed


def resolve_sections(runtime: Any, *, scope: str = SCOPE_AGENT) -> list[Section]:
    """便捷入口：``registry_of(runtime).resolve(scope=...)``。"""
    return registry_of(runtime).resolve(scope=scope)


__all__ = [
    "SCOPE_AGENT",
    "SCOPE_GLOBAL",
    "SCOPES",
    "Section",
    "SectionRegistry",
    "register_section",
    "registry_of",
    "resolve_sections",
    "unregister_section",
]
