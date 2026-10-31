"""长期记忆模块（llm_memory，v3）：接管记忆策略配置与全部实现。

## 它负责什么

记忆的存储、抽取、巩固、召回、画像、工具与命令现在**全部在本模块**（`src/`），
框架只保留四个通用接缝：`context_providers`（每轮上下文块）、`tool_sources`
（模型工具）、`llm_hooks`（pre_request / turn_completed）和 `runtime.memory_service`
（归档/重置等生命周期句柄）。

## 配置与开关

23 个策略键归本模块（`schemas.py`）。`on_load` 把 `ConfigView` 挂到
`runtime.memory_options`，框架侧 `policy_get(runtime, "memory", key)` 全部走模块配置；
**没有任何额外的「实验性总开关」**：`memory_enable` 即唯一总开关。

## 数据

`data/llm/<bot_id>/memory/memory.db`（SQLite，旧 v2 库自动增量迁移）。卸载模块不会
删除数据。
"""

from __future__ import annotations

from typing import Any

from app.plugin import BaseModule, GreedyStr, command_group

from .src.schemas import MEMORY_SCHEMA

#: 本模块持有的配置键（与 `config_partition.MODULE_KEYS["llm_memory"]` 一致）
OWNED_KEYS: tuple[str, ...] = tuple(MEMORY_SCHEMA.keys())


class Module(BaseModule):
    name = "长期记忆"
    sign = "LlmMemory"
    description = "记住用户的偏好和事实，之后聊天时想起来"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    order = 50

    default_config = {key: field.get("default") for key, field in MEMORY_SCHEMA.items()}
    config_schema = MEMORY_SCHEMA

    # ── 生命周期 ─────────────────────────────────────────
    async def on_load(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        from app.agent.delivery.output import ConfigView
        from app.agent.loop.providers import register_context_provider, register_tool_source

        from .src.service import MemoryLifecycle, MemoryProvider, MemoryToolSource

        runtime.memory_options = ConfigView(
            getattr(runtime, "config", None), self.config, OWNED_KEYS
        )
        # 文档化句柄：框架（归档/重置/装配）与工具都按鸭子类型用它
        runtime.memory_service = self.memory_manager()
        register_context_provider(runtime, MemoryProvider(self))
        register_tool_source(runtime, MemoryToolSource(self))
        lifecycle = MemoryLifecycle(self)
        runtime.llm_hooks.register(
            stage="pre_request", event_type="*", order=60,
            handler=lifecycle.on_pre_request, module=self,
        )
        runtime.llm_hooks.register(
            stage="turn_completed", event_type="*", order=60,
            handler=lifecycle.on_turn_completed, module=self,
        )
        # 向量回填 worker：托管给模块后台任务（卸载/热重载自动取消）
        manager = self.memory_manager()
        if manager is not None:
            spawn = getattr(self, "spawn", None)
            services = getattr(getattr(self, "ctx", None), "services", None)
            task_manager = getattr(services, "task_manager", None)
            if callable(spawn) and getattr(task_manager, "create_task", None) is not None:
                spawn(manager.embedding_worker(), name=f"memory-embeddings:{self.bot_id}")
            else:
                manager.start_worker()

    async def on_unload(self) -> None:
        runtime = self._runtime()
        if runtime is None:
            return
        from app.agent.loop.providers import unregister_owner

        unregister_owner(runtime, self)
        manager = getattr(self, "_manager", None)
        if manager is not None:
            try:
                manager.stop()
            except Exception as e:  # noqa: BLE001 —— 停止失败不该阻断卸载，但要留痕
                from app.core.logger import logger

                logger.debug(f"[llm_memory] 停止记忆管理器失败（已忽略）: {e}")
            self._manager = None
        runtime.memory_service = None
        runtime.memory_options = None

    def memory_manager(self) -> Any:
        """本模块的记忆管理器（命令 / 工具 / 钩子共用同一实例）。"""
        from .src.service import memory_manager

        return memory_manager(self)

    def _runtime(self) -> Any:
        services = getattr(getattr(self, "ctx", None), "services", None)
        getter = getattr(services, "runtime", None)
        return getter(self.bot_id) if callable(getter) else None

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("memory_enable", False))

    # ── 命令（/memory、／memory、#llm memory 三种前缀） ──
    @command_group(
        "memory",
        description="长期记忆（list/search/profile/forget/clear/audit/remember/export）",
        prefixes=("/", "／", "#llm "),
    )
    async def memory(self, event):
        """只发 /memory 时打印子命令表。"""
        await self._run_memory_command(event, "")

    @memory.command("list", description="列出当前可见记忆（--all 含已下架）")
    async def memory_list(self, event):
        await self._run_memory_command(event, _with_args("list", event))

    @memory.command("search", description="检索记忆")
    async def memory_search(self, event):
        await self._run_memory_command(event, _with_args("search", event))

    @memory.command("profile", description="查看关于你的画像")
    async def memory_profile(self, event):
        await self._run_memory_command(event, "profile")

    @memory.command("remember", description="记住一条关于你的信息")
    async def memory_remember(self, event, text: GreedyStr = ""):
        await self._run_memory_command(event, f"remember {text}".strip())

    @memory.command("forget", description="删除本人记忆（id 或关键词）")
    async def memory_forget(self, event, target: GreedyStr = ""):
        await self._run_memory_command(event, f"forget {target}".strip())

    @memory.command("clear", description="清空本人记忆")
    async def memory_clear(self, event):
        await self._run_memory_command(event, "clear")

    @memory.command("audit", description="记忆事件审计（管理员）")
    async def memory_audit(self, event, owner: str = ""):
        await self._run_memory_command(event, f"audit {owner}".strip())

    @memory.command("export", description="导出本人记忆（JSON 文本）")
    async def memory_export(self, event):
        await self._run_memory_command(event, "export")

    async def _run_memory_command(self, event, action: str) -> None:
        """把命令转给 `src/commands.py`（纯函数解析，便于测试与直调）。"""
        from .src.commands import handle_memory_command

        session_id, user_id, is_private = _event_session(event)
        is_admin = bool(getattr(event, "is_admin", False))

        async def send(text: str) -> None:
            reply = getattr(event, "reply", None)
            if reply is None:
                return
            await reply(text)

        try:
            await handle_memory_command(
                self, session_id, user_id, is_private, is_admin, action, send
            )
        except Exception as e:  # noqa: BLE001 —— 命令异常不能掀翻节点链
            from app.core.logger import logger

            logger.add_info(str(getattr(self, "bot_id", "?"))).exception(
                f"[llm_memory] 命令处理异常: {e}"
            )
            await send("记忆命令执行失败，请稍后再试")


def _event_session(event: Any) -> tuple[str, str, bool]:
    """从事件推导 (session_id, user_id, is_private)。"""
    user_id = str(getattr(event, "user_id", "") or "")
    message_type = str(getattr(event, "message_type", "") or "")
    group = getattr(event, "group", None)
    if message_type == "private" or (message_type == "" and group is None):
        return f"private_{user_id}", user_id, True
    group_id = getattr(group, "group_id", None) if group is not None else None
    if group_id is None:
        return f"private_{user_id}", user_id, True
    return f"group_{group_id}", user_id, False


def _with_args(sub: str, event: Any) -> str:
    text = str(getattr(getattr(event, "command", None), "text", "") or "").strip()
    return f"{sub} {text}".strip()


def owned_keys() -> tuple[str, ...]:
    """本模块持有的配置键（供迁移脚本与契约测试核对）。"""
    return OWNED_KEYS
