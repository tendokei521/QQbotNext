"""装饰器风格的钩子声明。

- ``@module_hook``：模块流水线事件钩子（按事件类型注册处理函数）。
- ``@llm_hook``：LLM 流水线阶段钩子（pre_request / post_response / pre_send / post_send）。

装饰器本身只把元数据挂到函数对象上，真正的注册由 ``BaseModule.collect_hooks()``
和 ``ModuleRegistry`` 在模块加载时完成。
"""

from __future__ import annotations

from typing import Callable


def module_hook(event_type: str = "*", order: int = 100) -> Callable:
    """注册模块流水线钩子。

    Args:
        event_type: 订阅的事件类型，如 ``"message_group"`` / ``"message_private"`` / ``"*"``。
        order: 同一模块内多个钩子的执行顺序，越小越先执行。
    """

    def decorator(fn):
        metas = getattr(fn, "__module_hook_meta__", [])
        metas.append({"event_type": event_type, "order": order})
        setattr(fn, "__module_hook_meta__", metas)
        return fn

    return decorator


def llm_hook(stage: str, event_type: str = "*", order: int = 100) -> Callable:
    """注册 LLM 流水线钩子。

    Args:
        stage: LLM 流水线阶段，取值：
            - ``pre_request``：LLM 请求前，可暂停/防抖/合并；
            - ``post_response``：LLM 返回后，可拆分/清洗/改写；
            - ``pre_send``：每条消息发送前；
            - ``post_send``：每条消息发送后。
        event_type: 只对指定事件类型生效，``"*"`` 表示全部。
        order: 同一阶段内多个钩子的执行顺序，越小越先执行。
    """

    def decorator(fn):
        metas = getattr(fn, "__llm_hook_meta__", [])
        metas.append({"stage": stage, "event_type": event_type, "order": order})
        setattr(fn, "__llm_hook_meta__", metas)
        return fn

    return decorator
