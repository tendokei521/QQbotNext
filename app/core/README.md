> 本文只负责：说明 `app/core/` 放什么、不放什么。上层目录总览见 `docs/ARCHITECTURE.md`。

# `app/core/` —— 内核

**一句话：框架的「水电煤」，不含任何业务。**

## 包含

| 文件 | 职责 |
|---|---|
| `settings.py` | pydantic-settings 配置（`.env`，变量名=字段名大写） |
| `container.py` | 轻量 DI 容器 + `get_active()`（全库唯一允许的服务定位） |
| `logger.py` | 日志（6h 轮转 / debug+warn+errors+user / 简洁+原始双视图） |
| `task_manager.py` | 统一后台任务管理（可追踪、级联取消） |
| `event_bus.py` | 类型化事件总线（框架级发布订阅） |

## 边界（重要）

- **不得** import 任何其它 `app.*` 子包。依赖方向永远是「上层 → core」。
- **不得**出现业务概念：不认「消息、群、Bot、插件、LLM」。出现即说明它该搬家。
- `container.get_active()` 是**唯一**允许的服务定位入口，且只给「插件无参便捷 API」
  （`app/plugin/api.py`）用。其余代码一律构造函数注入或 FastAPI `Depends`。

## 想改这里之前

- 加配置项 → 改 `settings.py`，同时看 `app/agent/config_schema.py` 是否也要暴露给 WebUI。
- 加日志分类 → 改 `logger.py`，同时确认 `app/webui/api/logs.py` 的读取路径。
