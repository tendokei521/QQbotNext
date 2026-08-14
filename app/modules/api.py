"""插件开发 API：暴露给业务模块使用的框架能力。

- get_modules():     获取已加载模块对象列表
- get_config_path(): module/configs/<name>/（模块配置目录）
- get_data_path():   module/data/<name>/（模块自定义持久化数据目录，自动创建）
"""

from __future__ import annotations

from typing import Any, List


def get_modules() -> List[Any]:
    """获取已加载的模块对象列表（扁平，含全局与各 Bot 实例，排除子模块）。"""
    from app.bootstrap import get_container
    from app.modules.registry import ModuleRegistry

    container = get_container()
    return container.get(ModuleRegistry).loaded()


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
