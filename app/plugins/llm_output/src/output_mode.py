"""输出形态的**单一事实源**：`mode` → text / stream / speech。

## 为什么单独成文件

模块里历史上有两个开关都在表达"文本算不算发言"：`mode`（输出形态）与
`speech_via_tool`（旧开关）。两个都参与判定的结果，是装配层与输出层各看一个，
在线上打出最坏组合：

- 装配层以为"没有发言通道"→ 不贴发言协议 → 模型照人设输出纯文本；
- 输出层其实是发言通道 → 文本只是思考、不发给用户 → 整轮静默。

所以判定只留一处（`resolve_mode`），并把"生效值"通过 `OutputOptionsView` 暴露给
框架其它机制；旧开关只在**从没保存过 `mode` 的老配置**上参与推导（升级不丢设置）。
"""

from __future__ import annotations

from typing import Any

from app.agent.delivery.output import ConfigView


def _stored_config(config: Any) -> dict:
    """用户**已保存**的配置（不含默认值）；拿不到就当作"全是显式"。

    ``ModuleConfig`` 有 `stored_config()`；测试/第三方传普通 dict 时，dict 本身就是
    显式配置。区分这两者是为了迁移：只有"从没存过 mode"的账号才按旧开关推导。
    """
    getter = getattr(config, "stored_config", None)
    if callable(getter):
        try:
            return dict(getter() or {})
        except Exception:  # noqa: BLE001 —— 读不到存储就当没存过
            return {}
    return dict(config) if isinstance(config, dict) else {}


def resolve_mode(module_config: Any) -> str:
    """本模块**生效的输出形态**：``stream`` / ``text`` 二选一。

    发言通道（文本=思考）已从模块移植为**系统能力**：它由 `reply_mode=participation`
    决定，本模块不再提供该选项。历史存过 ``speech`` 的账号按流式处理——那是发言通道
    唯一可用的底层策略，升级后不会静默丢消息。
    """
    stored = _stored_config(module_config)

    raw = str(stored.get("mode") or "").strip().lower()
    if raw == "speech":  # 迁移：旧"发言通道"形态 → 流式（发言通道由回复模式接管）
        return "stream"
    if raw in ("text", "stream"):
        return raw

    # ── 老配置迁移：没有 mode 键，按旧的开关推导，升级后行为不变 ──
    if "speech_via_tool" in stored:
        if stored["speech_via_tool"]:
            return "stream"  # 旧发言通道 → 流式（发言通道现在只看 reply_mode）
        return "stream" if stored.get("stream_output") else "text"
    if "stream_output" in stored:
        return "stream" if stored["stream_output"] else "text"

    # 从没保存过配置 → 用模块默认值（新默认 = 流式）
    getter = getattr(module_config, "get", None)
    default = getter("mode", "stream") if callable(getter) else "stream"
    default = str(default or "stream").strip().lower()
    if default == "speech":
        return "stream"
    return default if default in ("text", "stream") else "stream"


class OutputOptionsView(ConfigView):
    """给「框架其它机制」读的策略视图。

    以前这里把 ``speech_via_tool`` 覆写成"生效形态 == speech"，好让装配层与输出层
    同一口径。发言通道归回复模式之后**不再需要**这层覆写：`speech_via_tool()` 只看
    `reply_mode`，两边天然同源，模块也不再持有那个键。
    """
