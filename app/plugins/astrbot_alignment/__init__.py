"""AstrBot 组装对齐插件（`app/plugins/astrbot_alignment`）。

在**单个账号**下接管消息组装，把 `1/AstrBot-master`（4.27.2）的 system prompt 与
请求形态 1:1 复刻过来，用于迁移对账。

- `main.py`：模块声明（`supersedes = ("assembly",)`）与组装前素材预取
- `assembler.py`：组装实现（system prompt 段落顺序 / 历史 / 用户消息块）
- `astrbot_prompts.py`：AstrBot 原文常量（逐字，含来源行号）
- `config_schema.py`：WebUI 配置表单

对齐口径与已知差异见同目录 `README.md`。
"""

__all__: list[str] = []
