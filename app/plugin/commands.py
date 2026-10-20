"""模块层的统一命令解析与分发（`@command` / `@command_group`）。

## 它解决什么

模块要响应「输入某个词就干某件事」时，此前只能自己写 ``@module_hook`` + 文本匹配，
每家各写一遍解析（前缀、空白、别名、参数切分、大小写），还容易忘记 ``event.llm.stop()``
——忘了就会出现「命令回完，LLM 又跟着搭一句」。这里把这套东西收成一处：

- **解析**：前缀 / 空白归一 / 别名 / 多词命令（命令组）/ 参数切分，只有一份实现；
- **分发**：命中后由框架直接调你的处理器，并把解析结果挂在 ``event.command`` 上；
- **截断 LLM**：隐藏参数 ``skip_llm`` **默认 True** —— 命中命令即 ``event.llm.stop()``，
  本轮不再进 LLM；确实想让模型接着说话时显式写 ``skip_llm=False``。

## 参数口径（对齐 AstrBot 的 ``filter.command`` / ``filter.command_group``）

```python
from app.plugin import BaseModule, command, command_group

class Module(BaseModule):
    @command("log", description="发送本轮次 user 日志")
    async def log(self, event):
        ...                                   # 默认已挡掉 LLM

    @command("poke", aliases=("戳",), skip_llm=False)
    async def poke(self, event):
        ...                                   # 发一条回应后，本轮照常交给 LLM

    @command_group("memory", description="长期记忆")
    async def memory(self, event):            # 只发「memory」时走这里（通常打印子命令表）
        ...

    @memory.command("list", description="列出记忆")
    async def memory_list(self, event):
        args = event.command.args              # ["list"] 之后的参数
```

| 参数 | 含义 |
|---|---|
| ``name`` | 命令词；命令组会拼成 ``"<组> <子命令>"``（可多词） |
| ``aliases`` | 别名（同样受前缀约束） |
| ``description`` | 帮助用说明（``#llm help`` 式的清单可直接渲染） |
| ``group`` | 所属命令组（一般由 ``@xxx.command`` 自动填） |
| ``skip_llm`` | **隐藏参数**，默认 ``True``＝命中后截断 LLM；``False``＝照常交给 LLM |
| ``order`` | 同一模块内多条命令的优先级（越小越先；只在同分命中时有意义） |
| ``prefixes`` | 唤醒前缀，默认 ``("/", "／")``；传 ``("", ...)`` 允许不带前缀 |

## 分发时机

入站节点链：``router(100) → permission(110) → command(115) → invoke(120) → agent(130)``。

- 命令节点在**权限过滤之后**：``permission="owner"`` 之类的模块，命令一样受权限约束；
- 命令节点在**模块通用钩子之前**，也在 **LLM 兜底之前**：所以命令能挡住 LLM，
  且 ``@module_hook`` 的观察者（如 group_log）照常收到事件；
- 同一条消息命中多条命令时，只跑**最具体**的那条：先比命令词数（``memory list``
  胜过 ``memory``），再比字符长度，然后比 ``order``、模块名。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

#: 默认唤醒前缀：``/log``、``／log``（中文输入法下的全角斜杠）都算
DEFAULT_PREFIXES: tuple[str, ...] = ("/", "／")

_WS_RE = re.compile(r"\s+")


# ==================== 声明 ====================


@dataclass(frozen=True)
class CommandSpec:
    """一条命令的声明（装饰器收集，运行期只读）。"""

    name: str
    aliases: tuple[str, ...] = ()
    description: str = ""
    group: str = ""
    #: 隐藏参数：命中命令后是否截断 LLM（默认截断）
    skip_llm: bool = True
    order: int = 100
    prefixes: tuple[str, ...] = DEFAULT_PREFIXES

    @property
    def full_names(self) -> tuple[str, ...]:
        """命令名 + 别名（长名字优先，便于 ``memory list`` 先于 ``memory`` 命中）。"""
        names = (self.name, *self.aliases)
        return tuple(sorted({n for n in names if n}, key=len, reverse=True))


@dataclass
class CommandInvocation:
    """一次命中的解析结果（挂在 ``event.command`` 上，处理器与后续模块都可读）。"""

    name: str
    args: list[str]
    text: str
    prefix: str
    module: str
    skip_llm: bool

    def __str__(self) -> str:  # 便于日志
        return f"{self.prefix}{self.name}" + (f" {' '.join(self.args)}" if self.args else "")


@dataclass
class CommandMatch:
    """命中的命令 + 处理器 + 解析结果。"""

    spec: CommandSpec
    handler: Callable
    module: Any
    matched_name: str
    args: list[str]
    text: str
    prefix: str

    @property
    def skip_llm(self) -> bool:
        return bool(self.spec.skip_llm)

    @property
    def module_name(self) -> str:
        return str(getattr(self.module, "module_name", "") or "")

    @property
    def score(self) -> tuple[int, int, int, str]:
        """同一条消息多条命中时的排序键（越小越优先）。"""
        return (-len(self.matched_name.split()), -len(self.matched_name), int(self.spec.order), self.module_name)

    def as_invocation(self) -> CommandInvocation:
        return CommandInvocation(
            name=self.matched_name,
            args=list(self.args),
            text=self.text,
            prefix=self.prefix,
            module=self.module_name,
            skip_llm=self.skip_llm,
        )


def command(
    name: str,
    *,
    aliases: Iterable[str] = (),
    description: str = "",
    group: str = "",
    skip_llm: bool = True,
    order: int = 100,
    prefixes: Iterable[str] | None = None,
) -> Callable:
    """声明一条命令。

    Args:
        name: 命令词（命令组里用 ``@grp.command("sub")`` 自动拼成 ``"grp sub"``）
        aliases: 别名
        description: 说明（供帮助清单渲染）
        group: 所属命令组名（一般不用手填）
        skip_llm: **隐藏参数**，命中后是否截断 LLM，默认 ``True``
        order: 同分命中时的优先级（越小越先）
        prefixes: 唤醒前缀，默认 ``("/", "／")``
    """
    spec = CommandSpec(
        name=str(name).strip(),
        aliases=tuple(a for a in aliases if str(a).strip()),
        description=description,
        group=group,
        skip_llm=bool(skip_llm),
        order=int(order),
        prefixes=tuple(prefixes) if prefixes is not None else DEFAULT_PREFIXES,
    )

    def decorator(fn: Callable) -> Callable:
        metas = list(getattr(fn, "__command_meta__", []) or [])
        metas.append(spec)
        fn.__command_meta__ = metas
        return fn

    return decorator


class _GroupBuilder:
    """命令组上的 ``.command()`` / ``.group()`` 注册入口（对齐 AstrBot 的级联写法）。"""

    def __init__(self, full_name: str, template: CommandSpec) -> None:
        self.full_name = full_name
        self.template = template

    def command(
        self,
        sub: str,
        *,
        aliases: Iterable[str] = (),
        description: str = "",
        skip_llm: bool | None = None,
        order: int | None = None,
        prefixes: Iterable[str] | None = None,
    ) -> Callable:
        """给命令组挂一条子命令（``skip_llm`` 缺省继承组上的设定）。"""
        sub = str(sub).strip()
        return command(
            f"{self.full_name} {sub}",
            aliases=tuple(f"{self.full_name} {a}".strip() for a in aliases),
            description=description,
            group=self.full_name,
            skip_llm=self.template.skip_llm if skip_llm is None else skip_llm,
            order=self.template.order if order is None else order,
            prefixes=self.template.prefixes if prefixes is None else prefixes,
        )

    def group(
        self,
        sub: str,
        *,
        aliases: Iterable[str] = (),
        description: str = "",
        skip_llm: bool | None = None,
        order: int | None = None,
        prefixes: Iterable[str] | None = None,
    ) -> Callable:
        """挂一个子命令组（可继续级联 ``.command()`` / ``.group()``）。"""
        return _group_decorator(
            f"{self.full_name} {str(sub).strip()}",
            aliases=tuple(f"{self.full_name} {a}".strip() for a in aliases),
            description=description,
            group=self.full_name,
            skip_llm=self.template.skip_llm if skip_llm is None else skip_llm,
            order=self.template.order if order is None else order,
            prefixes=self.template.prefixes if prefixes is None else prefixes,
        )


def _group_decorator(
    full_name: str,
    *,
    aliases: Iterable[str] = (),
    description: str = "",
    group: str = "",
    skip_llm: bool = True,
    order: int = 100,
    prefixes: Iterable[str] | None = None,
) -> Callable:
    """把一个函数登记成「命令组」，并把 ``.command()`` / ``.group()`` 挂上去。"""
    spec = CommandSpec(
        name=full_name,
        aliases=tuple(a for a in aliases if str(a).strip()),
        description=description,
        group=group,
        skip_llm=bool(skip_llm),
        order=int(order),
        prefixes=tuple(prefixes) if prefixes is not None else DEFAULT_PREFIXES,
    )

    def decorator(fn: Callable) -> Callable:
        metas = list(getattr(fn, "__command_meta__", []) or [])
        metas.append(spec)
        fn.__command_meta__ = metas
        builder = _GroupBuilder(full_name, spec)
        fn.command = builder.command
        fn.group = builder.group
        return fn

    return decorator


def command_group(
    name: str,
    *,
    aliases: Iterable[str] = (),
    description: str = "",
    skip_llm: bool = True,
    order: int = 100,
    prefixes: Iterable[str] | None = None,
) -> Callable:
    """声明一个顶层命令组。

    组名本身也是一条命令：只发组名（``/memory``）时执行被装饰的函数——
    通常在里面渲染子命令表（可用 :func:`render_group_help`）。
    """
    return _group_decorator(
        str(name).strip(),
        aliases=aliases,
        description=description,
        skip_llm=skip_llm,
        order=order,
        prefixes=prefixes,
    )


# ==================== 解析 ====================


def normalize_text(text: str) -> str:
    """空白归一（``\\s+`` → 单个空格）并去首尾——命令匹配的唯一入口口径。"""
    return _WS_RE.sub(" ", (text or "").strip())


def strip_prefix(text: str, prefixes: Iterable[str] = DEFAULT_PREFIXES) -> tuple[str, str] | None:
    """剥掉唤醒前缀，返回 (剩余文本, 实际前缀)；剥不出返回 None。

    长前缀优先匹配；``prefixes`` 里含 ``""`` 表示允许不带前缀（裸词）。
    剥完为空（只发了个 ``/``）视为不命中。
    """
    candidates = [p for p in dict.fromkeys(prefixes) if p]
    for prefix in sorted(candidates, key=len, reverse=True):
        if text.startswith(prefix):
            rest = text[len(prefix) :].strip()
            return (rest, prefix) if rest else None
    if "" in tuple(prefixes):
        return text, ""
    return None


def match_spec(text: str, spec: CommandSpec) -> CommandMatch | None:
    """单条命令的匹配（命令名 / 别名，多词命令按词边界对齐，大小写不敏感）。"""
    norm = normalize_text(text)
    if not norm:
        return None
    stripped = strip_prefix(norm, spec.prefixes)
    if stripped is None:
        return None
    rest, prefix = stripped

    for name in spec.full_names:
        want = normalize_text(name).casefold()
        if not want:
            continue
        haystack = rest.casefold()
        if haystack == want:
            return CommandMatch(spec=spec, handler=None, module=None, matched_name=name, args=[], text="", prefix=prefix)
        if haystack.startswith(want + " "):
            tail = rest[len(want) + 1 :].strip()
            return CommandMatch(
                spec=spec,
                handler=None,
                module=None,
                matched_name=name,
                args=tail.split(" ") if tail else [],
                text=tail,
                prefix=prefix,
            )
    return None


def iter_module_commands(modules: Iterable[Any]) -> list[tuple[CommandSpec, Callable, Any]]:
    """展开若干模块声明的命令：``[(spec, handler, module), ...]``。"""
    entries: list[tuple[CommandSpec, Callable, Any]] = []
    for module in modules or []:
        for spec, handler in getattr(module, "_commands", None) or []:
            entries.append((spec, handler, module))
    return entries


def best_match(
    text: str,
    entries: Iterable[tuple[CommandSpec, Callable, Any]],
) -> CommandMatch | None:
    """在若干条命令里挑出最具体的一条命中（同分按 order / 模块名）。"""
    best: CommandMatch | None = None
    for spec, handler, module in entries:
        matched = match_spec(text, spec)
        if matched is None:
            continue
        matched.handler = handler
        matched.module = module
        if best is None or matched.score < best.score:
            best = matched
    return best


# ==================== 帮助渲染 ====================


def render_group_help(
    entries: Iterable[tuple[CommandSpec, Callable, Any]],
    group: str,
    *,
    prefix: str = "/",
) -> str:
    """把一个命令组的子命令渲染成清单（组处理器里直接 ``await event.reply(...)``）。"""
    group = normalize_text(group)
    rows: list[str] = []
    for spec, _handler, _module in entries:
        if not spec.group or normalize_text(spec.group) != group:
            continue
        sub = normalize_text(spec.name)[len(group) :].strip() or normalize_text(spec.name)
        desc = f" — {spec.description}" if spec.description else ""
        rows.append(f"{prefix}{group} {sub}{desc}")
    if not rows:
        return f"「{group}」下暂时没有子命令。"
    return "\n".join([f"【{group}】", *sorted(rows)])
