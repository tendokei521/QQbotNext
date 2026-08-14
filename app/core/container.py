"""轻量依赖注入容器。

不做运行时魔法：显式注册「工厂」或「单例实例」，显式解析。装配集中在
app/bootstrap.py，循环依赖在装配期即被暴露。
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, TypeVar, cast

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


def as_factory(type_: type, deps: list[type]) -> Callable[[], Any]:
    """构造一个由容器注入依赖的工厂。"""

    def factory() -> Any:
        instance = type_.__new__(type_)
        return instance

    return factory


def provide(container: Container, type_: type) -> Any:
    return container.get(type_)


def service(type_: type) -> Any:
    """供 bootstrap 之外访问容器单例的桥接函数（仅限已解析注册的类型）。"""
    from app.bootstrap import get_container

    return cast(type_, get_container().get(type_))
