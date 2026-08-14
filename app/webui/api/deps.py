"""API 公共依赖。"""

from __future__ import annotations

from typing import Optional

from fastapi import Query, Request


def get_container(request: Request):
    return request.app.state.container


def get_service(request: Request, service_type):
    return request.app.state.container.get(service_type)


def parse_bot_id(bot_id: Optional[str] = Query(None)) -> Optional[int]:
    """把查询参数 bot_id 安全转为 int；前端可能传 null/'null'/'None'。"""
    if not bot_id or bot_id in ("null", "None", ""):
        return None
    try:
        return int(bot_id)
    except (TypeError, ValueError):
        return None
