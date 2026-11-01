"""能力声明（P6 第 2 步）：让"有没有这项能力"由提供方声明，配置只表达偏好。

## 为什么

下面这几个键的语义其实是"**能力**"，不是"开关"：

| 键 | 真实语义 |
|---|---|
| `stream_output` | 输出策略是流式还是阻塞 |
| `speech_via_tool` | 输出策略是"发言通道"（文本只是思考，发话靠 ``send_message``）；**由回复模式决定**（participation） |
| `context_expand_enable` | 有没有人提供"上下文按需展开"这项能力（`llm_context` 模块） |

把它们当开关的结果：框架必须知道"谁提供了什么"，并且要靠读配置去猜。于是
"模块没装但配置开着"与"模块装了但配置关着"这两种完全不同的状态，在代码里长得一样。

本模块把前者变成**声明**：提供方（模块/宿主）在自己的 `on_load` 里声明，
框架只问"有没有人提供"。配置仍然是偏好（要不要用它），但**能力的存亡**由声明决定。

## 边界

- 纯数据 + 查询，不含任何业务；
- 声明是**幂等**的（重复声明同一能力不叠加）；
- 卸载时取消声明（`revoke`），框架立刻回到"没人提供"；
- 没声明时查询返回 None，调用方按老路径（读配置）兜底——**升级不破坏行为**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: 输出能力的三种取值（互斥）
OUTPUT_STREAM = "stream"
OUTPUT_BLOCKING = "blocking"
OUTPUT_SPEECH = "speech"

OUTPUT_KINDS = frozenset({OUTPUT_STREAM, OUTPUT_BLOCKING, OUTPUT_SPEECH})

#: `llm_context` 模块声明的能力名
CAP_CONTEXT_EXPAND = "context_expand"
#: 提供"提示词段"的模块（Q4）：声明后可以往 `runtime.prompt_sections` 注册 Section。
#: 框架自己的 12 段是内置的、不声明的——这里只用于"想接管/追加段"的第三方模块互查。
CAP_PROMPT_SECTIONS = "prompt_sections"


@dataclass(frozen=True)
class OutputCapability:
    """这套运行时**能**怎么产出：流式 / 阻塞 / 发言通道。

    ``kind`` 是三选一；``source`` 记录谁声明的（排障用：出了怪事先看是谁改的）。
    """

    kind: str = OUTPUT_BLOCKING
    source: str = "framework"

    def __post_init__(self) -> None:
        if self.kind not in OUTPUT_KINDS:
            raise ValueError(f"未知的输出能力: {self.kind!r}（可选 {sorted(OUTPUT_KINDS)}）")

    @property
    def streams(self) -> bool:
        """是否**逐句流式产出**。

        注意：「发言通道」（speech）**不是**流式。流式是**请求/响应方式**
        （边收边用 vs 一次拿全），发言通道是**语义形态**（文本只是思考、发言走工具）。
        两者正交，历史实现把它们混成一个维度（`kind in (stream, speech)`），
        结果就是"没开流式却被迫走流式"。这里只把 stream 算流式。
        """
        return self.kind == OUTPUT_STREAM

    @property
    def speech(self) -> bool:
        """是否是发言通道（文本=思考，发言走 ``send_message``）。"""
        return self.kind == OUTPUT_SPEECH

    @classmethod
    def from_config(cls, config: Any, *, source: str = "config") -> OutputCapability:
        """按配置推导（**升级兜底**：没人声明时保持改造前的行为）。

        「发言通道」由**回复模式**决定（参与度模式 = 文本只是思考），不再读
        `speech_via_tool`；其余形态仍按 `stream_output` 推导。
        """
        getter = getattr(config, "get", None)
        if not callable(getter):
            return cls(OUTPUT_BLOCKING, source)

        def _flag(key: str) -> bool:
            try:
                return bool(getter(key, False))
            except Exception:  # noqa: BLE001 —— 配置坏了按默认（非流式/非发言通道）
                return False

        if reply_mode_of(config) == "participation":
            return cls(OUTPUT_SPEECH, "reply_mode")
        return cls(OUTPUT_STREAM if _flag("stream_output") else OUTPUT_BLOCKING, source)


@dataclass(frozen=True)
class ToolCapability:
    """一项"工具能力"的声明（例如 ``context_expand`` 由 `llm_context` 提供）。"""

    name: str
    source: str = "framework"


class CapabilityRegistry:
    """挂在 ``runtime.capabilities`` 上：声明 / 撤销 / 查询。

    故意做得极薄：这里只是"谁声明了什么"，不放行为。
    """

    def __init__(self) -> None:
        self._output: OutputCapability | None = None
        self._tools: dict[str, ToolCapability] = {}

    # ── 输出能力 ──────────────────────────────────────

    def declare_output(self, kind: str, *, source: str = "") -> OutputCapability:
        capability = OutputCapability(kind, source or "unknown")
        self._output = capability
        return capability

    def revoke_output(self, *, source: str = "") -> None:
        if source and self._output is not None and self._output.source != source:
            return      # 别人声明的，不是你能撤的
        self._output = None

    @property
    def output(self) -> OutputCapability | None:
        return self._output

    # ── 工具能力 ──────────────────────────────────────

    def declare_tool(self, name: str, *, source: str = "") -> ToolCapability:
        capability = ToolCapability(str(name or ""), source or "unknown")
        if capability.name:
            self._tools[capability.name] = capability
        return capability

    def revoke_tool(self, name: str, *, source: str = "") -> None:
        current = self._tools.get(str(name or ""))
        if current is None:
            return
        if source and current.source != source:
            return
        self._tools.pop(current.name, None)

    def has_tool(self, name: str) -> bool:
        return str(name or "") in self._tools

    def tool_capabilities(self) -> dict[str, ToolCapability]:
        return dict(self._tools)


# ── 运行时取用（都带"没人声明"的兜底）─────────────────────


def registry_of(runtime: Any) -> CapabilityRegistry:
    """取该运行时的能力表（没有就建一个并挂上去）。"""
    registry = getattr(runtime, "capabilities", None)
    if not isinstance(registry, CapabilityRegistry):
        registry = CapabilityRegistry()
        try:
            runtime.capabilities = registry
        except Exception as e:  # noqa: BLE001 —— 只读替身上挂不了属性：用临时的
            from app.core.logger import logger

            logger.debug(f"[Capability] 运行时挂不上能力表（用临时表继续）: {e}")
    return registry


def reply_mode_of(config: Any) -> str:
    """读回复模式（``legacy`` / ``participation``）；读不到按 legacy。"""
    getter = getattr(config, "get", None)
    if not callable(getter):
        return "legacy"
    try:
        return str(getter("reply_mode", "legacy") or "legacy")
    except Exception:  # noqa: BLE001
        return "legacy"


def output_capability(runtime: Any, config: Any = None) -> OutputCapability:
    """输出能力：**回复模式优先** → 模块声明 → 配置推导。

    「发言通道」（文本=思考，发言走 ``send_message``）是回复模式的语义，不是输出模块的
    一个选项：参与度模式下它必须成立，否则"沉默"这个状态根本不存在。所以这条判定放在
    声明之前——模块只能决定"流式 / 一次性"，不能把参与度模式改回文本即回复。
    """
    cfg = config if config is not None else getattr(runtime, "config", None)
    if reply_mode_of(cfg) == "participation":
        return OutputCapability(OUTPUT_SPEECH, "reply_mode")
    registry = getattr(runtime, "capabilities", None)
    if isinstance(registry, CapabilityRegistry) and registry.output is not None:
        return registry.output
    return OutputCapability.from_config(cfg)


def has_tool_capability(
    runtime: Any,
    name: str,
    config: Any = None,
    config_key: str = "",
    config_default: bool = False,
) -> bool:
    """工具能力是否可用：声明优先；没人声明时回退到（可选的）配置键。

    ``config_default`` 必须由调用方给出**该键缺失时**的默认值——不能一律填 False：
    `context_expand_enable` 这类默认开启的功能，键缺失当 False 会让工具静默消失
    （与 `_SYSTEM_TOOL_PREREQUISITES` 表里的第三项保持一致）。
    """
    registry = getattr(runtime, "capabilities", None)
    if isinstance(registry, CapabilityRegistry) and registry.has_tool(name):
        return True
    if config_key:
        getter = getattr(config if config is not None else getattr(runtime, "config", None), "get", None)
        if callable(getter):
            try:
                return bool(getter(config_key, config_default))
            except Exception:  # noqa: BLE001 —— 配置异常按调用方给的默认
                return bool(config_default)
    return bool(config_default)
