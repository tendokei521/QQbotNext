"""WebUI 需要、但属于 agent 层的只读元数据门面。

WebUI 是 HTTP 适配层，只该认识 `application/` 的门面（见
`tests/architecture/test_architecture_boundaries.py` 的 webui→agent 规则）。
套装校验需要 Agent 配置 schema 来拒绝未知键，因此在这里开一个只读函数，
而不是让 `app/webui/api/bundles.py` 直接 import `app.agent.*`。
"""

from __future__ import annotations


def agent_config_schema() -> dict:
    """返回 Agent 配置 schema（键 → 字段定义）；只读，调用方不要修改。"""
    from app.agent.config_schema import SCHEMA

    return SCHEMA
