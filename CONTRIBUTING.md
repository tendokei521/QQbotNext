# 贡献指南

感谢你愿意改进 QQBot Next。开始之前，请先读 [AGENTS.md](AGENTS.md)：本仓库对提交格式、测试策略与前端验证有硬性要求，
比"写多少代码"更重要。

## 环境

```bash
python start.py --dev      # 建 venv 并安装运行 + 开发依赖
# 或手动
pip install -e ".[dev]"
```

## 改哪里

| 想改的东西 | 先读 |
|---|---|
| 框架行为 / 分层边界 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 插件开发 | [docs/MODULE_DEV.md](docs/MODULE_DEV.md)、[docs/PLUGIN_STYLE.md](docs/PLUGIN_STYLE.md) |
| Agent 配置键 | [docs/AGENT_CONFIG.md](docs/AGENT_CONFIG.md) |
| 前端页面 / 文案 | [docs/dashboard-frontend-architecture.md](docs/dashboard-frontend-architecture.md)、[docs/UI_COPY.md](docs/UI_COPY.md) |
| 测试怎么写 | [docs/TEST_POLICY.md](docs/TEST_POLICY.md) |

## 提交

- 使用 Conventional Commits：`feat(scope): …` / `fix(scope): …` / `docs: …` / `build: …`；
- 一步一提交，一个提交只做一件事，不把无关改动混进来；
- 提交信息说明"做了什么、为什么"，不写"update"之类无信息量的描述。

## 验证

按 [AGENTS.md](AGENTS.md) 的"验证规则"分档执行，不要无脑全量测试：

```bash
venv\Scripts\python.exe -m pytest -q        # 相关测试文件即可；发布前才跑全量
venv\Scripts\python.exe -m ruff check .
```

- 新增 / 修改功能请补**契约测试**（对外行为、边界、回归），见 [docs/TEST_POLICY.md](docs/TEST_POLICY.md)；
- 改前端必须按 [docs/FRONTEND_VERIFY.md](docs/FRONTEND_VERIFY.md) 重新构建、截图并量布局；
- `dist/` 是进仓库的产物，源码与产物分两个提交。

## Pull Request

1. 说明动机、影响范围与验证方式（跑了什么、没跑什么）；
2. 关联相关 Issue；
3. 确认 CI 通过（后端 py3.10 / py3.12 / Windows、依赖元数据、Dashboard 构建）。
