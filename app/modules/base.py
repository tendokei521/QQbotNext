"""插件协议：BaseModule / ModuleContext / ModuleConfig / ModuleAuthority。

模块只实现业务，权限/启停/事件过滤由框架（dispatcher）统一完成。
"""

from __future__ import annotations

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
    providers: Any = None
    scheduler: Any = None
    agent_manager: Any = None  # 框架级 LLM Agent 运行时管理
    send_hooks: Any = None     # 消息发送成功钩子注册表 SendHookRegistry
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

    def __init__(self, ctx: ModuleContext) -> None:
        self.ctx = ctx
        self.config = ctx.config
        self.authority = ctx.authority
        self.module_name = ctx.module_name
        self.bot_id = ctx.bot_id
        # 装饰器收集到的模块流水线钩子（由 ModuleRegistry 填充）
        self._module_hooks: list[dict] = []
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
    def collect_send_hooks(cls) -> list[dict]:
        """收集类中所有 @send_hook 装饰的发送后钩子。"""
        send_hooks: list[dict] = []
        for klass in reversed(cls.__mro__):
            for name, attr in vars(klass).items():
                for meta in getattr(attr, "__send_hook_meta__", []):
                    send_hooks.append({"method": name, **meta})
        return send_hooks

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

    # ---------- 业务入口 ----------
    async def handle(self, event: BaseEvent) -> None:
        """旧版单入口；使用 @module_hook 的模块可以忽略。"""
        return
