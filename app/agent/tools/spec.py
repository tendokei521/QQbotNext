"""轻量工具调用层：模块工具扩展口 + 上下文 + 执行器。

原有 ToolSpec 只支持 `async (args) -> str`；生产化后：
- 工具处理器统一接收 ``ToolContext``（模块/机器人/会话/事件/运行时/触发者）；
- 模块通过 ``@tool`` 装饰器或 ``TOOLS`` 类属性声明工具；
- 执行器带超时、异常兜底与结果截断，避免把异常/超大结果塞回 LLM 上下文；
- 工具可声明**呈现层**（``present_call`` / ``present_result``）：只改日志文本，
  不改返回给模型的字符串（Q6）。
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.agent.hooks import ToolCallContext
from app.agent.telemetry import ToolCallRecord
from app.agent.tools.result import (  # noqa: F401 - 重导出，便于调用方统一从 spec 取
    INVALID_ARGS,
    OUTPUT_LIMIT,
    PERMISSION_DENIED,
    TOOL_EXCEPTION,
    TOOL_TIMEOUT as TOOL_TIMEOUT_CODE,
    TOOL_UNAVAILABLE,
    UNKNOWN_TOOL,
    ToolResult,
    coerce,
)
from app.core.logger import logger

# 单次工具执行超时（秒）
TOOL_TIMEOUT = 20
# 工具结果截断长度（防止 pollute 上下文）
TOOL_RESULT_MAX = 2000
# 工具失败约定：处理器以 "error: ..." 文本报告失败
ERROR_RESULT_PREFIX = "error:"


def is_error_result(result: Any) -> bool:
    """工具返回文本是否表示失败（统一约定：以 ``error:`` 开头）。

    约定见各工具实现（``error: 无权限调用工具 xxx`` / ``error: {name} 调用失败: retcode ...``）。
    """
    return str(result or "").lstrip().startswith(ERROR_RESULT_PREFIX)

# 工具级权限：与模块权限语义一致（private 下群管理/群主降级为 member）
TOOL_PERMISSION_RANK = {
    "everyone": 0,
    "member": 0,
    "group_admin": 1,
    "group_owner": 2,
    "owner": 3,
}

ToolHandler = Callable[["ToolContext", dict], Awaitable[str]]

# OpenAI function name 仅允许字母/数字/下划线/连字符
_TOOL_NAME_SAFE_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def sanitize_tool_name(name: str) -> str:
    """把工具名清洗成 OpenAI 认可的 function.name（^[a-zA-Z0-9_-]+$）。"""
    cleaned = _TOOL_NAME_SAFE_RE.sub("_", str(name or ""))
    return cleaned or "tool"


@dataclass
class ToolContext:
    """一次工具调用的运行上下文。

    - ``module``：声明该工具的模块实例
    - ``bot``：当前 Bot（可发消息/查群）
    - ``session_id``：会话 id（group_x / private_y）
    - ``event``：触发事件（主动/定时场景可能为 None）
    - ``runtime``：框架 AgentRuntime
    - ``user_id`` / ``group_id``：触发者 / 触发群
    """

    module: Any = None
    bot: Any = None
    session_id: str = ""
    event: Any = None
    runtime: Any = None
    user_id: Any = None
    group_id: Any = None
    extra: dict = field(default_factory=dict)


class ToolSpec:
    """单个工具定义：名称 + 描述 + 参数 JSON Schema + 处理器。"""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: ToolHandler,
        module: Any = None,
        permission: str = "everyone",
        scopes: list[str] | tuple[str, ...] | None = None,
        source: str = "system",
        category: str = "",
        max_result: int | None = None,
        concurrency: str = "exclusive",
        concurrency_safe: Callable[[dict], bool] | None = None,
        timeout_s: float | None = None,
        sensitivity: str = "normal",
        present_call: Callable[[dict], str] | None = None,
        present_result: Callable[[ToolResult], str] | None = None,
    ) -> None:
        raw_name = str(name or "")
        self.name = sanitize_tool_name(raw_name)
        if self.name != raw_name:
            logger.warning(
                f"[Tool] 工具名 {raw_name!r} 不符合 OpenAI function.name 规范，已清洗为 {self.name!r}"
            )
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.module = module
        self.permission = (permission or "everyone").lower()
        self.scopes = tuple(s.lower() for s in (scopes or ("group", "private")) if s)
        self.source = source
        self.category = category
        # 结果长度上限：None=用全局默认；0=不截断（"把正文给我"类工具用）
        self.max_result = max_result
        # 并发分类：默认**独占**（fail-closed）。只有明确声明 parallel 才允许与其他工具
        # 同时执行；同一个处理器既读又写的工具（如 OneBot 的 _handler）必须用
        # ``concurrency_safe(args)`` 按**每次调用的实参**再判一次。
        self.concurrency = (concurrency or "exclusive").strip().lower()
        self.concurrency_safe = concurrency_safe
        # 单次执行的墙钟上限；None = 用全局默认（``tool_timeout_default_s``）
        self.timeout_s = timeout_s
        # 敏感级别（normal/high/critical）：高危 + 开了审批开关时，执行前必须先问人
        self.sensitivity = str(sensitivity or "normal").strip().lower()
        # Q6 呈现层：日志里怎么把"这次调用 / 这个结果"讲清楚。
        # None = 今天的日志行（``[ToolCall]调用工具：name`` / ``... result=<前200字>``）。
        # 渲染器只影响**日志文本**，绝不改变返回给模型的字符串（那是协议面）。
        self.present_call = present_call
        self.present_result = present_result

    def runs_parallel(self, args: dict) -> bool:
        """这次调用能否与其他工具并发（**fail-closed**：拿不准就按独占）。

        为什么必须按实参判：同一个 ToolSpec 可能既跑"查信息"也跑"发消息"
        （OneBot 的 ``_handler`` 就是这么复用的），按工具名分类会放过写操作。
        """
        if self.concurrency != "parallel":
            return False
        if self.concurrency_safe is None:
            return True
        try:
            return self.concurrency_safe(args or {}) is True
        except Exception:  # noqa: BLE001 —— 判定本身失败就当独占（宁可串行）
            return False

    def allows(self, ctx: ToolContext | None) -> bool:
        """工具级权限/作用域校验；主动/定时等无事件场景默认放行。"""
        if ctx is None:
            # 不携带调用上下文时保留旧行为（框架内部工具）
            return True
        return self.allowed_for_event(getattr(ctx, "event", None))

    def allowed_for_event(self, event: Any, *, scope: str | None = None) -> bool:
        """按**事件**判定权限与作用域（`allows()` 与 `resolve_tool_state` 共用同一份实现）。

        修正记录：``event is None`` 的放行判断此前排在**作用域检查之后**，于是
        没声明 ``scopes=("*",)`` 的工具（memory_* / knowledge_* / schedule_task…）
        在主动消息与定时任务路径上被当成"当前会话类型不在适用范围"而静默失败，
        与本文档字符串"无事件场景默认放行"自相矛盾。现在无事件一律在最前面放行：
        那类触发不归属于任何普通群成员，权限判定没有主语。

        ``scope`` 不传时按事件类型推断（群 / 私聊 / 通配）；传了就以调用方的判定为准
        （`resolve_tool_state` 的 ⑤ 层会显式传入）。
        """
        if event is None:
            return True
        if scope is None:
            scope = "*"
            event_type = getattr(event, "event_type", "") or ""
            if event_type == "message_group" or getattr(event, "group", None) is not None:
                scope = "group"
            elif event_type == "message_private" or getattr(event, "user_id", None):
                scope = "private"
        if "*" not in self.scopes and scope not in self.scopes:
            return False

        if self.permission == "everyone":
            return True

        role = getattr(event, "permission_role", None) or getattr(event, "role", None) or "member"
        if self.permission == "owner":
            return bool(getattr(event, "is_bot_owner", False))
        required = TOOL_PERMISSION_RANK.get(self.permission, 0)
        if self.permission in ("group_admin", "group_owner") and scope == "private":
            required = 0
        current = TOOL_PERMISSION_RANK.get(role, 0)
        return current >= required

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def build_tools(specs: list[ToolSpec]) -> list[dict]:
    """ToolSpec 列表 → OpenAI tools 参数。"""
    return [s.to_openai() for s in specs]


def _truncate_result(text: str, max_len: int | None = None) -> str:
    """按工具自身预算截断结果；``max_len=0`` 表示不截断，``None`` 用全局默认。"""
    text = str(text)
    budget = TOOL_RESULT_MAX if max_len is None else int(max_len)
    if budget <= 0 or len(text) <= budget:
        return text
    return text[:budget] + "\n…(结果过长已截断)"


def _present(renderer: Callable[[Any], str] | None, value: Any, *, what: str) -> str:
    """调用呈现层渲染器；没有渲染器 → 空串，渲染失败 → 空串。

    呈现是**日志增强**：它坏了不该让一次工具调用失败（也不该把 traceback 带进
    模型上下文），所以这里吞掉异常只留一条 debug。
    """
    if renderer is None:
        return ""
    try:
        return str(renderer(value) or "").strip()
    except Exception as e:  # noqa: BLE001 —— 呈现层失败按"没有呈现"处理
        logger.debug(f"[Tool] 呈现层 {what} 渲染失败（已忽略）: {e}")
        return ""


def make_executor(specs: list[ToolSpec], ctx: ToolContext | None = None) -> ToolHandler:
    """按工具名分发到对应处理器；带超时/异常兜底/结果截断。

    未来扩展：可在此处追加统一审计日志。
    """

    async def _executor(name: str, args: dict, *, sink: dict | None = None) -> str:
        for spec in specs:
            if spec.name != name:
                continue
            started = time.monotonic()
            error = ""
            success = True

            def _log_enabled() -> bool:
                if ctx is None:
                    return True
                runtime = getattr(ctx, "runtime", None)
                config = getattr(runtime, "config", None) if runtime is not None else None
                if config is None:
                    return True
                if spec.source == "onebot":
                    return bool(config.get("onebot_tools_log_enabled", True))
                if spec.source == "mcp":
                    return bool(config.get("mcp_tools_log_enabled", True))
                if spec.source == "module":
                    return bool(config.get("module_tools_log_enabled", True))
                return bool(config.get("system_tools_log_enabled", True))

            if _log_enabled():
                # Q6：有呈现层就把"调用了什么"渲染进日志行（默认行原样保留）
                call_note = _present(
                    spec.present_call, dict(args or {}), what=f"present_call({name})"
                )
                logger.add_info("Tool").info(
                    f"[ToolCall]调用工具：{name}" + (f" | {call_note}" if call_note else "")
                )
            # 执行前复核：清单是在本轮开始时组的，此刻开关可能已经变了
            # （模块被关掉、工具在总表里被关掉）。这里再问一次唯一判定点。
            gate_reason = _execution_gate(ctx, spec)
            if gate_reason:
                error = "unavailable"
                success = False
                result = f"error: 工具 {name} 当前不可用（{gate_reason}）"
            elif not spec.allows(ctx):
                error = "forbidden"
                success = False
                result = f"error: 无权限调用工具 {name}"
            else:
                # P6 人工审批：高危 OneBot 工具在开关打开时必须先问人。
                # fail-closed——没有通道就是拒绝，绝不存在"配了审批却悄悄放行"。
                approval_denied = ""
                runtime_for_approval = getattr(ctx, "runtime", None) if ctx is not None else None
                if runtime_for_approval is not None:
                    from app.agent.tools.approval import (
                        needs_approval,
                        rejection_message,
                        request_approval,
                    )

                    if needs_approval(runtime_for_approval, spec):
                        outcome = await request_approval(
                            runtime_for_approval, tool=name, args=args, ctx=ctx
                        )
                        if not outcome.allowed:
                            approval_denied = rejection_message(outcome)
                if approval_denied:
                    success = False
                    error = "approval_denied"
                    result = f"error: 工具 {name} 未获批准（{approval_denied}）"
                else:
                    timeout_s = _timeout_of(ctx, spec)
                    try:
                        # 不用 wait_for：它超时会 **cancel** 工具协程，把"发了一半的消息 /
                        # 写了一半的文件"掐在半路。这里只等，超时就把结果换成 TOOL_TIMEOUT，
                        # 让协程自己收敛（DSH 同款语义）。
                        task = asyncio.create_task(spec.handler(ctx, args))
                        done, _ = await asyncio.wait({task}, timeout=timeout_s)
                        if not done:
                            _orphan(task, name)
                            raise asyncio.TimeoutError
                        result = task.result()
                    except asyncio.TimeoutError:
                        success = False
                        error = f"timeout({timeout_s}s)"
                        result = f"error: TOOL_TIMEOUT 工具 {name} 执行超时（{timeout_s}s）"
                    except Exception as e:
                        success = False
                        error = str(e)
                        logger.add_info("Tool").warning(f"[Tool] {name} 执行异常: {e}")
                        result = f"error: 工具 {name} 执行异常: {e}"
            # 工具用 "error: ..." 文本报错（而非抛异常）时，此前会被记为 success=True，
            # 导致 1404/未连接之类的接口错误在日志与遥测里彻底不可见。
            if success and is_error_result(result):
                success = False
                error = "error_result"
            # P5：单一出口收敛成结构化结果（错误码由事实推导，不再靠前缀猜）。
            # 上游拿到的仍是字符串（三个 provider 的插入点只读 msg["content"]），
            # 所以这一版对 provider 零改动。
            tr = _to_tool_result(result, success=success, error=error)
            result = tr.plain()
            duration_ms = (time.monotonic() - started) * 1000
            if _log_enabled():
                # Q6：结果呈现层（默认仍然只有 result=<前200字>）
                result_note = _present(
                    spec.present_result, tr, what=f"present_result({name})"
                )
                logger.add_info("Tool").info(
                    f"[ToolCall]返回：{name} success={success} duration={duration_ms:.0f}ms "
                    f"error={error} code={tr.code} result={str(result)[:200]}"
                    + (f" | {result_note}" if result_note else "")
                )
            await _run_tool_call_hooks(
                ctx, spec, name, args, result, success, error, duration_ms, code=tr.code
            )
            runtime = getattr(ctx, "runtime", None) if ctx is not None else None
            telemetry = getattr(runtime, "telemetry", None) if runtime is not None else None
            if telemetry is not None:
                telemetry.record_tool(ToolCallRecord(
                    bot_id=getattr(runtime, "bot_id", None),
                    session_id=getattr(ctx, "session_id", "") if ctx is not None else "",
                    name=name,
                    success=success,
                    duration_ms=duration_ms,
                    error=error,
                ))
            # 结果侧的「回应要求」：拼在工具结果末尾（位置最贴近生成点），
            # 压住"工具返回后必写长串汇报"的倾向。详见 tool_loop 模块 docstring。
            # 放在截断**之后**，保证这条要求不会被长度上限砍掉；
            # 失败结果不追加（见 append_reply_directive）。
            # 发言通道工具（source="speech"）不追加：模型已经用 send_message 说过话了，
            # 再要求它"用自己口吻回应"只会让它把同一句再说一遍。
            # P5 spill：**先**看原文是否超阈值（砍过就取不回来了），超了落盘并把
            # 正文换成"预览 + 路径"；落盘失败只 warn、保留原文（增强不是必需品）。
            runtime_obj = getattr(ctx, "runtime", None) if ctx is not None else None
            spilled_to = ""
            if runtime_obj is not None:
                from app.agent.tools.spill import apply_spill

                result, spilled_to = apply_spill(
                    result,
                    runtime=runtime_obj,
                    session_id=getattr(ctx, "session_id", "") if ctx is not None else "",
                    tool_name=name,
                    source=spec.source,
                    preview_budget=min(int(spec.max_result or 4000), 4000),
                )
            # 已落盘时正文就是预览（自带取回方式与路径），不能再按 max_result 截断——
            # 那会把末尾的路径和说明切掉，等于白存。
            final = result if spilled_to else _truncate_result(result, spec.max_result)
            if spilled_to:
                # 结构化结果里也带上落盘位置：Q3 的 tool/result 事件与 pruner 要用
                from dataclasses import replace as _replace

                tr = _replace(tr, meta={**tr.meta, "spill": spilled_to})
            if sink is not None:
                sink["result"] = tr
            if runtime is not None and spec.source != "speech":
                from app.agent.loop.tool_loop import append_reply_directive, reply_directive
                from app.agent.policy import policy_config

                # 回应要求文案归 `llm_tools_extra` 模块所有（未接管时回退 agent 配置）
                final = append_reply_directive(
                    final, reply_directive(policy_config(runtime, "tools"))
                )
            return final
        return f"error: 未知工具 {name}"

    # 让 `tool_loop.execute_tool_batch` 能按 spec 判定并发（fail-closed）与池大小。
    # 挂在函数对象上而不是改签名：`make_executor` 的返回值是 `async (name, args)`，
    # 第三方执行器也符合这个形状，没有属性时按独占 + 串行处理即可。
    _executor.specs_by_name = {s.name: s for s in specs}
    _executor.parallel_max = _parallel_max_of(ctx)
    return _executor


def _parallel_max_of(ctx: Any, default: int = 8) -> int:
    """并发池大小（配置 ``tool_parallel_max``）；读不到就回默认，永远 ≥1。"""
    runtime = getattr(ctx, "runtime", None) if ctx is not None else None
    config = getattr(runtime, "config", None) if runtime is not None else None
    if config is None:
        return default
    try:
        return max(1, min(int(config.get("tool_parallel_max", default) or default), 32))
    except (TypeError, ValueError):
        return default


def _execution_gate(ctx: Any, spec: ToolSpec) -> str:
    """执行前的可用性复核；返回空串表示可以执行。

    与注入期用的是**同一个** `resolve_tool_state`，所以两侧不会打架。
    这里显式传 ``event``：判定链的第 ⑤ 层（权限/作用域）只有在拿到事件时才有主语，
    此前不传事件导致该层在**任何**调用点都是死代码（详见 `tools/state.py` 顶部说明）。
    """
    runtime = getattr(ctx, "runtime", None) if ctx is not None else None
    if runtime is None:
        return ""
    try:
        from app.agent.tools.state import resolve_tool_state

        event = getattr(ctx, "event", None)
        scope = "private" if getattr(event, "message_type", "") == "private" else "group"
        state = resolve_tool_state(runtime, spec, session_scope=scope, event=event)
        if state.available:
            return ""
        return state.reason_text
    except Exception as e:  # noqa: BLE001 —— 复核失败按可用处理（不因统计问题拦工具）
        logger.debug(f"[Tool] 执行前复核失败（{getattr(spec, 'name', '?')}），按可用处理: {e}")
        return ""


async def _run_tool_call_hooks(
    tool_ctx: ToolContext | None,
    spec: ToolSpec,
    name: str,
    args: dict,
    result: str,
    success: bool,
    error: str,
    duration_ms: float,
    *,
    code: str = "",
) -> None:
    """执行 LLM 工具调用后钩子（runtime.llm_tool_call_hooks）。"""
    if tool_ctx is None:
        return
    runtime = getattr(tool_ctx, "runtime", None)
    registry = getattr(runtime, "llm_tool_call_hooks", None) if runtime is not None else None
    if registry is None:
        return
    event = getattr(tool_ctx, "event", None)
    event_type = getattr(event, "event_type", "") if event is not None else ""
    bot_id = getattr(runtime, "bot_id", None)
    if bot_id is None and tool_ctx.bot is not None:
        bot_id = getattr(tool_ctx.bot, "bot_id", None)
    extra = {"error": error} if error else {}
    if code:
        # 稳定错误码（P5）：观测方可以按码聚合，不用解析中文文案
        extra["code"] = code
    call_ctx = ToolCallContext(
        name=name,
        args=dict(args or {}),
        result=str(result),
        success=success,
        duration_ms=duration_ms,
        tool_ctx=tool_ctx,
        module=spec.module,
        bot_id=bot_id,
        event_type=event_type,
        extra=extra,
    )
    await registry.run(call_ctx)


def tool(
    *,
    description: str = "",
    parameters: dict | None = None,
    permission: str = "everyone",
    scopes: list[str] | tuple[str, ...] | None = None,
) -> Callable:
    """装饰器：把一个模块方法声明为 LLM 工具。

    用法：

    .. code-block:: python

        from app.agent import tool

        class Module(BaseModule):
            @tool(description="查询天气", parameters={"type":"object","properties":{"city":{"type":"string"}}})
            async def query_weather(self, ctx: ToolContext, args: dict) -> str:
                return "晴"

    进阶权限/作用域：

        @tool(permission="group_admin", scopes=["group"])
        async def delete_message(self, ctx, args): ...
    """

    def decorator(fn):
        metas = getattr(fn, "__tool_meta__", [])
        metas.append({
            "description": description,
            "parameters": parameters or {"type": "object", "properties": {}},
            "permission": permission,
            "scopes": list(scopes) if scopes else None,
        })
        fn.__tool_meta__ = metas
        return fn

    return decorator


class ModuleToolRegistry:
    """按 AgentRuntime 保存模块工具；模块加载/卸载时注册/注销。"""

    def __init__(self, log=None) -> None:
        self._specs: list[ToolSpec] = []
        self._by_name: dict[str, ToolSpec] = {}
        self.log = log or logger

    def register_module(self, module) -> int:
        records: list[dict] = []

        cls = type(module)
        for klass in reversed(cls.__mro__):
            for name, attr in vars(klass).items():
                for meta in getattr(attr, "__tool_meta__", []):
                    records.append({"name": name, **meta})

        # 兼容旧式 TOOLS 类属性声明
        for item in getattr(cls, "TOOLS", []) or []:
            if isinstance(item, dict):
                records.append(dict(item))

        made = 0
        for rec in records:
            name = rec.get("name") or rec.get("method") or ""
            method_name = rec.get("handler") or name
            method = getattr(module, method_name, None)
            if not name or method is None or not callable(method):
                continue
            if name in self._by_name:
                self.log.warning(
                    f"[Tool] 工具名冲突 {name}（{module.module_name}）已存在，跳过；请改唯一名称"
                )
                continue
            parameters = rec.get("parameters") or {"type": "object", "properties": {}}
            spec = ToolSpec(
                name=name,
                description=rec.get("description", ""),
                parameters=parameters,
                handler=lambda ctx, args, m=method: m(ctx, args),
                module=module,
                permission=rec.get("permission", "everyone"),
                scopes=rec.get("scopes"),
                source="module",
                category=getattr(module, "module_name", "模块工具"),
            )
            self._specs.append(spec)
            self._by_name[name] = spec
            made += 1
        if made:
            self.log.debug(f"[Tool] 模块 {module.module_name} 注册 {made} 个工具")
        return made

    def unregister_module(self, module) -> int:
        removed = [s for s in self._specs if s.module is module]
        for spec in removed:
            self._by_name.pop(spec.name, None)
        self._specs = [s for s in self._specs if s.module is not module]
        return len(removed)

    def all_specs(self) -> list[ToolSpec]:
        return list(self._specs)

    def enabled_specs(self) -> list[ToolSpec]:
        """只返回「模块启用 + 工具未在 config 中被关闭」的工具。"""
        result = []
        for spec in self._specs:
            module = spec.module
            if module is None:
                result.append(spec)
                continue
            authority = getattr(module, "authority", None)
            if authority is not None and not getattr(authority, "enabled", True):
                continue
            config = getattr(module, "config", None)
            enabled_map = config.get("tools_enabled", {}) if config is not None else {}
            if isinstance(enabled_map, dict) and spec.name in enabled_map and not enabled_map.get(spec.name):
                continue
            result.append(spec)
        return result

def _timeout_of(ctx: Any, spec: ToolSpec) -> float:
    """这次执行允许几秒：工具自己声明的优先，否则用全局默认（``tool_timeout_default_s``）。"""
    declared = getattr(spec, "timeout_s", None)
    if declared:
        return float(declared)
    runtime = getattr(ctx, "runtime", None) if ctx is not None else None
    config = getattr(runtime, "config", None) if runtime is not None else None
    if config is None:
        return float(TOOL_TIMEOUT)
    try:
        return max(0.01, float(config.get("tool_timeout_default_s", TOOL_TIMEOUT) or TOOL_TIMEOUT))
    except (TypeError, ValueError):
        return float(TOOL_TIMEOUT)


def _orphan(task: asyncio.Task, name: str) -> None:
    """超时后**不取消**任务，只挂一个回调：结束（或失败）时留痕。

    为什么必须留痕：结果已经按超时回给了模型，但这个工具还在后台跑。它最终成功
    还是抛错，是排查"超时之后群里还是冒出一条消息"这类现象的唯一线索。
    """

    def _done(t: asyncio.Task) -> None:
        if t.cancelled():
            logger.info(f"[Tool] {name} 超时后被取消")
            return
        exc = t.exception()
        if exc is not None:
            logger.warning(f"[Tool] {name} 超时后仍抛错: {exc!r}")
            return
        logger.info(f"[Tool] {name} 超时后自行收敛（结果已按 TOOL_TIMEOUT 回给模型）")

    task.add_done_callback(_done)

def _to_tool_result(result: Any, *, success: bool, error: str) -> ToolResult:
    """把执行器的 ``(result, success, error)`` 三元组收敛成结构化结果。

    错误码从**事实**推导（哪条分支失败），不再从文案里猜：这样上游想按"是不是权限
    问题"分支时，读的是稳定码而不是中文句子。老的 `"error:"` 前缀仍然双读
    （`ToolResult.text` 内部会判），所以 60 个工具生产者一个都不用改。
    """
    text = str(result if result is not None else "")
    if success:
        return ToolResult.text(text)
    lowered = str(error or "").lower()
    if lowered == "unavailable":
        return ToolResult.error(TOOL_UNAVAILABLE, text)
    if lowered == "forbidden":
        return ToolResult.error(PERMISSION_DENIED, text)
    if lowered.startswith("timeout("):
        return ToolResult.error(TOOL_TIMEOUT_CODE, text)
    if lowered == "error_result":
        # 工具自己用 "error: ..." 文本报的错：没有结构化码可依，保持空码
        return ToolResult(blocks=({"type": "text", "text": text},), is_error=True)
    if text:
        return ToolResult.error(TOOL_EXCEPTION, text)
    return ToolResult.error(TOOL_EXCEPTION, str(error or "工具执行失败"))
