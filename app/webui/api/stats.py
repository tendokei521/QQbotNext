"""统计 API：查询 / 下拉选项 / 清理。

三个端点，参数都是**封闭白名单**（见 docs/DATA_STATS_DESIGN.md §4）：

- `GET  /api/stats/query`   按时间桶 + 堆叠维度聚合（含空桶补零、top-N、summary）
- `GET  /api/stats/facets`  下拉选项（只返回该范围内真的出现过的值）
- `POST /api/stats/purge`   手动清理（数据永久保留，由用户决定删）
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.agent.usage import query as stat_query
from app.agent.usage.recorder import StatRecorder

router = APIRouter(prefix="/stats", tags=["stats"])


def _recorder(request: Request) -> StatRecorder | None:
    try:
        return request.app.state.container.get(StatRecorder)
    except Exception:  # noqa: BLE001 —— 未装配统计时接口优雅降级
        from app.agent.usage.recorder import get_recorder

        return get_recorder()


def _filters(
    bot_id: str | None,
    source: str | None,
    api: str | None,
    model: str | None,
    session_id: str | None,
    group_id: str | None,
    user_id: str | None,
    event_type: str | None,
    outcome: str | None,
) -> dict[str, Any]:
    return {
        "bot_id": bot_id,
        "source": source,
        "api": api,
        "model": model,
        "session_id": session_id,
        "group_id": group_id,
        "user_id": user_id,
        "event_type": event_type,
        "outcome": outcome,
    }


@router.get("/query")
async def query_stats(
    request: Request,
    kind: str = Query("llm", description="llm | msg_in | msg_out | all"),
    metric: str = Query("tokens", description="tokens|input|output|cache_hit|reasoning|requests|messages|triggered|bytes|events"),
    granularity: str = Query("day", description="minute | hour | day | week | month"),
    breakdown: str = Query("model", description="none|model|api|source|bot|session|event|outcome"),
    from_ts: int | None = Query(None, description="起始时间（UTC 秒，含）"),
    to_ts: int | None = Query(None, description="结束时间（UTC 秒，含当天请传次日 0 点）"),
    bot_id: str | None = None,
    source: str | None = None,
    api: str | None = None,
    model: str | None = None,
    session_id: str | None = None,
    group_id: str | None = None,
    user_id: str | None = None,
    event_type: str | None = None,
    outcome: str | None = None,
    ok_only: bool = False,
    include_estimated: bool = True,
):
    recorder = _recorder(request)
    db = getattr(recorder, "_db", None)
    if db is None:
        return JSONResponse(status_code=503, content={"status": "error", "message": "统计未启用"})

    now = int(time.time())
    to_ts = int(to_ts or now)
    from_ts = int(from_ts or (to_ts - 30 * 86400))
    if from_ts >= to_ts:
        return JSONResponse(status_code=400, content={"status": "error", "message": "from_ts 必须小于 to_ts"})

    try:
        data = await stat_query.query(
            db,
            kind=kind,
            metric=metric,
            granularity=granularity,
            breakdown=breakdown,
            from_ts=from_ts,
            to_ts=to_ts,
            filters=_filters(
                bot_id, source, api, model, session_id, group_id, user_id, event_type, outcome
            ),
            ok_only=ok_only,
            include_estimated=include_estimated,
        )
    except Exception as e:  # noqa: BLE001 —— 查询失败返回 500，附原因便于排查
        return JSONResponse(status_code=500, content={"status": "error", "message": f"查询失败: {e}"})
    return JSONResponse(content={"ok": True, **data})


@router.get("/facets")
async def list_facets(
    request: Request,
    kind: str = "llm",
    from_ts: int | None = None,
    to_ts: int | None = None,
    api: str | None = None,
    source: str | None = None,
    model: str | None = None,
    bot_id: str | None = None,
):
    recorder = _recorder(request)
    db = getattr(recorder, "_db", None)
    if db is None:
        return JSONResponse(status_code=503, content={"status": "error", "message": "统计未启用"})

    now = int(time.time())
    to_ts = int(to_ts or now)
    from_ts = int(from_ts or (to_ts - 30 * 86400))
    try:
        data = await stat_query.facets(
            db,
            kind=kind,
            from_ts=from_ts,
            to_ts=to_ts,
            filters={"api": api, "source": source, "model": model, "bot_id": bot_id},
        )
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"status": "error", "message": f"读取筛选项失败: {e}"})
    return JSONResponse(content={"ok": True, "facets": data})


@router.post("/purge")
async def purge_stats(
    request: Request,
    before_ts: int | None = None,
    bot_id: str | None = None,
):
    """手动清理历史统计数据（默认永久保留）。"""
    recorder = _recorder(request)
    if recorder is None:
        return JSONResponse(status_code=503, content={"status": "error", "message": "统计未启用"})
    if before_ts is None and not bot_id:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "必须指定 before_ts 或 bot_id，拒绝清空全表"},
        )
    try:
        removed = await recorder.purge(before_ts=before_ts, bot_id=bot_id)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"status": "error", "message": f"清理失败: {e}"})
    return JSONResponse(content={"ok": True, "removed": removed})
