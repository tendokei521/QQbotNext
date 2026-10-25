"""高危工具的**人工审批**（P6 第 1 步）。

## 为什么要有

OneBot 侧有一批不可撤销的操作（踢人、禁言、退群…）打着 ``sensitivity in {high, critical}``
的标，但运行期只做了权限校验：只要调用者权限够，模型一句"帮我踢了他"就能执行。
审计里也只有"调用成功"，没有"谁批准过"。

## 设计要点（fail-closed 是全部重点）

- 触发条件：``tool_approval_enable`` 开 **且** 工具来自 OneBot **且** sensitivity 高危；
- 通道是**可插拔**的：``runtime.approval_channel`` 提供一个
  ``async ask(runtime, *, tool, args, ctx) -> ApprovalOutcome``；
- **没有通道 = 拒绝**（``UNAVAILABLE``）。绝不存在"配了审批但悄悄放行"的路径；
- 只有 ``ALLOWED_ONCE`` 才允许执行，且只对**这一次调用**有效（不缓存、不升级）；
- asked/decided 成对留痕：拒绝文案按 outcome 区分，并写一条审计日志。

通道留给插件实现（私聊拥有者、WebUI 确认框…）：框架不假设你用哪种媒介，只要求
"能给出一个结论，否则就是没有通道"。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

#: 需要审批的敏感级别
SENSITIVE_LEVELS = frozenset({"high", "critical"})


class ApprovalOutcome(str, Enum):
    """一次审批的结论（``str`` 混入便于直接进日志/JSON）。"""

    ALLOWED_ONCE = "allowed_once"   # 批准这一次
    REJECTED = "rejected"           # 明确拒绝
    TIMEOUT = "timeout"             # 等不到回应
    UNAVAILABLE = "unavailable"     # 没有可用的审批通道（→ 拒绝）

    def __str__(self) -> str:
        return self.value

    @property
    def allowed(self) -> bool:
        return self is ApprovalOutcome.ALLOWED_ONCE


#: 拒绝时给模型看的话术（按结论区分，别让模型以为"再试一次就能过"）
_REJECT_TEXT = {
    ApprovalOutcome.REJECTED: "用户拒绝了这次操作",
    ApprovalOutcome.TIMEOUT: "等待用户确认超时，操作已取消",
    ApprovalOutcome.UNAVAILABLE: "没有可用的审批通道，操作已拒绝（fail-closed）",
}


def approval_enabled(runtime: Any) -> bool:
    config = getattr(runtime, "config", None)
    getter = getattr(config, "get", None)
    if not callable(getter):
        return False
    try:
        return bool(getter("tool_approval_enable", False))
    except Exception:  # noqa: BLE001 —— 配置坏了按关闭（与默认值一致）
        return False


def needs_approval(runtime: Any, spec: Any) -> bool:
    """这个工具的**这次调用**是否要先问人。"""
    if spec is None:
        return False
    if str(getattr(spec, "source", "") or "") != "onebot":
        return False
    if str(getattr(spec, "sensitivity", "normal") or "normal").lower() not in SENSITIVE_LEVELS:
        return False
    return approval_enabled(runtime)


def _channel_of(runtime: Any):
    channel = getattr(runtime, "approval_channel", None)
    ask = getattr(channel, "ask", None)
    return channel if callable(ask) else None


def _log(runtime: Any):
    from app.agent import logger
    from app.core.logger import account_label

    return logger.add_info(account_label(getattr(runtime, "bot_id", "?")))


async def request_approval(
    runtime: Any, *, tool: str, args: dict, ctx: Any = None
) -> ApprovalOutcome:
    """问一次人；返回结论（**任何异常都按 UNAVAILABLE = 拒绝**）。"""
    channel = _channel_of(runtime)
    if channel is None:
        _audit(runtime, tool, args, ApprovalOutcome.UNAVAILABLE)
        return ApprovalOutcome.UNAVAILABLE
    try:
        outcome = await channel.ask(runtime, tool=tool, args=dict(args or {}), ctx=ctx)
    except Exception as e:  # noqa: BLE001 —— 通道异常 = 没有可靠结论 = 拒绝
        _log(runtime).warning(f"[Approval] 审批通道异常（按拒绝处理）（{tool}）: {e}")
        _audit(runtime, tool, args, ApprovalOutcome.UNAVAILABLE)
        return ApprovalOutcome.UNAVAILABLE
    if not isinstance(outcome, ApprovalOutcome):
        # 通道返回了不认识的东西：不猜，按拒绝
        try:
            outcome = ApprovalOutcome(str(outcome))
        except Exception:  # noqa: BLE001
            outcome = ApprovalOutcome.UNAVAILABLE
    _audit(runtime, tool, args, outcome)
    return outcome


def rejection_message(outcome: ApprovalOutcome) -> str:
    """拒绝时回给模型的正文（走 ``error:`` 约定，码是 PERMISSION_DENIED）。"""
    return _REJECT_TEXT.get(outcome, "操作未获批准")


def _audit(runtime: Any, tool: str, args: dict, outcome: ApprovalOutcome) -> None:
    """asked/decided 成对留痕（一条日志；遥测由执行器统一记）。"""
    try:
        _log(runtime).info(
            f"[Approval] 工具 {tool} 的审批结论: {outcome}"
            f"（args={str(dict(args or {}))[:120]}）"
        )
    except Exception as e:  # noqa: BLE001 —— 审计失败不影响结论，但要留痕
        try:
            _log(runtime).debug(f"[Approval] 审计日志写入失败（已忽略）: {e}")
        except Exception as inner:  # noqa: BLE001 —— 连日志都写不了就只能放弃
            from app.core.logger import logger as _root_logger

            _root_logger.debug(f"[Approval] 审计与日志都失败: {inner}")
