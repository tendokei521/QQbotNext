"""插件协议：BaseModule / ModuleContext / ModuleConfig / ModuleAuthority。

模块只实现业务，权限/启停/事件过滤由框架（dispatcher）统一完成。
"""

from __future__ import annotations

from app.core.logger import account_label

from abc import ABC
from dataclasses import dataclass, field
from typing import Any

from app.domain.bot import IBot
from app.domain.events import BaseEvent


class ModuleConfig:
    """模块配置门面：深合并默认值 + 已存配置，读写走 ConfigService。

    特性（移植自 AstrBot Config）：
    - 递归合并：补缺 / None 回填 / 类型修复（保留多余键，不删键）；
    - set() 时按旧值类型做安全转换（bool/int/float/str，容器原样）；
    - 支持点号只读访问 config.api_key。
    """

    def __init__(self, module_name: str, bot_id: Any, default_config: dict, service) -> None:
        self.module_name = module_name
        self.bot_id = bot_id
        self._default = dict(default_config or {})
        self._service = service

    def get(self, key: str, default: Any = None) -> Any:
        data = self._service.get_module_config(self.module_name, self.bot_id)
        if key in data and data[key] is not None:
            return data[key]
        if key in self._default and self._default[key] is not None:
            return self._default[key]
        return default

    @property
    def raw_config(self) -> dict:
        stored = self._service.get_module_config(self.module_name, self.bot_id) or {}
        result: dict = {}
        for key, default_value in self._default.items():
            if key in stored and stored[key] is not None:
                result[key] = ModuleConfig._merge_value(default_value, stored[key])
            else:
                result[key] = default_value
        for key, value in stored.items():  # 保留默认值之外的多余键
            if key not in result:
                result[key] = value
        return result

    def set(self, key: str, value: Any, auto_save: bool = True) -> None:
        data = dict(self.raw_config)
        old = data.get(key)
        data[key] = ModuleConfig.cast(value, type(old)) if old is not None else value
        self._service.set_module_config(self.module_name, self.bot_id, data, persist=auto_save)

    def update(self, data: dict) -> None:
        merged = dict(self.raw_config)
        for key, value in (data or {}).items():
            old = merged.get(key)
            merged[key] = ModuleConfig.cast(value, type(old)) if old is not None else value
        self._service.set_module_config(self.module_name, self.bot_id, merged, persist=False)

    def save(self) -> None:
        self._service.set_module_config(self.module_name, self.bot_id, dict(self.raw_config), persist=True)

    async def save_async(self) -> None:
        await self._service.save_module_config(self.module_name, self.bot_id, dict(self.raw_config))

    def __getattr__(self, key: str):
        """点号只读访问：config.api_key == config.get('api_key')。"""
        if key.startswith("_"):
            raise AttributeError(key)
        return self.get(key)

    # ---------- 深合并 / 类型转换 ----------

    @staticmethod
    def _merge_value(default_value: Any, stored_value: Any) -> Any:
        if isinstance(default_value, dict):
            if not isinstance(stored_value, dict):
                return default_value  # 存储类型错误 → 回退默认
            return ModuleConfig._deep_merge(default_value, stored_value)
        if isinstance(default_value, list):
            return stored_value if isinstance(stored_value, list) else default_value
        return stored_value

    @staticmethod
    def _deep_merge(defaults: dict, data: dict) -> dict:
        result = dict(data)
        for key, default_value in defaults.items():
            if key not in result or result[key] is None:
                result[key] = default_value
            elif isinstance(default_value, dict):
                if not isinstance(result[key], dict):
                    result[key] = default_value
                else:
                    result[key] = ModuleConfig._deep_merge(default_value, result[key])
            elif isinstance(default_value, list):
                if not isinstance(result[key], list):
                    result[key] = default_value
        return result

    @staticmethod
    def cast(value: Any, target_type: type) -> Any:
        """安全类型转换：仅标量（bool/int/float/str）转换，容器原样返回。"""
        if value is None:
            return None
        try:
            if target_type is bool:
                if isinstance(value, str):
                    return value.strip().lower() in ("1", "true", "yes", "on")
                return bool(value)
            if target_type is int:
                return int(value)
            if target_type is float:
                return float(value)
            if target_type is str:
                return str(value)
        except (ValueError, TypeError):
            pass
        return value


@dataclass
class ModulePermission:
    """权限配置数据类。默认黑名单+空列表 = 放行所有群/用户。"""

    group_mode: str = "blacklist"
    group_list: list[str] = field(default_factory=list)
    user_mode: str = "blacklist"
    user_list: list[str] = field(default_factory=list)


class ModuleAuthority:
    """模块权限门面：读写 ConfigService 中的 authority 数据。"""

    def __init__(self, module_name: str, bot_id: Any, service) -> None:
        self.module_name = module_name
        self.bot_id = bot_id
        self._service = service
        self._data: dict = self._service.get_module_authority(module_name, bot_id) or {}

    @property
    def enabled(self) -> bool:
        return bool(self._data.get("enabled", True))

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._data["enabled"] = bool(value)
        self.save()

    @property
    def permission(self) -> ModulePermission:
        return ModulePermission(
            group_mode=self._data.get("group_mode", "blacklist"),
            group_list=list(self._data.get("group_list", []) or []),
            user_mode=self._data.get("user_mode", "blacklist"),
            user_list=list(self._data.get("user_list", []) or []),
        )

    def set_enabled(self, enabled: bool) -> bool:
        self.enabled = enabled
        return True

    def update_permission(self, group_mode: str, group_list, user_mode: str, user_list) -> None:
        self._data["group_mode"] = group_mode
        self._data["group_list"] = list(group_list or [])
        self._data["user_mode"] = user_mode
        self._data["user_list"] = list(user_list or [])
        self.save()

    def save(self) -> bool:
        self._service.set_module_authority(self.module_name, self.bot_id, self._data)
        return True

    def add_group(self, group_id: str, auto_save: bool = True) -> None:
        groups = self._data.setdefault("group_list", [])
        if group_id not in groups:
            groups.append(group_id)
            if auto_save:
                self.save()

    def remove_group(self, group_id: str, auto_save: bool = True) -> None:
        groups = self._data.get("group_list", [])
        if group_id in groups:
            groups.remove(group_id)
            if auto_save:
                self.save()

    def add_user(self, user_id: str, auto_save: bool = True) -> None:
        users = self._data.setdefault("user_list", [])
        if user_id not in users:
            users.append(user_id)
            if auto_save:
                self.save()

    def remove_user(self, user_id: str, auto_save: bool = True) -> None:
        users = self._data.get("user_list", [])
        if user_id in users:
            users.remove(user_id)
            if auto_save:
                self.save()


@dataclass
class ServiceAccess:
    """模块可用的框架服务集合（由 bootstrap 装配注入）。"""

    cache: Any = None
    config_service: Any = None
    task_manager: Any = None
    settings: Any = None
    providers: Any = None           # 数据源注册表（ProviderRegistry）：只管 list/dynamic 的模块数据源
    #: ``providers`` 的语义化别名（同一个对象）：它跟 LLM provider 无关，
    #: 用 LLM 请用 ``llm``，别被 "providers" 这个词误导。
    data_sources: Any = None
    scheduler: Any = None
    agent_manager: Any = None  # 框架级 LLM Agent 运行时管理
    llm: Any = None            # 模块用 LLM 客户端（ModuleLLMClient）：chat/stream/models/resolve
    provider_presets: Any = None  # Provider 预设服务（ProviderPresetService）：连接配置（含密钥，只读列表用）
    provider_models: Any = None   # Provider 模型服务（ProviderModelService）：模型实例 CRUD/清单
    role_presets: Any = None     # 角色预设（RolePresetService）：可复用人设（含预设对话/工具/技能）
    resources: Any = None        # 系统资源注册表（ResourceRegistry）：schema resource 字段的候选来源
    send_hooks: Any = None     # 消息发送成功钩子注册表 SendHookRegistry
    before_send_hooks: Any = None   # 消息发送前钩子注册表
    api_hooks: Any = None           # 任意 API 调用后钩子注册表
    lifecycle_hooks: Any = None     # Bot 生命周期钩子注册表
    event_completed_hooks: Any = None  # 事件处理完成钩子注册表
    features: Any = None            # 全局 FeatureRegistry（能力接管/恢复）
    capabilities: Any = None        # 能力清单（callable() -> dict）：模块自检框架有什么
    #: 入站/出站节点注册表（NodeRegistry）：模块可注册/替换节点，出站链按它即时生效
    node_registry: Any = None
    #: callable(bot_id) -> SessionManager（会话历史读写；按 bot_id 的进程级单例）
    sessions: Any = None
    #: callable(bot_id) -> AgentRuntime | None（只读访问运行时，替代"偷容器"）
    runtime: Any = None
    #: callable(bot_id, **kw) -> (list[ToolSpec], ToolContext)（框架工具集只读视图）
    tools: Any = None
    #: 出站管道（OutboundPipeline）：诊断/自检用
    outbound: Any = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        return self.extra[key]


@dataclass
class ModuleContext:
    """模块实例的构造上下文。"""

    module_name: str
    bot_id: int | None
    config: ModuleConfig
    authority: ModuleAuthority
    services: ServiceAccess
    bot: IBot | None = None


def resolve_enabled_ids(config_value, mode: str = "all"):
    """解析 list 类型配置为「启用的 id 列表」。

    新格式（list 类型）：{<id>: {"enabled": bool, "index": int}, ...} + mode(all/partial/none)
    兼容旧格式（string_list 数组）：["id1", "id2", ...] → 直接返回。

    Args:
        config_value: 配置值（dict 或 list）
        mode: all → 全部；none → 无；partial → 仅 enabled 项
    """
    if isinstance(config_value, (list, tuple, set)):
        return [str(x) for x in config_value]
    if isinstance(config_value, dict):
        if mode == "all":
            return [str(k) for k in config_value.keys()]
        if mode == "none":
            return []
        return [str(k) for k, v in config_value.items() if isinstance(v, dict) and v.get("enabled")]
    return []


class BaseModule(ABC):
    """模块基类。子类实现 handle()，并声明元数据。"""

    name: str = "未知模块"
    sign: str = "Module"
    description: str = ""
    permission: str = "member"  # everyone / member / group_admin / group_owner / owner
    subscribe: tuple = ()           # 订阅的事件类型，如 ("message_group", "notice_poke")
    default_config: dict = {}
    config_schema: dict = {}        # 可选，供 WebUI 渲染表单
    category: str = "未分类"        # WebUI 分类
    tags: list = []                 # WebUI 标签
    order: int = 100                # 分类内排序
    hidden: bool = False            # 是否默认隐藏
    pinned: bool = False            # 是否默认置顶
    provides: tuple = ()            # 本插件提供的能力（feature_id 列表）
    supersedes: tuple = ()          # 启用时自动接管/禁用的能力（feature_id 列表）
    #: 声明的框架依赖（服务名或 feature_id，如 ("llm", "role_presets")）：
    #: 加载时自动校验，缺失只 WARNING 不阻断；模块可用 self.missing_requirements() 据此降级
    requires: tuple = ()
    #: 是否声明支持「单一服务模式」（默认 False = 不支持）。
    #:
    #: 单一服务是**声明制**能力：只有声明为 True 的模块，WebUI 才显示该开关、
    #: 写入配置才被接受、框架运行时才会按「多群管理 → 指定服务账号」过滤群消息。
    #: 未声明的模块即使配置里残留 true 也完全不受影响（绝不因误配而静默失联）。
    #: 只有真正「按群响应」的模块才该声明；纯 LLM 策略模块（subscribe=()）不需要。
    supports_single_service: bool = False
    #: 本模块在 `webui.single_service` 配置里使用的键（缺省 = module_name）。
    #:
    #: 虚拟模块用它对齐 UI 的键：框架级 Agent 的内部 module_name 是 `llm_chat_v2`
    #: 时代遗留值，而 WebUI 存的是 `"agent"`，两者必须显式钉在一起。
    single_service_key: str = ""

    def __init__(self, ctx: ModuleContext) -> None:
        self.ctx = ctx
        self.config = ctx.config
        self.authority = ctx.authority
        self.module_name = ctx.module_name
        self.bot_id = ctx.bot_id
        # 装饰器收集到的模块流水线钩子（由 ModuleRegistry 填充）
        self._module_hooks: list[dict] = []
        # 装饰器收集到的命令：(CommandSpec, 绑定后的处理器)（由 loader 填充）
        self._commands: list[tuple[Any, Any]] = []
        # 事件运行期属性（dispatcher 每事件更新）
        self.permission_granted = False

    # ---------- 钩子收集 ----------
    @classmethod
    def collect_hooks(cls) -> tuple[list[dict], list[dict]]:
        """收集类中所有 @module_hook / @llm_hook 装饰的钩子。"""
        module_hooks: list[dict] = []
        llm_hooks: list[dict] = []

        for klass in reversed(cls.__mro__):
            for name, attr in vars(klass).items():
                for meta in getattr(attr, "__module_hook_meta__", []):
                    module_hooks.append({"method": name, **meta})
                for meta in getattr(attr, "__llm_hook_meta__", []):
                    llm_hooks.append({"method": name, **meta})

        # 兼容旧的 LLM_HOOKS 类属性声明方式
        for item in getattr(cls, "LLM_HOOKS", []) or []:
            if isinstance(item, dict):
                llm_hooks.append(dict(item))

        return module_hooks, llm_hooks

    @classmethod
    def collect_commands(cls) -> list[tuple[Any, str]]:
        """收集类中所有 @command / @command_group 声明的命令：``[(spec, 方法名)]``。

        顺序 = 类体定义顺序（``vars`` 保序），同一方法可叠多个 ``@command``。
        """
        found: list[tuple[Any, str]] = []
        for klass in reversed(cls.__mro__):
            for name, attr in vars(klass).items():
                for spec in getattr(attr, "__command_meta__", []) or []:
                    found.append((spec, name))
        return found

    @classmethod
    def collect_send_hooks(cls) -> list[dict]:
        """收集类中所有 @send_hook 装饰的发送后钩子。"""
        send_hooks: list[dict] = []
        for klass in reversed(cls.__mro__):
            for name, attr in vars(klass).items():
                for meta in getattr(attr, "__send_hook_meta__", []):
                    send_hooks.append({"method": name, **meta})
        return send_hooks

    @classmethod
    def collect_before_send_hooks(cls) -> list[dict]:
        """收集类中所有 @before_send_hook 装饰的发送前钩子。"""
        hooks: list[dict] = []
        for klass in reversed(cls.__mro__):
            for name, attr in vars(klass).items():
                for meta in getattr(attr, "__before_send_hook_meta__", []):
                    hooks.append({"method": name, **meta})
        return hooks

    @classmethod
    def collect_api_hooks(cls) -> list[dict]:
        """收集类中所有 @api_hook 装饰的 API 调用后钩子。"""
        hooks: list[dict] = []
        for klass in reversed(cls.__mro__):
            for name, attr in vars(klass).items():
                for meta in getattr(attr, "__api_hook_meta__", []):
                    hooks.append({"method": name, **meta})
        return hooks

    @classmethod
    def collect_lifecycle_hooks(cls) -> list[dict]:
        """收集类中所有 @bot_lifecycle_hook 装饰的生命周期钩子。"""
        hooks: list[dict] = []
        for klass in reversed(cls.__mro__):
            for name, attr in vars(klass).items():
                for meta in getattr(attr, "__lifecycle_hook_meta__", []):
                    hooks.append({"method": name, **meta})
        return hooks

    @classmethod
    def collect_event_completed_hooks(cls) -> list[dict]:
        """收集类中所有 @event_completed_hook 装饰的处理完成钩子。"""
        hooks: list[dict] = []
        for klass in reversed(cls.__mro__):
            for name, attr in vars(klass).items():
                for meta in getattr(attr, "__event_completed_hook_meta__", []):
                    hooks.append({"method": name, **meta})
        return hooks

    @classmethod
    def collect_tool_call_hooks(cls) -> list[dict]:
        """收集类中所有 @tool_call_hook 装饰的 LLM 工具调用后钩子。"""
        hooks: list[dict] = []
        for klass in reversed(cls.__mro__):
            for name, attr in vars(klass).items():
                for meta in getattr(attr, "__tool_call_hook_meta__", []):
                    hooks.append({"method": name, **meta})
        return hooks

    # ---------- 事件入口 ----------
    async def process_event(self, event: BaseEvent) -> None:
        """模块流水线统一入口。

        优先执行装饰器注册的 @module_hook；
        没有装饰器钩子时回退到旧版 handle()。
        """
        hooks = [
            h
            for h in self._module_hooks
            if h["event_type"] in ("*", event.event_type)
        ]
        hooks.sort(key=lambda h: h["order"])

        if not hooks:
            await self.handle(event)
            return

        for hook in hooks:
            await hook["handler"](event)
            if getattr(event, "_stopped", False):
                break

    # ---------- 生命周期 ----------
    async def on_load(self) -> None: ...

    async def on_unload(self) -> None: ...

    # ---------- 框架依赖自检 ----------
    def missing_requirements(self) -> list[str]:
        """本模块 ``requires`` 里框架未提供的部分（空列表 = 满足或未声明）。

        加载时框架会自动校验并 WARNING 列出缺失项；模块也可在 ``on_load``
        或业务入口据此降级（例如没有 ``llm`` 就关掉自己的模型相关功能）。
        """
        from app.plugin.requirements import missing_requirements

        return missing_requirements(self)

    # ---------- 调系统 LLM（模块自己的模型请求） ----------
    def _usage_scope(self):
        """统计归属作用域：`source` = 本模块名，`session_id` 由调用方按需覆盖。"""
        from app.agent.usage.context import usage_scope

        return usage_scope(
            bot_id=self.bot_id,
            source=self.module_name,
            session_id=getattr(self, "_usage_session_id", "") or "",
            session_label=getattr(self, "_usage_session_label", "") or "",
            group_id=getattr(self, "_usage_group_id", "") or "",
        )

    async def llm_chat(self, messages: list[dict], **kwargs):
        """用系统 provider 做一次 LLM 请求（密钥 / 回退链 / 记账都留在框架）。

        缺省用**本账号** Agent 配置的模型池；也可传 ``model_id`` / ``preset_id`` 指定
        （通常来自配置里 ``type: "resource"`` 的下拉选择）。没有可用配置时返回空
        ``LLMResponse``（``resp.ok`` 为假），模块据此走降级分支。

        例：``resp = await self.llm_chat([{"role": "user", "content": "你好"}])``
        """
        client = getattr(getattr(self, "ctx", None), "services", None)
        client = getattr(client, "llm", None)
        if client is None:
            return self._llm_unavailable("llm_chat")
        kwargs.setdefault("bot_id", self.bot_id)
        # 归属上下文：让 provider 层的统计知道"这笔是本模块花的"
        with self._usage_scope():
            return await client.chat(messages, **kwargs)

    async def llm_stream(self, messages: list[dict], **kwargs):
        """流式版 ``llm_chat``（逐个事件产出）；没有可用配置时不产出任何事件。"""
        client = getattr(getattr(self, "ctx", None), "services", None)
        client = getattr(client, "llm", None)
        if client is None:
            self._llm_unavailable("llm_stream")
            return
        kwargs.setdefault("bot_id", self.bot_id)
        with self._usage_scope():
            async for event in client.stream(messages, **kwargs):
                yield event

    # ---------- 无事件的完整一轮对话 ----------
    async def llm_turn(
        self,
        user_text: str,
        *,
        session_id: str,
        is_private: bool | None = None,
        user_id: str = "",
        group_id: str | None = None,
        kind: str = "module",
        stream: bool | None = None,
        on_sentence=None,
        record_history: bool = False,
        send: bool = False,
        event=None,
        system_prompt: str | None = None,
        **turn_kwargs,
    ):
        """用框架跑一轮**完整对话**（取值 + 组装 + 工具循环 + 记忆 + 可选写历史）。

        与 ``llm_chat`` 的区别：那个只发一次裸请求；这个吃框架的全部上下文能力，
        且**不需要真实事件对象**——主动消息、定时任务、模块自建循环都用它。

        Args:
            user_text: 本轮用户内容（可空：纯图片/纯指令场景）。
            session_id: ``group_x`` / ``private_y``。
            is_private: 缺省按 ``session_id`` 前缀推断。
            user_id / group_id: 触发者与群（无事件场景显式给出）。
            kind: 请求性质（写进 PromptRequest.kind）。
            stream: ``True`` 流式逐句回调；``None`` 按配置。
            on_sentence: 流式回调 ``async (text) -> None``。
            record_history: 是否写进会话历史（默认 False：模块自己决定要不要污染会话）。
            send: ``True`` 时边产边用 ``send_to`` 发出去（与 ``on_sentence`` 二选一）。
            event: 可选真实事件（图片/引用指令需要）。
            system_prompt: 覆盖本轮人设。
            **turn_kwargs: 透传给 ``run_turn``。

        Returns:
            ``TurnResult``；运行时不可用时返回 ``error="no_runtime"`` 的空结果（不抛）。
        """
        from app.agent.loop.turn import TurnRequest, TurnResult, run_turn
        from app.domain.session import try_parse_session_id

        runtime = self._runtime()
        if runtime is None:
            return TurnResult(session_id=session_id, error="no_runtime")

        if is_private is None:
            parsed = try_parse_session_id(session_id)
            is_private = not parsed[0] if parsed else False

        if send:
            async def _sender(text: str) -> None:
                await self.send_to(session_id, text)

            on_sentence = _sender
            stream = True if stream is None else stream

        request = TurnRequest(
            session_id=session_id,
            is_private=bool(is_private),
            user_text=user_text,
            user_id=str(user_id or ""),
            group_id=group_id,
            kind=kind,
            event=event if event is not None else self.ctx.event if hasattr(self.ctx, "event") else None,
            bot=self._bot(),
            stream=stream,
            record_history=record_history,
            system_prompt=system_prompt,
        )
        return await run_turn(runtime, request, on_sentence=on_sentence, **turn_kwargs)

    # ---------- 发消息 / 后台任务 / 工具集 ----------
    async def send_to(self, session_id: str, message, **kwargs):
        """按会话发一条消息（唯一发送出口：走出站链 + 全部发送钩子）。

        群聊 ``group_<id>`` / 私聊 ``private_<id>`` 自动分流；正文开头的
        ``[reply]`` / ``[@qq]`` 会被解析成真正的消息段。

        例：``await self.send_to("group_123", "晚上好")``
        """
        from app.agent.delivery.channel import send_item

        kwargs.setdefault("runtime", self._runtime())
        return await send_item(self._bot(), session_id, message, **kwargs)

    def spawn(self, coro, *, name: str | None = None):
        """把协程作为本模块的后台任务托管（热重载/卸载自动取消）。

        不要直接用 ``ctx.services.task_manager.create_task``：它的 owner 默认
        ``"system"``，模块卸载时不会被清理，热重载会留下孤儿任务。
        """
        manager = getattr(getattr(self, "ctx", None), "services", None)
        manager = getattr(manager, "task_manager", None)
        if manager is None:
            raise RuntimeError("task_manager 不可用，无法托管后台任务")
        return manager.create_task(coro, name=name, owner=self.task_owner)

    @property
    def task_owner(self) -> str:
        """本模块后台任务的归属标识（与 ModuleRegistry 的清理口径一致）。"""
        return f"module:{self.module_name}:{self.bot_id}"

    @property
    def sessions(self):
        """本账号的会话历史门面（`SessionManager` 进程级单例）。"""
        getter = getattr(getattr(self, "ctx", None), "services", None)
        getter = getattr(getter, "sessions", None)
        return getter(self.bot_id) if callable(getter) else None

    async def framework_tools(self, session_id: str, **kwargs):
        """框架工具集只读视图 ``(specs, ToolContext)``。

        拿到的工具可以直接喂给 ``self.llm_chat(msgs, tools=build_tools(specs),
        tool_executor=make_executor(specs, ctx))``。
        """
        from app.agent.tools.catalog import framework_tools
        from app.domain.session import try_parse_session_id

        runtime = self._runtime()
        if runtime is None:
            return [], None
        parsed = try_parse_session_id(session_id) or (False, session_id)
        kwargs.setdefault("is_private", not parsed[0])
        kwargs.setdefault("group_id", parsed[1] if parsed[0] else None)
        kwargs.setdefault("bot", self._bot())
        return await framework_tools(runtime, session_id=session_id, **kwargs)

    # ---------- 内部：运行时 / 连接 ----------
    def _runtime(self):
        """本账号的 AgentRuntime（取不到返回 None）。"""
        services = getattr(getattr(self, "ctx", None), "services", None)
        getter = getattr(services, "runtime", None)
        if callable(getter):
            return getter(self.bot_id)
        manager = getattr(services, "agent_manager", None)
        if manager is None or self.bot_id is None:
            return None
        return manager.get_runtime(self.bot_id)

    def _bot(self):
        """当前连接：优先运行时实时绑定（换号后自动跟随），退化到构造期快照。"""
        runtime = self._runtime()
        live = getattr(runtime, "bot", None) if runtime is not None else None
        return live or getattr(getattr(self, "ctx", None), "bot", None)

    def _llm_unavailable(self, where: str):
        """框架没注入 LLM 客户端时的兜底：留痕并返回空响应（不抛，避免模块直接崩）。"""
        from app.agent.providers.base import LLMResponse
        from app.core.logger import module_logger

        module_logger.add_info(account_label(self.bot_id)).warning(
            f"[{self.module_name}] ctx.services.llm 不可用（{where}）："
            "该框架版本未提供模块 LLM 客户端"
        )
        return LLMResponse(text="", raw=None)

    # ---------- 业务入口 ----------
    async def handle(self, event: BaseEvent) -> None:
        """旧版单入口；使用 @module_hook 的模块可以忽略。"""
        return
