"""一轮对话的**结构化结局**（`TurnReason`）与轮次状态机。

## 为什么要有这个文件

改造前"这一轮到底怎么结束的"只能从日志文案里猜：`"抱歉，我暂时无法回答"` 既可能是
上游 429、也可能是思考吃满 max_tokens、还可能是模型真的没话说。于是：

- 记忆蒸馏把兜底话术也当成"bot 说过的话"记了下来；
- 主动消息统计"哑火"只能 grep 日志；
- 遥测里 `success=bool(text)` 把兜底话术记成成功。

`TurnReason` 把结局变成**枚举**，由事实推导（轮数用尽 / finish_reason / 错误 / 取消），
不再由调用方各写各的判断。落库位置与消费者见 `docs/tool-loop-refactor-plan.md §1.1`。

## 边界

- 只做**判定**：不发送、不写历史、不抛异常（取消语义在 `AbortScope`，本文件不引入）。
- `silent` 是本项目的产品语义（发言通道没入稿 / 正文为空），DSH 没有这个概念。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.agent.loop.tool_step import KIND_EMPTY, KIND_ERROR, KIND_MAX_ROUNDS


class TurnReason(str, Enum):
    """一轮对话的结局（七值，`str` 混入便于直接进日志/JSON）。"""

    COMPLETED = "completed"      # 正常发言结束
    SILENT = "silent"            # 主动选择不说 / 没产出正文
    BLOCKED = "blocked"          # 被钩子拦下，没请求模型
    MAX_ROUNDS = "max_rounds"    # 工具轮耗尽
    MAX_TOKENS = "max_tokens"    # 输出被 max_tokens 截断且无正文
    ERROR = "error"              # 请求/执行失败
    ABORTED = "aborted"          # 被取消

    def __str__(self) -> str:  # 直接 f-string 就是枚举值，日志不用写 .value
        return self.value

    @property
    def is_success(self) -> bool:
        """这一轮算不算"正常跑完"（静默是正常结局，不是失败）。"""
        return self in (TurnReason.COMPLETED, TurnReason.SILENT)

    @property
    def worth_distilling(self) -> bool:
        """只有真的说了话的一轮才该进记忆蒸馏。"""
        return self is TurnReason.COMPLETED


#: 一旦确定就不再被后续步骤覆盖的结局（失败/取消优先于"最后一步看起来正常"）
_TERMINAL = (TurnReason.ABORTED, TurnReason.ERROR, TurnReason.BLOCKED)


def classify_step(step) -> TurnReason:
    """把一次步骤（`tool_step.StepResult`）的结局翻译成 `TurnReason`。

    只读事实：``kind`` / ``finish_reason`` / ``text``。**不看**日志文案，也不看调用方
    塞进来的兜底文本——兜底话术由调用方自己处理，不该冒充 `completed`。
    """
    kind = str(getattr(step, "kind", "") or "")
    finish = str(getattr(step, "finish_reason", "") or "").lower()
    text = str(getattr(step, "text", "") or "")

    if kind == KIND_ERROR:
        return TurnReason.ERROR
    if kind == KIND_MAX_ROUNDS:
        return TurnReason.MAX_ROUNDS
    if kind == KIND_EMPTY or not text.strip():
        # 被预算截断（正文还没开始就被砍）与"模型就是没话说"是两件事：
        # 前者要抬预算，后者是产品语义上的静默。
        if finish == "length":
            return TurnReason.MAX_TOKENS
        return TurnReason.SILENT
    # 有正文：即便被截断，用户也已经看到了内容，算正常结束（截断由遥测记录）
    return TurnReason.COMPLETED


class TurnAborted(Exception):
    """本轮被协作式取消（由 `AbortScope.throw_if_aborted` 抛出）。

    刻意不是 `asyncio.CancelledError`：取消只发生在**边界**（请求前 / 工具派发前），
    已经发出去的消息与已经写进历史的前缀必须留下，所以不能用 task.cancel() 那种
    "任意位置被打断"的语义。
    """

    def __init__(self, cause: str = "") -> None:
        self.cause = cause or "turn aborted"
        super().__init__(self.cause)


class AbortScope:
    """协作式取消：只置标志，在边界处抛 `TurnAborted`。

    为什么不用 ``task.cancel()``：它会在任意 await 点打断，可能正好打断"发送成功
    之后、写历史之前"，于是用户看到了消息但历史里没有（下一轮模型"失忆"），
    或者反过来双写。这里只允许在**我们自己放的检查点**停，停之前发生的事都算数。

    用法::

        scope = AbortScope()
        scope.throw_if_aborted()        # 边界处检查
        scope.cancel("新消息取代")       # 任何一方都能取消（幂等）
    """

    __slots__ = ("_cause",)

    def __init__(self) -> None:
        self._cause = ""

    @property
    def aborted(self) -> bool:
        return bool(self._cause)

    @property
    def cause(self) -> str:
        return self._cause

    def cancel(self, cause: str = "") -> bool:
        """取消（幂等）。返回是否**首次**取消（便于日志只打一次）。"""
        if self._cause:
            return False
        self._cause = cause or "aborted"
        return True

    def throw_if_aborted(self) -> None:
        if self._cause:
            raise TurnAborted(self._cause)


def abort_scope_of(source) -> AbortScope | None:
    """从 ``ctx`` / ``TurnRequest`` 之类的对象上取取消句柄（没有就是 None）。"""
    return getattr(source, "abort", None)


@dataclass
class TurnState:
    """一轮的状态：``running → finished(reason)``。

    - `add_step`：每跑完一次步骤记一次（最后一步决定"用户看到的结局"）；
    - `finish`：落定结局；已经落定过**失败/取消**的不会被后来的"看起来正常"覆盖。

    取消（P2 第 2 步）落地后，`interrupted` 由 `AbortScope` 置位，本类只负责记账。
    """

    rounds: int = 0
    #: 最近一步推导出的结局（还没落定）
    last_reason: TurnReason | None = None
    #: 落定后的结局（None = 还在跑）
    reason: TurnReason | None = None
    interrupted: bool = False
    #: 每步的结局轨迹（诊断/测试用，按发生顺序）
    history: list[TurnReason] = field(default_factory=list)

    @property
    def running(self) -> bool:
        return self.reason is None

    def add_step(self, step) -> TurnReason:
        self.rounds += int(getattr(step, "rounds", 0) or 0)
        reason = classify_step(step)
        self.last_reason = reason
        self.history.append(reason)
        return reason

    def finish(self, reason: TurnReason | None = None) -> TurnReason:
        """落定结局（幂等；失败/取消一经确定就不再被覆盖）。"""
        if self.reason is not None:
            return self.reason
        candidate = reason or self.last_reason or TurnReason.SILENT
        if self.interrupted and candidate not in _TERMINAL:
            candidate = TurnReason.ABORTED
        self.reason = candidate
        return self.reason
