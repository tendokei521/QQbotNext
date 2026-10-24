"""Bot 应用服务：WebUI 面向的能力入口 + 登录后模块装配。

替代原 webui/app.py 中散落的业务函数与 main.py 的轮询主循环。
"""

from __future__ import annotations

from app.agent.config import LEGACY_LLM_CONNECTION_KEYS
from app.core.logger import logger
from app.domain.bot import IBot
from app.infrastructure.onebot.endpoint import FORWARD
from app.plugin.authority import declares_single_service
from app.plugin.schema_utils import webui_schema


class BotService:
    def __init__(self, *, gateway, registry, config_service, dispatcher, agent_manager=None,
             lifecycle_hooks=None, resource_registry=None) -> None:
        self.gateway = gateway
        self.registry = registry
        self.config_service = config_service
        self.dispatcher = dispatcher
        self.agent_manager = agent_manager
        self.lifecycle_hooks = lifecycle_hooks
        self.resource_registry = resource_registry

        # 网关回调注入：登录装配 / 事件派发 / 配置读取
        self.gateway.login_handler = self.on_bot_login
        self.gateway.dispatch_handler = self.dispatcher.dispatch
        self.gateway.bots_provider = self.config_service.get_bots
        # 上次登录账号快照：登录成功写入，查询时读取（断开后前端回退展示）
        self.gateway.account_saver = self.on_account_login
        self.gateway.account_lookup = self.config_service.get_bot_accounts

    # ==================== 登录装配 ====================
    async def on_bot_login(self, conn: IBot) -> None:
        # 先装配 Agent 运行时，再加载模块；这样模块的 @llm_hook 才能注册到 runtime.llm_hooks。
        # 键一律用 account_id（账号 id，用户设定且不可变）——不能再用 QQ 号：同一账号
        # 换个 QQ 登录时配置应当跟着账号走，而不是跟着 QQ 走。
        if self.agent_manager is not None:
            bind = getattr(self.agent_manager, "bind_account", None) or self.agent_manager.ensure_runtime
            bind(conn.account_id, bot=conn)
        await self.registry.load_all(conn.account_id, bot=conn)
        logger.info(f"[BotService] 账号「{getattr(conn, 'label', '?')}」模块装配完成")
        if self.lifecycle_hooks is not None:
            try:
                await self.lifecycle_hooks.run(conn, "login", "已登录")
            except Exception as e:
                logger.warning(f"[BotService] 登录钩子执行异常: {e}")

    async def on_account_login(self, account_id: str, bot_id: int | None, nickname: str) -> bool:
        """登录成功 → 记录该账号本次登录的 QQ 快照。

        账号未变化时 ConfigService 内部为 no-op（不写库、不通知），
        符合「账号相同不变、不同才换」：只有换了 QQ 才广播刷新前端。
        """
        changed = await self.config_service.save_bot_account(account_id, bot_id, nickname)
        if changed:
            name = self.config_service.get_account_name(account_id)
            logger.info(f"[BotService] 账号「{name}」的登录 QQ 已更新: {bot_id} ({nickname})")
            await self._notify_account_changed(account_id)
        return changed

    async def _notify_account_changed(self, account_id: str) -> None:
        """广播「登录 QQ 已更新」，让所有已打开的 WebUI 立即换成新账号信息。"""
        from app.core.event_bus import BotAccountUpdatedEvent, event_bus

        conn = self.gateway.connections.get(str(account_id))
        try:
            await event_bus.publish(BotAccountUpdatedEvent(
                account_id=str(account_id),
                user_id=conn.bot_id if conn else None,
            ))
        except Exception as e:
            logger.warning(f"[BotService] 账号变更广播失败（{account_id}）: {e}")

    # ==================== 连接操作（WebUI 调用） ====================
    async def start(self) -> None:
        await self.gateway.start_all(self.config_service.get_bots())

    async def connect(self, account_id: str) -> bool:
        return await self.gateway.connect_bot(account_id)

    async def disconnect(self, account_id: str) -> None:
        # manual=True：用户主动断开，监督循环不得把它自动连回来
        # （否则 auto_connect 账号最多 10s 后又连接，日志表现为「断开后又连上」）
        await self.gateway.disconnect_bot(account_id, manual=True)

    async def reconnect(self, account_id: str) -> bool:
        return await self.gateway.reconnect_bot(account_id)

    async def add_bot(
        self,
        ws_url: str,
        owner_id: int | None,
        auto_connect: bool = False,
        conn_mode: str = FORWARD,
        account_id: str | None = None,
        name: str = "",
    ) -> str:
        """新增账号：先落配置（分配/校验 id），再建网关连接对象，返回账号 id。"""
        account_id = await self.config_service.add_bot({
            "id": account_id,
            "name": name,
            "ws_url": ws_url,
            "owner_id": owner_id,
            "auto_connect": auto_connect,
            "conn_mode": conn_mode,
        })
        await self.gateway.add_bot(
            ws_url, owner_id, auto_connect, account_id=account_id,
            conn_mode=conn_mode, name=self.config_service.get_account_name(account_id),
        )
        return account_id

    async def delete_bot(self, account_id: str) -> bool:
        await self.gateway.del_bot(account_id)
        return await self.config_service.delete_bot(account_id)

    async def save_bots_config(self, bots: list[dict]) -> None:
        await self.config_service.save_bots(bots)

    # ==================== 查询（WebUI 渲染） ====================
    def get_bots_data(self) -> list[dict]:
        return self.gateway.get_bots_info()

    def get_bots_groups(self) -> dict[str, dict]:
        result: dict = {}
        for account_id, conn in self.gateway.connections.items():
            if conn.all_group_list:
                result[str(account_id)] = {
                    "account_id": account_id,
                    "name": getattr(conn, "account_name", "") or account_id,
                    "bot_id": conn.bot_id,
                    "groups": conn.all_group_list,
                    "groups_info": conn.all_group_list_info,
                }
        return result

    def get_modules_data(self, account_id: str | None = None) -> dict[str, dict]:
        """模块数据（WebUI 渲染所需），返回格式与原实现一致。

        account_id 有指定时优先取该账号的实例；未加载则回退全局(None)实例，
        保证页面在 Bot 未连接时也能渲染模块列表。
        """
        data: dict = {}
        for mod_name, bot_modules in self.registry.loaded_map().items():
            if not bot_modules:
                continue
            mod = None
            if account_id is not None:
                mod = bot_modules.get(account_id)
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
                "permission": mod.permission,
                "account_id": mod.bot_id,
                "category": getattr(mod, "category", "未分类"),
                "tags": list(getattr(mod, "tags", []) or []),
                "order": int(getattr(mod, "order", 100) or 100),
                "hidden": bool(getattr(mod, "hidden", False)),
                "pinned": bool(getattr(mod, "pinned", False)),
                "permission_config": {
                    "group_mode": mod.authority.permission.group_mode,
                    "group_list": mod.authority.permission.group_list,
                    "user_mode": mod.authority.permission.user_mode,
                    "user_list": mod.authority.permission.user_list,
                },
                "config": _mask_password_config(dict(mod.config.raw_config), mod.config_schema),
                "config_schema": webui_schema(
                    mod.config_schema, registry=self.resource_registry, source_module=mod_name
                ),
                "has_page": self.registry.module_has_page(mod_name),
                "source": getattr(mod, "source", "local"),
                "version": getattr(mod, "version", "") or "0.0.0",
                "can_uninstall": True,
                "provides": list(getattr(mod, "provides", ()) or ()),
                "supersedes": list(getattr(mod, "supersedes", ()) or ()),
                # 单一服务是声明制能力：未声明的模块，WebUI 不给开关
                "single_service": declares_single_service(mod),
            }
        # 虚拟 Agent 模块（框架级注入，不依赖模块目录）：即使所有模块被删也保留 LLM 界面
        data["agent"] = self._agent_module_data(account_id)
        return data

    def _agent_module_data(self, account_id: str | None) -> dict:
        """框架级 LLM 模块条目：读 AgentRuntime（无运行时则用默认值）。"""
        from app.agent.config import DEFAULT_LLM_CONFIG
        from app.agent.config_schema import SCHEMA
        from app.plugin.base import ModulePermission

        runtime = self.agent_manager.get_runtime(account_id) if self.agent_manager and account_id else None
        if runtime is not None:
            config = dict(runtime.config.raw_config)
            enabled = runtime.config.enabled
            perm = runtime.config.permission
            permission = runtime.config.get("permission", "member")
        else:
            config = dict(DEFAULT_LLM_CONFIG)
            enabled = True
            perm = ModulePermission()
            permission = config.get("permission", "member")
        for key in LEGACY_LLM_CONNECTION_KEYS:
            config.pop(key, None)
        schema = webui_schema(SCHEMA, registry=self.resource_registry, source_module="agent")
        return {
            "name": "LLM服务",
            "name_sign": "Agent",
            "description": "框架级 LLM 角色扮演对话",
            "enabled": enabled,
            "permission": permission,
            "account_id": account_id,
            "category": "LLM",
            "tags": ["LLM", "Agent"],
            "order": 0,
            "hidden": False,
            "pinned": True,
            "permission_config": {
                "group_mode": perm.group_mode,
                "group_list": perm.group_list,
                "user_mode": perm.user_mode,
                "user_list": perm.user_list,
            },
            "config": _mask_password_config(config, SCHEMA),
            "config_schema": schema,
            # 不设独立 page：Agent 配置走框架自身的 schema 表单（与普通模块统一排版），
            # 读写由 app/webui/api/modules.py 的 _AgentProxy 桥接到 AgentRuntime。
            "has_page": False,
            "provides": [],
            "supersedes": [],
            # 框架级 Agent 恒支持单一服务（与 inbound.py 的 _AgentGate 声明一致）
            "single_service": True,
        }

    def get_module_data(self, module_name: str, account_id: str | None = None) -> dict | None:
        return self.get_modules_data(account_id).get(module_name)

    async def get_bot_id(self, account_id: str) -> int | None:
        """该账号当前登录的 QQ 号（运行时信息，不是身份）。"""
        return await self.gateway.get_bot_id(account_id)

    # ==================== 模块重载 ====================
    async def reload_modules(self, account_id: str | None = None) -> int:
        await self.registry.reload_all(account_id)
        return len(self.registry.module_names())

    async def shutdown(self) -> None:
        await self.gateway.shutdown()


def _split_schema(schema: dict) -> dict:
    """兼容旧调用：schema 分区已迁到 `app.plugin.schema_utils.split_schema`。"""
    from app.plugin.schema_utils import split_schema

    return split_schema(schema)


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
