> 本文只负责：说明 `app/webui/` 放什么、不放什么。前端源码在仓库根 `dashboard/`，见 `docs/dashboard-frontend-architecture.md`。

# `app/webui/` —— WebUI 后端

**一句话：HTTP / WebSocket 适配层，只做「解析请求 → 调服务 → 序列化返回」。**

## 包含

| 文件 / 目录 | 职责 |
|---|---|
| `app.py` | FastAPI 应用装配、静态资源挂载、`/legacy` 回退 |
| `api/deps.py` | FastAPI 依赖：`get_container` / `get_service` / `parse_bot_id` |
| `api/*.py` | 各业务域路由（agent / bots / modules / sessions / logs / media / …） |
| `schemas.py` | 请求 / 响应模型 |
| `ws.py` | WebSocket 推送（日志、状态） |
| `templates/` `static/` | **旧版前端**（P6 删除，见 `STYLE.md §12.4`） |

## 边界（重要）

- **不得有业务逻辑**。判断「该不该发这条消息」属于 `app/agent/`；判断「这次请求
  合不合法」属于 `app/plugin/authority.py`。路由只做搬运。
- **不得 import `app.bootstrap`**（`bootstrap_imports=0` 守着）。容器从
  `request.app.state.container` 拿，见 `deps.py`。
- 现状还有 13 处直接 `import app.agent.*`。P4 计划加一层 `app/services/` 门面后清零，
  这样 WebUI 就不再被 Agent 的内部实现焊死（`test_architecture_boundaries.py` 记账）。

## 加一个接口的顺序

1. 先确认「这个动作」是否已有 `app/services/*.py` 里的函数；没有就先加服务；
2. 在 `api/<域>.py` 加路由，用 `Depends(get_service)` 注入，不要自己 `container.get(type)`；
3. 在 `schemas.py` 定请求/响应模型；
4. 补 `tests/` 用例（HTTP 层用 `TestClient`，纯逻辑测服务层）。
