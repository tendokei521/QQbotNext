"""插件开发 API：暴露给业务模块使用的框架能力。

- get_modules():     获取已加载模块对象列表
- get_features():    获取能力注册表状态
- get_config_path(): data/module/configs/<name>/（模块配置目录）
- get_data_path():   data/module/data/<name>/（模块自定义持久化数据目录，自动创建）

## 为什么这里用 `get_active()`

前四个函数是**插件作者的无参便捷 API**（插件里直接
`from app.plugin.api import get_config_path`，不该被迫先拿到容器），
所以必然需要一个「当前容器」入口。

它取自 `app.core.container` 而非 `app.bootstrap`：后者是装配根，从插件 API 反向
import 它会在 import 图上把 `modules/api` 与 `bootstrap` 焊成环——实测环闭合在
`modules/api → bootstrap → webui/app → webui/api/agent → ... → media/store →
modules → modules/api`。

`app.core.container.get_active()` 是全库唯一允许的服务定位，且方向向下（上层依赖
core）。写新代码请优先传参或走 FastAPI `Depends`（`app/webui/api/deps.py`）。
"""

from __future__ import annotations

from typing import Any


def get_modules() -> list[Any]:
    """获取已加载的模块对象列表（扁平，含全局与各 Bot 实例，排除子模块）。"""
    from app.core.container import get_active
    from app.plugin.registry import ModuleRegistry

    container = get_active()
    return container.get(ModuleRegistry).loaded()


def get_features(bot_id: Any = None) -> list[dict]:
    """获取全局能力注册表状态（含接管者信息）。"""
    from app.core.container import get_active
    from app.plugin.features import FeatureRegistry

    container = get_active()
    return container.get(FeatureRegistry).status(bot_id)


def get_config_path(module_name: str, create: bool = True) -> str:
    """获取模块配置目录（data/module/configs/<name>/）。"""
    from app.core.container import get_active
    from app.core.settings import Settings

    container = get_active()
    path = container.get(Settings).module_configs_dir / module_name
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return str(path)


def get_data_path(module_name: str, create: bool = True) -> str:
    """获取模块数据目录（data/module/data/<name>/），供插件持久化自定义数据。"""
    from app.core.container import get_active
    from app.core.settings import Settings

    container = get_active()
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

    **离线（账号未连接）时一律不注册**，返回 False：装配可以发生在建号时，但计时器只
    能在连上 QQ 之后才开始跑。模块应在 ``on_online()`` 里再调一次本函数（上线补注册），
    并在 ``on_offline()`` 里让框架注销（`ModuleRegistry.notify_offline` 会自动做）。

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
    # 开关即契约：禁用模块不得因 on_online/on_load 再被调用而重新挂上计时器
    if not bool(getattr(getattr(module, "authority", None), "enabled", True)):
        await scheduler.unload_module(module.module_name, module.bot_id)
        return False
    if not _runtime_online(module):
        return False
    if not module.config.get(enable_key, False):
        await scheduler.unload_module(module.module_name, module.bot_id)
        return False
    time_str = module.config.get(time_key, "00:00")
    key = f"{module.module_name}:{module.bot_id}:{key_suffix}"
    await scheduler.register(key, time_str, handler_factory())
    return True


def _runtime_online(module) -> bool:
    """该模块所属账号是否已连上（运行时不存在或离线都算离线）。"""
    runtime = getattr(module, "_runtime", None)
    if not callable(runtime):
        return False
    try:
        live = runtime()
    except Exception:  # noqa: BLE001 —— 取运行时失败按离线处理，绝不因此注册计时器
        return False
    return bool(getattr(live, "online", False))
