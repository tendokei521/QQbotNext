"""Bot 应用服务：WebUI 面向的能力入口 + 登录后模块装配。

替代原 webui/app.py 中散落的业务函数与 main.py 的轮询主循环。
"""

from __future__ import annotations


from app.core.logger import logger
from app.domain.bot import IBot


class BotService:
    def __init__(self, *, gateway, registry, config_service, dispatcher, agent_manager=None) -> None:
        self.gateway = gateway
        self.registry = registry
        self.config_service = config_service
        self.dispatcher = dispatcher
        self.agent_manager = agent_manager

        # 网关回调注入：登录装配 / 事件派发 / 配置读取
        self.gateway.login_handler = self.on_bot_login
        self.gateway.dispatch_handler = self.dispatcher.dispatch
        self.gateway.bots_provider = self.config_service.get_bots

    # ==================== 登录装配 ====================
    async def on_bot_login(self, conn: IBot) -> None:
        await self.registry.load_all(conn.bot_id, bot=conn)
        # 框架级 Agent 运行时装配（与模块加载解耦，定时/主动随 Bot 登录即生效）
        if self.agent_manager is not None:
            self.agent_manager.ensure_runtime(conn.bot_id, bot=conn)
        logger.info(f"[BotService] Bot {conn.bot_id} 模块装配完成")

    # ==================== 连接操作（WebUI 调用） ====================
    async def start(self) -> None:
        await self.gateway.start_all(self.config_service.get_bots())

    async def connect(self, index: int) -> bool:
        return await self.gateway.connect_bot(index)

    async def disconnect(self, index: int) -> None:
        await self.gateway.disconnect_bot(index)

    async def reconnect(self, index: int) -> bool:
        return await self.gateway.reconnect_bot(index)

    async def add_bot(self, ws_url: str, owner_id: int | None, auto_connect: bool = False) -> int:
        index = await self.config_service.add_bot(
            {"ws_url": ws_url, "owner_id": owner_id, "auto_connect": auto_connect}
        )
        await self.gateway.add_bot(ws_url, owner_id, auto_connect, index=index)
        return index

    async def delete_bot(self, index: int) -> bool:
        await self.gateway.del_bot(index)
        return await self.config_service.delete_bot(index)

    async def save_bots_config(self, bots: list[dict]) -> None:
        await self.config_service.save_bots(bots)

    # ==================== 查询（WebUI 渲染） ====================
    def get_bots_data(self) -> list[dict]:
        return self.gateway.get_bots_info()

    def get_bots_groups(self) -> dict[str, dict]:
        result: dict = {}
        for index, conn in self.gateway.connections.items():
            if conn.all_group_list:
                result[str(index)] = {
                    "bot_id": conn.bot_id,
                    "index": index,
                    "groups": conn.all_group_list,
                    "groups_info": conn.all_group_list_info,
                }
        return result

    def get_modules_data(self, bot_id: int | None = None) -> dict[str, dict]:
        """模块数据（WebUI 渲染所需），返回格式与原实现一致。

        bot_id 有指定时优先取该 bot 的实例；未加载则回退全局(None)实例，
        保证页面在 Bot 未连接时也能渲染模块列表。
        """
        data: dict = {}
        for mod_name, bot_modules in self.registry.loaded_map().items():
            if not bot_modules:
                continue
            mod = None
            if bot_id is not None:
                mod = bot_modules.get(bot_id)
                if mod is None:
                    mod = bot_modules.get(None)
            else:
                mod = bot_modules.get(None) or next(iter(bot_modules.values()))
            if mod is None:
                continue

            data[mod_name] = {
                "name": mod.name,
                "name_sign": mod.sign,
                "description": mod.description,
                "enabled": mod.authority.enabled,
                "authority_type": mod.authority_type,
                "bot_id": mod.bot_id,
                "permission": {
                    "group_mode": mod.authority.permission.group_mode,
                    "group_list": mod.authority.permission.group_list,
                    "user_mode": mod.authority.permission.user_mode,
                    "user_list": mod.authority.permission.user_list,
                },
                "config": _mask_password_config(dict(mod.config.raw_config), mod.config_schema),
                "config_schema": _split_schema(mod.config_schema),
                "has_page": self.registry.module_has_page(mod_name),
            }
        # 虚拟 Agent 模块（框架级注入，不依赖模块目录）：即使所有模块被删也保留 LLM 界面
        data["agent"] = self._agent_module_data(bot_id)
        return data

    def _agent_module_data(self, bot_id: int | None) -> dict:
        """框架级 LLM 模块条目：读 AgentRuntime（无运行时则用默认值）。"""
        from app.llm.config import DEFAULT_LLM_CONFIG
        from app.llm.config_schema import SCHEMA
        from app.modules.base import ModulePermission

        runtime = self.agent_manager.get_runtime(bot_id) if self.agent_manager and bot_id else None
        if runtime is not None:
            config = dict(runtime.config.raw_config)
            enabled = runtime.config.enabled
            perm = runtime.config.permission
            authority_type = runtime.config.get("authority_type", "strict")
        else:
            config = dict(DEFAULT_LLM_CONFIG)
            enabled = True
            perm = ModulePermission()
            authority_type = config.get("authority_type", "strict")
        return {
            "name": "LLM服务",
            "name_sign": "Agent",
            "description": "框架级 LLM 角色扮演对话",
            "enabled": enabled,
            "authority_type": authority_type,
            "bot_id": bot_id,
            "permission": {
                "group_mode": perm.group_mode,
                "group_list": perm.group_list,
                "user_mode": perm.user_mode,
                "user_list": perm.user_list,
            },
            "config": _mask_password_config(config, SCHEMA),
            "config_schema": _split_schema(SCHEMA),
            "has_page": True,
        }

    def get_module_data(self, module_name: str, bot_id: int | None = None) -> dict | None:
        return self.get_modules_data(bot_id).get(module_name)

    async def get_bot_id(self, index: int) -> int | None:
        return await self.gateway.get_bot_id(index)

    # ==================== 模块重载 ====================
    async def reload_modules(self, bot_id: int | None = None) -> int:
        await self.registry.reload_all(bot_id)
        return len(self.registry.module_names())

    async def shutdown(self) -> None:
        await self.gateway.shutdown()


def _split_schema(schema: dict) -> dict:
    groups: dict = {}
    items: dict = {}
    for key, value in (schema or {}).items():
        if isinstance(value, dict) and value.get("type") == "group":
            groups[key] = value
        else:
            items[key] = value
    return {"groups": groups, "items": items}


# 密码字段脱敏哨兵：前端显示此值，保存时后端识别并保留旧值
PASSWORD_MASK = "••••••••"


def _mask_password_config(config: dict, schema: dict) -> dict:
    """把 password 类型字段的值替换为脱敏哨兵，避免 API/页面泄露明文。"""
    result = dict(config)
    for key, field in (schema or {}).items():
        if isinstance(field, dict) and field.get("type") == "password":
            if result.get(key):
                result[key] = PASSWORD_MASK
    return result
