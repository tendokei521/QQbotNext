"""轻量依赖注入容器。

不做运行时魔法：显式注册「工厂」或「单例实例」，显式解析。装配集中在
`app/bootstrap.py`，循环依赖在装配期即被暴露。

## 本模块的边界（重要）

**不得** import `app.bootstrap` 或任何上层模块。依赖方向永远是「上层依赖 core」，
core 不认识任何业务。历史上这里曾有 `service(type_)` 桥接函数，内部
`from app.bootstrap import get_container`，带来两个后果：

1. 它让「服务定位器」成了合法写法，全库出现 71 处 `get_container()` 直调
   （65 处在 `app/webui/api/*`），把 WebUI 焊死在 Agent 内部实现上；
2. 它在 import 图上制造了 `core/container → bootstrap` 的反向边，
   把 DI 容器本身拖进了 24 文件 / 12,112 行的强连通块。

该函数与另外两个从未被引用的辅助函数（`as_factory` / `provide`）已删除。
依赖请走**构造函数注入**或 FastAPI `Depends`（`app/webui/api/deps.py`）。
见 `STYLE.md §8` 与 `STYLE.md §12.2`。

## 唯一允许的服务定位：`get_active()`

模块（插件）的便捷 API `app/modules/api.py` 里有 `get_config_path(name)` 这类
**无参函数**——插件作者不该被迫先拿到容器。它们需要一个「当前容器」入口。

放在本模块而不是 `app/bootstrap.py`，是为了让这条依赖**向下**而不是向上：
`app.modules.api → app.core.container` 是合法方向（上层依赖 core），
而原来的 `app.modules.api → app.bootstrap` 是反向边，它把装配根拉进了强连通块
（实测环闭合在 `modules/api → bootstrap → webui/app → webui/api/agent → ...`）。

`get_active()` 是**全库唯一**允许的服务定位写法，且只有模块便捷 API 用它；
写新代码请优先传参或 `Depends`。装配根在建立容器时调用 `set_active()`。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


class Container:
    """极简 DI 容器。

    - register(type, factory_or_instance)：工厂为可调用对象，惰性求值并缓存为单例；
      传入实例则直接复用。
    - get(type)：解析并缓存结果。
    - inject(fn)：自动把 fn 中标注了容器内类型的参数注入后调用。
    """

    def __init__(self) -> None:
        self._factories: dict[type, Callable[[], Any]] = {}
        self._instances: dict[type, Any] = {}
        self._resolving: set[type] = set()

    def register(self, type_: type, factory_or_instance: Any = None) -> None:
        if factory_or_instance is None:
            # 以类型本身作为工厂（零参构造或由容器注入构造）
            factory = type_
        elif isinstance(factory_or_instance, type):
            factory = factory_or_instance
        else:
            # 传入的是实例
            self._instances[type_] = factory_or_instance
            return
        self._factories[type_] = factory

    def register_factory(self, type_: type, factory: Callable[[], Any]) -> None:
        self._factories[type_] = factory

    def get(self, type_: type) -> Any:
        if type_ in self._instances:
            return self._instances[type_]
        if type_ not in self._factories:
            raise KeyError(f"容器中未注册类型: {type_}")
        if type_ in self._resolving:
            raise RuntimeError(f"检测到循环依赖: {type_}")
        self._resolving.add(type_)
        try:
            instance = self._factories[type_]()
        finally:
            self._resolving.discard(type_)
        self._instances[type_] = instance
        return instance

    def has(self, type_: type) -> bool:
        return type_ in self._instances or type_ in self._factories

    def inject(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """调用 fn，自动注入标注为容器类型的参数。"""
        params = inspect.signature(fn).parameters
        resolved: dict[str, Any] = {}
        for name, param in params.items():
            if name in kwargs:
                continue
            annotation = param.annotation
            if annotation is inspect.Parameter.empty:
                continue
            # 跳过 typing 泛型（如 list[str] 无法直接作为键）
            if isinstance(annotation, type) and self.has(annotation):
                resolved[name] = self.get(annotation)
        resolved.update(kwargs)
        return fn(*args, **resolved)


# --------------------------------------------------------------------------- #
# 活动容器（唯一允许的服务定位，见模块文档）
# --------------------------------------------------------------------------- #
_active: Container | None = None


def set_active(container: Container | None) -> None:
    """由装配根（`app/bootstrap.py`）在容器建好/替换时调用。"""
    global _active
    _active = container


def get_active() -> Container:
    """取当前进程的容器。

    只给「插件无参便捷 API」用（`app/modules/api.py`）。其余代码请走构造函数注入
    或 FastAPI `Depends`——服务定位会把调用方焊死在具体实现上，也让依赖图失真。
    """
    if _active is None:
        raise RuntimeError("容器尚未初始化：请先调用 app.bootstrap.run()")
    return _active
