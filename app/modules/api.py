"""插件开发 API：暴露给业务模块使用的框架能力。

- get_modules():     获取已加载模块对象列表
- get_config_path(): module/configs/<name>/（模块配置目录）
- get_data_path():   module/data/<name>/（模块自定义持久化数据目录，自动创建）
"""

from __future__ import annotations

from typing import Any


def get_modules() -> list[Any]:
    """获取已加载的模块对象列表（扁平，含全局与各 Bot 实例，排除子模块）。"""
    from app.bootstrap import get_container
    from app.modules.registry import ModuleRegistry

    container = get_container()
    return container.get(ModuleRegistry).loaded()


def get_features(bot_id: Any = None) -> list[dict]:
    """获取全局能力注册表状态（含接管者信息）。"""
    from app.bootstrap import get_container
    from app.modules.features import FeatureRegistry

    container = get_container()
    return container.get(FeatureRegistry).status(bot_id)


def get_config_path(module_name: str, create: bool = True) -> str:
    """获取模块配置目录（module/configs/<name>/）。"""
    from app.bootstrap import get_container
    from app.core.settings import Settings

    container = get_container()
    path = container.get(Settings).module_configs_dir / module_name
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return str(path)


def get_data_path(module_name: str, create: bool = True) -> str:
    """获取模块数据目录（module/data/<name>/），供插件持久化自定义数据。"""
    from app.bootstrap import get_container
    from app.core.settings import Settings

    container = get_container()
    path = container.get(Settings).module_data_dir / module_name
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return str(path)


async def register_daily_schedule(
    module,
    *,
    key_suffix: str,
    enable_key: str,
    time_key: str,
    handler_factory,
) -> bool:
    """按配置的每日时间动态注册定时任务；未启用则注销（模块 on_load 调用）。

    Args:
        module: 模块实例（须有 ctx.services.scheduler）
        key_suffix: 任务 key 后缀（如 "daily" / "cron"），同模块同后缀互斥
        enable_key: 启用开关配置键
        time_key: 每日触发时间配置键（HH:MM[:SS] 或 5 字段 cron）
        handler_factory: () -> 无参异步处理器（如 lambda: functools.partial(daily_push, module, bot)）
    """
    scheduler = module.ctx.services.scheduler
    if scheduler is None or module.bot_id is None:
        return False
    if not module.config.get(enable_key, False):
        await scheduler.unload_module(module.module_name, module.bot_id)
        return False
    time_str = module.config.get(time_key, "00:00")
    key = f"{module.module_name}:{module.bot_id}:{key_suffix}"
    await scheduler.register(key, time_str, handler_factory())
    return True
