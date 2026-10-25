"""一次请求应该长什么样 —— provider 层的**唯一决策点**。

这个模块存在的理由：此前"这次请求用哪个 max_tokens / 该不该重试 / 要不要换 provider"
散在四个地方各自决定，而且互相看不见：

- ``AgentRuntime._config_for_model_id``   —— 合并三层配置（另一份同样的合并躺在
  ``ProviderRuntimeManager.resolve_provider_config`` 里，两份会漂移）；
- ``providers.chat_with_fallback``       —— 空回复原样重试；
- ``providers.iter_stream_with_fallback`` —— 另一个口径的空回复/错误处理；
- ``chat.stream_response``               —— 思考吃满预算的收敛重试。

四层重试**相乘**（``retry_attempts × empty_reply_retries × reasoning_overflow_retries
× provider 数``），而且每一次都把全量上下文重发一遍——线上为此把 16.7 万 token 的
请求原样发了三次、回复 0 字符。

这里把三件事收口：

1. **``EffectiveConfig``** —— 三层配置（模型实例 / 连接预设 / Agent）合并的**唯一**实现，
   并且逐键记下"实际生效的是哪一层"（此前溯源函数只看合并结果，永远报同一个答案）；
2. **``classify_failure``** —— 把失败分成"确定性 / 瞬态 / 空-被截断 / 空-只思考 /
   空-疑似"五类，并直接给出"值不值得同 provider 再试 / 值不值得换 provider"；
   重试只该解决**可能变化的外部条件**，不该解决参数错误和上下文超长；
3. **``TurnBudget``** —— 一轮的请求次数与累计输入 token 预算。没有这个闸门，
   上面三层重试各自的"一次"会乘成十几次。

数值口径（``reasoning_floor`` / 重试档位）也放这里，配置文件只存用户的**意图**，
不存推导结果。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ── 层名（同时用于日志、UI 与告警文案，必须是用户看得懂的话）──────────────────
LAYER_MODEL = "provider 模型"
LAYER_PRESET = "连接预设"
LAYER_AGENT = "agent 配置"
LAYER_DEFAULT = "内置默认"

#: 三层都能设置、因而需要逐键溯源的配置项
TRACKED_KEYS: tuple[str, ...] = (
    "max_tokens",
    "temperature",
    "timeout",
    "retry_attempts",
    "context_window",
)

#: 思考模型的输出预算下限。思考本身要吃掉几十到几百 token，
#: 预算压到 1024 时正文必然一个字都写不出来（实测 max_tokens=24 必失败、48 才稳）。
REASONING_FLOOR = 2048

#: 模型名/配置里出现这些字样，就按"思考模型"对待（配置里 ``reasoning=true`` 优先）
_REASONING_MARKERS = (
    "reasoner",
    "thinking",
    "-r1",
    "r1-",
    "/r1",
    "o1",
    "o3",
    "o4",
    "qwq",
    "deepseek-r",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
)


def _present(cfg: Any, key: str) -> bool:
    """该层是否**显式**给了这个键（``None`` / 空串都算没给）。"""
    if not isinstance(cfg, dict):
        return False
    value = cfg.get(key)
    return value is not None and value != ""


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ── 1. 配置合并与溯源 ────────────────────────────────────────────────────


@dataclass
class EffectiveConfig:
    """一层合并后的 provider 配置 + 逐键来源。"""

    config: dict = field(default_factory=dict)
    origins: dict[str, str] = field(default_factory=dict)

    def origin(self, key: str) -> str:
        return self.origins.get(key, "") or LAYER_DEFAULT

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    @property
    def max_tokens(self) -> int:
        return _as_int(self.config.get("max_tokens"), 0)

    @property
    def max_tokens_origin(self) -> str:
        return self.origin("max_tokens")

    @property
    def context_window(self) -> int:
        return _as_int(self.config.get("context_window"), 0)

    def describe(self) -> str:
        """一行可读摘要（日志/UI 用）。"""
        bits = [f"max_tokens={self.max_tokens}（来自{self.max_tokens_origin}）"]
        window = self.context_window
        if window:
            bits.append(f"窗口={window}")
        model = str(self.config.get("provider_model_id") or self.config.get("model") or "")
        if model:
            bits.insert(0, f"模型={model}")
        return " ".join(bits)


def _apply_layer(merged: dict, layer: dict | None) -> None:
    """把一层合并进 ``merged``，但**跳过"没给"** 的值。

    ``max_tokens=None`` / ``max_tokens=""`` 是"清除覆盖、回落到下一层"的写法；
    直接 ``dict.update`` 会把它当成一个值写进去（``int(None)`` 之后变成 0），
    于是"清除"变成了"把预算设成 0"，比不清除更糟。
    """
    for key, value in (layer or {}).items():
        if value is None:
            continue
        if key in TRACKED_KEYS and value == "":
            continue
        merged[key] = value


def merge_config_layers(
    *,
    base: dict | None = None,
    agent_layer: dict | None = None,
    preset_layer: dict | None = None,
    model_layer: dict | None = None,
    defaults: dict | None = None,
    meta: dict | None = None,
) -> EffectiveConfig:
    """按「模型实例 > 连接预设 > Agent 配置 > 内置默认」合并，并记录逐键来源。

    ``base`` 是调用方已有的"铺满默认值的 Agent 配置"（``_base_raw_config()``）——
    保留它才能保证合并结果与改造前**逐字一致**（provider 层有些代码还会从链上的
    cfg 里读 ``image_max_count`` 之类的 agent 键）。

    ``agent_layer`` 只放 Agent 侧**显式**设置过的键（``AgentConfig.explicit_values``）：
    溯源必须看"用户有没有动过"，否则 Agent 层那份铺满默认值的 dict 会把一切都算成
    "来自 agent 配置"，等于没溯源。
    """
    merged: dict = {}
    _apply_layer(merged, defaults)
    _apply_layer(merged, base)
    _apply_layer(merged, preset_layer)
    _apply_layer(merged, model_layer)
    _apply_layer(merged, meta)

    origins: dict[str, str] = {}
    layers = (
        (LAYER_MODEL, model_layer),
        (LAYER_PRESET, preset_layer),
        (LAYER_AGENT, agent_layer),
        (LAYER_DEFAULT, defaults if defaults is not None else base),
    )
    for key in TRACKED_KEYS:
        for name, layer in layers:
            if _present(layer, key):
                origins[key] = name
                break

    # 兼容既有读取口径：chat.py 用 ``_max_tokens_origin``，UI/日志用 ``_origins``
    if "max_tokens" in origins:
        merged["_max_tokens_origin"] = origins["max_tokens"]
    merged["_origins"] = dict(origins)

    return EffectiveConfig(config=merged, origins=origins)


# ── 2. max_tokens 的归一化 ──────────────────────────────────────────────


def is_reasoning_model(cfg: dict | None) -> bool:
    """是否按"思考模型"对待（会先花预算思考，正文才轮到）。"""
    cfg = cfg or {}
    flag = cfg.get("reasoning")
    if flag is not None:
        return bool(flag)
    name = str(cfg.get("model") or "").lower()
    return any(marker in name for marker in _REASONING_MARKERS)


@dataclass(frozen=True)
class MaxTokensPlan:
    """``max_tokens`` 的最终取值 + 它是怎么来的。"""

    value: int
    origin: str
    notes: tuple[str, ...] = ()

    def describe(self) -> str:
        text = f"max_tokens={self.value}（来自{self.origin}）"
        if self.notes:
            text += "；" + "；".join(self.notes)
        return text


def plan_max_tokens(cfg: dict | None, *, fallback: int = 1024) -> MaxTokensPlan:
    """算出本次请求真正该用的 ``max_tokens``。

    三件事：

    - **思考下限**：思考模型给低于 ``REASONING_FLOOR`` 的预算等于让正文必然为空，
      直接抬到下限并留痕（不静默、也不报错——用户设 1024 的意图通常是"省点"，不是
      "我要拿到空回复"）；
    - **窗口封顶**：不超过声明过的 ``context_window``；
    - **溯源**：跟着 ``_max_tokens_origin`` 走，让日志能回答"这个 1024 是谁给的"。
    """
    cfg = cfg or {}
    origin = str(cfg.get("_max_tokens_origin") or "")
    raw = cfg.get("max_tokens")
    if raw in (None, ""):
        # 没设 = 继承：有窗口就按四分之一给一个不会撑爆的默认，否则用调用方兜底
        window = _as_int(cfg.get("context_window"), 0)
        value = max(1, window // 4) if window else max(1, _as_int(fallback, 1024))
        if not origin:
            origin = LAYER_DEFAULT
    else:
        value = max(1, _as_int(raw, fallback))
        if not origin:
            origin = LAYER_DEFAULT

    notes: list[str] = []
    if is_reasoning_model(cfg) and value < REASONING_FLOOR:
        notes.append(f"思考模型下限 {REASONING_FLOOR}，已从 {value} 抬升")
        value = REASONING_FLOOR
        origin = f"{origin}+思考下限"

    window = _as_int(cfg.get("context_window"), 0)
    if window and value > window:
        notes.append(f"超过模型窗口 {window}，已封顶")
        value = window
        origin = f"{origin}+窗口封顶"

    return MaxTokensPlan(value=value, origin=origin, notes=tuple(notes))


# ── 3. 失败分类与重试决策 ───────────────────────────────────────────────


class FailureKind(str, Enum):
    """五类失败。重试策略按类决定，不按"是异常还是空回复"决定。"""

    DETERMINISTIC = "deterministic"      # 参数/权限/模型不存在/上下文超长/余额：重试必然同样失败
    TRANSIENT = "transient"              # 限流/5xx/超时/连接断：值得退避重试
    EMPTY_CUT = "empty_cut"              # 被 max_tokens 截断（finish_reason=length）：抬预算再要
    EMPTY_THINK = "empty_think"          # 只有思考没有正文：换问法，别原样重发
    EMPTY_SUSPECT = "empty_suspect"      # 上游计费了却没产出、也没有任何提示：最多一次


#: HTTP 状态码 → 失败类别（与 ``openai_compat.RETRYABLE_STATUS`` 口径一致）
_TRANSIENT_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
#: 文案里的确定性关键词（拿不到状态码时兜底判定）
_DETERMINISTIC_MARKERS = (
    "context length",
    "context_length",
    "maximum context",
    "too many tokens",
    "invalid api key",
    "incorrect api key",
    "unauthorized",
    "insufficient",
    "quota",
    "billing",
    "余额",
    "欠费",
    "model not found",
    "does not exist",
    "invalid_request_error",
    "content policy",
    "safety",
    # 中文上游（国内中转/自建网关）常见文案
    "模型不存在",
    "不存在",
    "无权限",
    "未授权",
    "参数错误",
    "内容审核",
    "超长",
    "上下文",
)


@dataclass(frozen=True)
class Failure:
    """一次失败的判定结果：怎么归类、下一步该做什么。"""

    kind: FailureKind
    detail: str = ""
    #: 值不值得**同一个 provider** 再试一次（必须"改了条件"才有意义）
    retry_same: bool = False
    #: 值不值得**换下一个 provider**（换模型可能换掉窗口/配额/可用性）
    switch_provider: bool = True
    #: 上游要求的退避秒数（429 的 Retry-After）
    retry_after: float | None = None

    @property
    def deterministic(self) -> bool:
        return self.kind == FailureKind.DETERMINISTIC

    def describe(self) -> str:
        bits = [f"{self.kind.value}"]
        if self.detail:
            bits.append(self.detail)
        if self.retry_after:
            bits.append(f"retry_after={self.retry_after:g}s")
        return ": ".join(bits[:1]) + (" " + " ".join(bits[1:]) if len(bits) > 1 else "")


def _retry_after_of(exc: Any) -> float | None:
    """从异常里挖 ``Retry-After``（httpx 的 response.headers 或自带属性）。"""
    for holder in (
        getattr(exc, "response", None),
        getattr(exc, "headers", None),
    ):
        headers = getattr(holder, "headers", holder)
        getter = getattr(headers, "get", None)
        if not callable(getter):
            continue
        try:
            raw = getter("retry-after") or getter("Retry-After")
        except Exception:  # noqa: BLE001 —— 头解析失败不该影响失败分类
            raw = None
        if raw in (None, ""):
            continue
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            continue
    for attr in ("retry_after", "retry_after_seconds"):
        value = getattr(exc, attr, None)
        if value not in (None, ""):
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                continue
    return None


def _status_of(exc: Any) -> int | None:
    for holder in (exc, getattr(exc, "response", None)):
        status = getattr(holder, "status_code", None)
        if status is None:
            status = getattr(holder, "status", None)
        if isinstance(status, int):
            return status
    return None


_STATUS_RE = re.compile(r"\b([45]\d\d)\b")


def classify_error_text(text: str) -> Failure:
    """错误**文案** → 失败分类（没有异常对象可看时用）。

    provider 适配器会把上游的错误报文包成 ``StreamEvent(type="error")``，回退器
    拿到的只有一句话。这里和 ``classify_exception`` 用同一套关键词与状态码口径，
    避免"异常路径判确定性、事件路径判瞬态"这种两套标准。
    """
    detail = str(text or "").strip()
    lowered = detail.lower()
    status_match = _STATUS_RE.search(detail)
    if status_match:
        status = int(status_match.group(1))
        if status in _TRANSIENT_STATUS or 500 <= status < 600:
            return Failure(FailureKind.TRANSIENT, detail[:200], retry_same=True)
        return Failure(FailureKind.DETERMINISTIC, detail[:200], switch_provider=False)
    if any(marker in lowered for marker in _DETERMINISTIC_MARKERS):
        return Failure(FailureKind.DETERMINISTIC, detail[:200], switch_provider=False)
    # 文案里既没有状态码也没有确定性关键词：当成瞬态抖一次（总次数由 TurnBudget 兜住）
    return Failure(FailureKind.TRANSIENT, detail[:200], retry_same=True)


def classify_exception(exc: BaseException, *, detail: str = "") -> Failure:
    """异常 → 失败分类。

    判定顺序：状态码（最准）→ 异常类型（超时/连接）→ 文案关键词 → 兜底。

    兜底给 ``TRANSIENT`` 而不是确定性：未知异常里混着最多的是网络与网关抖动，
    把它们一律当确定性会让"主模型一抖就整轮失败"。代价是未知异常最多多试一次，
    由 ``TurnBudget`` 的总次数上限兜住。
    """
    from app.agent.providers.base import format_llm_error

    text = f"{detail} {format_llm_error(exc)}".strip()
    lowered = text.lower()
    retry_after = _retry_after_of(exc)

    status = _status_of(exc)
    if status is not None:
        if status in _TRANSIENT_STATUS:
            return Failure(FailureKind.TRANSIENT, f"HTTP {status}", retry_same=True, retry_after=retry_after)
        if 500 <= status < 600:
            return Failure(FailureKind.TRANSIENT, f"HTTP {status}", retry_same=True, retry_after=retry_after)
        return Failure(FailureKind.DETERMINISTIC, f"HTTP {status} {text}"[:200], switch_provider=False)

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError)):
        return Failure(FailureKind.TRANSIENT, f"{type(exc).__name__}: {text}"[:200], retry_same=True)

    if any(marker in lowered for marker in _DETERMINISTIC_MARKERS):
        return Failure(FailureKind.DETERMINISTIC, text[:200], switch_provider=False)

    return Failure(FailureKind.TRANSIENT, f"{type(exc).__name__}: {text}"[:200], retry_same=True)


def classify_response(
    *,
    text: str = "",
    reasoning: str = "",
    tool_results: Any = None,
    tool_calls: Any = None,
    finish_reason: str = "",
    usage: dict | None = None,
    max_tokens: int = 0,
) -> Failure | None:
    """一次"成功"响应其实什么都没产出时的分类；真的产出了就返回 ``None``。

    关键口径：**思考也算产出**（上游确实在跑，只是把预算花在 reasoning 里），
    但它**不值得同 prompt 重试**——同样的输入必然同样打满。

    **模型要求的工具调用也算产出**：适配器只发一次请求后，``tool_calls`` 就是这一轮
    的产出（此前它由适配器执行完落到 ``tool_results`` 上）；不看它会把"要调工具"
    误判成空回复并原样重发，白白多打一次全量上下文。
    """
    if (text or "").strip() or tool_results or tool_calls:
        return None

    usage = usage or {}
    out_tokens = _as_int(usage.get("completion_tokens") or usage.get("output_tokens"), 0)
    reason = str(finish_reason or "").lower()

    if reason == "length" or (max_tokens and out_tokens >= max_tokens):
        return Failure(
            FailureKind.EMPTY_CUT,
            f"被 max_tokens={max_tokens or '?'} 截断（输出 {out_tokens} token）",
            retry_same=True,
        )
    if (reasoning or "").strip():
        return Failure(FailureKind.EMPTY_THINK, "只有思考没有正文", retry_same=False, switch_provider=False)
    if out_tokens or _as_int(usage.get("prompt_tokens") or usage.get("input_tokens"), 0):
        return Failure(FailureKind.EMPTY_SUSPECT, "上游计费但无产出", retry_same=True)
    return Failure(FailureKind.TRANSIENT, "空回复（无计费）", retry_same=True)


# ── 4. 一轮的请求预算 ───────────────────────────────────────────────────

#: 重试档位 → (一轮最多请求次数, 单个 provider 瞬态重试次数)
RETRY_PROFILES: dict[str, dict[str, int]] = {
    "thrifty": {"max_requests": 2, "transient_retries": 1},     # 省钱：能失败就失败
    "balanced": {"max_requests": 4, "transient_retries": 2},    # 默认：一次退避 + 换一个模型
    "resilient": {"max_requests": 6, "transient_retries": 3},   # 抗抖动：链路多、限流频繁
}
DEFAULT_RETRY_PROFILE = "balanced"


def retry_profile_of(config: Any) -> str:
    """读档位（兼容旧的 ``empty_reply_retries`` 数值配置）。"""
    getter = getattr(config, "get", None)
    raw = ""
    if callable(getter):
        try:
            raw = str(getter("retry_profile", "") or "").strip().lower()
        except Exception:  # noqa: BLE001 —— 配置对象异常不该影响重试策略
            raw = ""
    if raw in RETRY_PROFILES:
        return raw
    return DEFAULT_RETRY_PROFILE


@dataclass
class TurnBudget:
    """一轮对话允许花掉的"请求次数"和"输入 token"。

    为什么必须有：``retry_attempts × empty_reply_retries × reasoning_overflow_retries
    × provider 数`` 最坏能乘到十几次，而每一次都带着全量上下文——线上就是这么把
    16.7 万 token 发了三遍的。这里给整轮一个硬上限。

    关键区分：**工具轮是合法的多次请求，不算重试**。``max_tool_rounds`` 是用户
    明确配置的"模型→调工具→再判断"轮数，把它算进重试额度会让配置 5 轮的任务在第
    4 轮被预算掐死（而且日志会诬告"重试用尽"）。所以额度分两半：``base_requests``
    是合法轮次，档位给的是**额外的**重试余量。
    """

    profile: str = DEFAULT_RETRY_PROFILE
    max_input_tokens: int = 0  # 0 = 不限（沿用 context_token_budget 的语义）
    #: 合法轮次（工具循环的轮数上限）：不占重试额度，否则"配 5 轮"会被预算掐在第 4 轮
    base_requests: int = 0
    requests: int = 0
    input_tokens: int = 0
    denied_reasons: list[str] = field(default_factory=list)

    @classmethod
    def from_config(cls, config: Any) -> TurnBudget:
        profile = retry_profile_of(config)
        limit = 0
        base = 0
        getter = getattr(config, "get", None)
        if callable(getter):
            try:
                limit = _as_int(getter("turn_token_budget", 0), 0)
            except Exception:  # noqa: BLE001
                limit = 0
            try:
                base = _as_int(getter("max_tool_rounds", 0), 0)
            except Exception:  # noqa: BLE001
                base = 0
        return cls(profile=profile, max_input_tokens=max(0, limit), base_requests=max(0, base))

    @property
    def retry_requests(self) -> int:
        """档位给的**额外**请求额度（重试用）。"""
        return RETRY_PROFILES.get(self.profile, RETRY_PROFILES[DEFAULT_RETRY_PROFILE])["max_requests"]

    @property
    def max_requests(self) -> int:
        return self.retry_requests + max(0, self.base_requests)

    @property
    def transient_retries(self) -> int:
        return RETRY_PROFILES.get(self.profile, RETRY_PROFILES[DEFAULT_RETRY_PROFILE])["transient_retries"]

    @property
    def exhausted(self) -> bool:
        return self.requests >= self.max_requests

    def allow(self, *, estimated_input: int = 0, reason: str = "") -> tuple[bool, str]:
        """还能不能再发一次？返回 ``(是否允许, 拒绝原因)``。"""
        if self.exhausted:
            why = f"本轮请求次数已用尽 {self.requests}/{self.max_requests}（{self.profile}）"
            if reason:
                why += f"，最后想做的事：{reason}"
            self.denied_reasons.append(why)
            return False, why
        if self.max_input_tokens > 0 and estimated_input > 0:
            if self.input_tokens + estimated_input > self.max_input_tokens:
                why = (
                    f"本轮累计输入 {self.input_tokens}+{estimated_input} 将超预算 "
                    f"{self.max_input_tokens}"
                )
                if reason:
                    why += f"，最后想做的事：{reason}"
                self.denied_reasons.append(why)
                return False, why
        return True, ""

    def charge(self, *, estimated_input: int = 0, usage: dict | None = None) -> None:
        """记一次请求。真实 usage 优先于估算（估算可能偏差两三倍）。"""
        self.requests += 1
        usage = usage or {}
        real = _as_int(usage.get("prompt_tokens") or usage.get("input_tokens"), 0)
        self.input_tokens += real if real else max(0, _as_int(estimated_input, 0))

    def summary(self) -> dict:
        return {
            "profile": self.profile,
            "requests": self.requests,
            "max_requests": self.max_requests,
            "base_requests": self.base_requests,
            "retry_requests": self.retry_requests,
            "input_tokens": self.input_tokens,
            "max_input_tokens": self.max_input_tokens,
            "denied": list(self.denied_reasons),
        }

    def describe(self) -> str:
        text = f"请求 {self.requests}/{self.max_requests}"
        if self.base_requests:
            text += f"（工具轮上限 {self.base_requests} + 重试余量 {self.retry_requests}）"
        if self.input_tokens:
            text += f"，累计输入 ≈{self.input_tokens} token"
            if self.max_input_tokens:
                text += f"/{self.max_input_tokens}"
        return text


__all__ = [
    "LAYER_MODEL",
    "LAYER_PRESET",
    "LAYER_AGENT",
    "LAYER_DEFAULT",
    "TRACKED_KEYS",
    "REASONING_FLOOR",
    "RETRY_PROFILES",
    "DEFAULT_RETRY_PROFILE",
    "EffectiveConfig",
    "MaxTokensPlan",
    "Failure",
    "FailureKind",
    "TurnBudget",
    "merge_config_layers",
    "plan_max_tokens",
    "is_reasoning_model",
    "classify_exception",
    "classify_error_text",
    "classify_response",
    "retry_profile_of",
]


