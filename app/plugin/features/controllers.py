"""具体的能力接管者，一个能力一个类。

每个控制器只做一件事：认领某个能力后，在对应时机把活接过去。加一种新能力就在这里
加一个类，然后在 `FeatureRegistry` 里注册——不需要改 `agent` 内部。
"""

from __future__ import annotations

"""全局功能/能力注册表（FeatureRegistry）。

插件扩展性核心：
- 框架内置能力通过 FeatureController 注册为 feature_id；
- 插件声明 ``provides`` / ``supersedes`` 后，在加载/启用/卸载/禁用时自动接管或释放；
- 接管是「租约」：多个插件同时接管同一能力时，只要还有租约持有者，能力就保持禁用；
  最后一个租约释放时自动恢复被接管前的状态。

## v3：只保留**高级接口**（更换机制 / 接管行为）

| feature_id | 类型 | 说明 |
|---|---|---|
| `assembly` | 装槽型 | 本轮 messages 怎么组装（`build(req)`） |
| `provider_invoker` | 装槽型 | LLM 请求怎么发（`chat` / `stream`） |
| `output` | 装槽型 | 一轮回复怎么产出（`respond(ctx)`） |
| `agent` | 开关型 | 整体停用框架 Agent |

**业务能力（记忆/知识库/主动消息/定时任务/OneBot/MCP/元信息/指代）不再是 feature**：
它们由各自模块用基础接口实现，框架不认识它们的存在。
"""


from typing import Any

from app.core.logger import logger
from app.plugin.features.base import FeatureController, _RuntimeAccess


class ConfigToggleFeature(_RuntimeAccess, FeatureController):
    """通用配置开关能力：接管时把一组布尔配置置 False，恢复时还原。"""

    keys: tuple[str, ...] = ()

    def __init__(self, agent_manager: Any, *, feature_id: str, label: str, keys: tuple[str, ...]) -> None:
        _RuntimeAccess.__init__(self, agent_manager)
        self.feature_id = feature_id
        self.label = label
        self.keys = tuple(keys)

    def _enabled(self, runtime: Any) -> bool:
        if runtime is None:
            return False
        values = [bool(runtime.config.get(k, False)) for k in self.keys]
        return any(values)

    def _call_quietly(self, label: str, fn, /, *args: Any) -> None:
        """调用运行时的 stop/resume 等控制方法；失败只降级不抛出。

        接管/恢复是"尽力而为"：某个运行时组件停不下来不应阻断整个 feature 切换，
        因此这里吞掉异常，但用 debug 留痕，避免排查时完全无线索。
        """
        try:
            fn(*args)
        except Exception as e:
            logger.debug(f"[Feature] {self.label} {label} 失败（已忽略）: {e}")

    def status(self, bot_id: Any) -> dict:
        runtime = self.runtime(bot_id)
        return {
            "feature_id": self.feature_id,
            "label": self.label,
            "enabled": self._enabled(runtime) if runtime is not None else False,
            "keys": list(self.keys),
        }

    def suppress(self, bot_id: Any) -> dict:
        runtime = self.runtime(bot_id)
        if runtime is None:
            return {}
        snapshot = {k: runtime.config.get(k) for k in self.keys}
        for key in self.keys:
            runtime.config.set(key, False)
        logger.info(f"[Feature] {self.label} 已由插件接管并暂停 ({bot_id})")
        return snapshot

    def restore(self, bot_id: Any, snapshot: dict) -> None:
        runtime = self.runtime(bot_id)
        if runtime is None or not snapshot:
            return
        for key, value in snapshot.items():
            runtime.config.set(key, value)
        logger.info(f"[Feature] {self.label} 已恢复 ({bot_id})")


class AgentFeatureController(_RuntimeAccess, FeatureController):
    """框架 Agent 整体启停（模块 authority 层面的接管）。"""

    feature_id = "agent"
    label = "Agent"

    def status(self, bot_id: Any) -> dict:
        runtime = self.runtime(bot_id)
        return {
            "feature_id": self.feature_id,
            "label": self.label,
            "enabled": bool(runtime.config.enabled) if runtime is not None else False,
        }

    def suppress(self, bot_id: Any) -> dict:
        runtime = self.runtime(bot_id)
        if runtime is None:
            return {}
        snapshot = {"enabled": bool(runtime.config.enabled)}
        runtime.config.set_enabled(False)
        self._call_quietly("暂停", runtime.proactive.stop)
        self._call_quietly("暂停", runtime.scheduler.stop)
        logger.info(f"[Feature] Agent 已由插件接管并暂停 ({bot_id})")
        return snapshot

    def restore(self, bot_id: Any, snapshot: dict) -> None:
        runtime = self.runtime(bot_id)
        if runtime is None or not snapshot:
            return
        runtime.config.set_enabled(bool(snapshot.get("enabled", True)))
        self._call_quietly("恢复", runtime.proactive.resume)
        self._call_quietly("恢复", runtime.scheduler.resume)
        logger.info(f"[Feature] Agent 已恢复 ({bot_id})")


class RuntimeSlotFeatureController(_RuntimeAccess, FeatureController):
    """通用「把接管者装进 runtime 某个属性槽」的能力控制器。

    子类只需声明：``feature_id`` / ``label`` / ``attr``（runtime 上的属性名）/
    ``require_methods``（接管者必须具备的可调用方法）/ ``default_value()``。

    接管 = ``runtime.<attr> = owner``；交棒 = 指回仍在场的持有者；
    释放 = 还原快照（快照为空则用 ``default_value()``）。接管者不合格时忽略并留痕，
    避免把坏对象装进运行时后每轮请求都炸。
    """

    attr: str = ""
    require_methods: tuple[str, ...] = ()

    def _usable(self, owner: Any) -> bool:
        return all(callable(getattr(owner, name, None)) for name in self.require_methods)

    @staticmethod
    def _owner_label(owner: Any) -> str:
        return str(getattr(owner, "name", None) or getattr(owner, "module_name", None) or owner)

    def default_value(self) -> Any:
        """快照为空时还原成什么（子类必须覆盖）。"""
        raise NotImplementedError

    def status(self, bot_id: Any) -> dict:
        runtime = self.runtime(bot_id)
        current = getattr(runtime, self.attr, None) if runtime is not None else None
        return {
            "feature_id": self.feature_id,
            "label": self.label,
            "enabled": runtime is not None,
            self.attr: type(current).__name__ if current is not None else "",
        }

    def suppress(self, bot_id: Any) -> dict:
        """无占有者的接管入口：不知道要装谁，只能忽略。"""
        logger.warning(
            f"[Feature] {self.label} 接管缺少占有者，已忽略"
            f"（请用 supersedes=(\"{self.feature_id}\",)）"
        )
        return {}

    def suppress_with_owner(self, bot_id: Any, owner: Any) -> dict | None:
        """把槽位换成接管者；**拒绝接管时返回 None**（`{}` 是"接下了但没有快照"）。

        拒绝的原因（没有运行时 / 接管者缺方法）必须让 `FeatureRegistry` 知道——
        否则会记一条"已接管"的租约，但运行时属性根本没换，状态与实际不一致。
        """
        runtime = self.runtime(bot_id)
        if runtime is None:
            logger.warning(
                f"[Feature] {self._owner_label(owner)} 声明接管{self.label}但运行时尚未装配，已忽略"
            )
            return None
        if not self._usable(owner):
            logger.warning(
                f"[Feature] {self._owner_label(owner)} 声明接管{self.label}但缺少 "
                f"{'/'.join(self.require_methods)}()，已忽略"
            )
            return None
        snapshot = {self.attr: getattr(runtime, self.attr, None)}
        setattr(runtime, self.attr, owner)
        logger.info(f"[Feature] {self.label} 已由 {self._owner_label(owner)} 接管 ({bot_id})")
        return snapshot

    def restore(self, bot_id: Any, snapshot: dict) -> None:
        runtime = self.runtime(bot_id)
        if runtime is None or not snapshot:
            return
        setattr(runtime, self.attr, snapshot.get(self.attr) or self.default_value())
        logger.info(f"[Feature] {self.label} 已恢复 ({bot_id})")

    def on_owner_changed(self, bot_id: Any, owner: Any) -> None:
        """交棒：把槽指回仍然在场的租约持有者。"""
        runtime = self.runtime(bot_id)
        if runtime is None or not self._usable(owner):
            return
        setattr(runtime, self.attr, owner)
        logger.info(f"[Feature] {self.label} 交棒给 {self._owner_label(owner)} ({bot_id})")


class AssemblerFeatureController(RuntimeSlotFeatureController):
    """消息组装器接管：把 ``runtime.assembler`` 换成插件自己的装配器。

    插件声明 ``supersedes = ("assembly",)`` 并提供 ``build(req) -> list[dict]``，
    即可整体替换本轮请求 messages 的组装（1:1 复刻第三方组装格式的接入点）；
    卸载/禁用时自动还原框架默认装配器。
    """

    feature_id = "assembly"
    label = "消息组装"
    attr = "assembler"
    require_methods = ("build",)

    def default_value(self) -> Any:
        # 局部 import：modules 层不在模块级依赖 llm 层（装配根才允许知道全部组件）
        from app.agent.assembly.assembler import PromptAssembler

        return PromptAssembler()


class ProviderInvokerFeatureController(RuntimeSlotFeatureController):
    """provider 调用策略接管：把 ``runtime.invoker`` 换成插件自己的调用器。

    插件声明 ``supersedes = ("provider_invoker",)`` 并实现 ``chat()`` / ``stream()``，
    即可换掉框架既定的调用约定（回退链、重试、参数透传），迁移到第三方框架的
    provider 层时用它；卸载/禁用时自动还原默认调用策略。
    """

    feature_id = "provider_invoker"
    label = "Provider 调用策略"
    attr = "invoker"
    require_methods = ("chat", "stream")

    def default_value(self) -> Any:
        from app.agent.providers.invoker import ProviderInvoker

        return ProviderInvoker()


class OutputFeatureController(RuntimeSlotFeatureController):
    """输出策略接管：把 ``runtime.output`` 换成插件自己的回复产出策略。

    插件声明 ``supersedes = ("output",)`` 并实现
    ``respond(ctx, *, should_stop=None)``（异步产出 ``Message``），即可整体替换
    "一轮回复怎么产出来"——流式、非流式、发言通道、或自定义形态。
    卸载/禁用时还原成 ``None``（= 框架按 ``stream_output`` 自动选择，行为不变）。
    """

    feature_id = "output"
    label = "输出策略"
    attr = "output"
    require_methods = ("respond",)

    def default_value(self) -> Any:
        # None = 交给 select_default_output() 按配置决定，保持既有行为
        return None


