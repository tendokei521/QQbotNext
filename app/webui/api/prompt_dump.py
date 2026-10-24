"""请求 messages 导出查询 API（迁移对账用）。

开启 ``prompt_dump_enable`` 后，每轮最终请求会落到
``data/llm/<account_id>/prompt_dump.jsonl``；这里提供只读查询与清理。

账号参数同时接受 ``?account_id=`` 与旧名 ``?bot_id=``（第三方脚本/诊断命令里
还在用后者）。缺省两者都没传时返回 400 而不是 422，便于排查到底是"参数名写错"
还是"账号不存在"。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.agent.assembly import dump as prompt_dump
from app.webui.api.deps import parse_account_id

router = APIRouter(prefix="/agent/prompt-dumps", tags=["agent"])


@router.get("")
async def list_dumps(
    account_id: str | None = Depends(parse_account_id),
    session_id: str = "",
    limit: int = 10,
):
    """读取最近几轮请求（旧 → 新）；session_id 非空时只看该会话。"""
    if not account_id:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": "缺少 account_id（兼容别名 bot_id）"},
        )
    entries = prompt_dump.load_recent(account_id, session_id=session_id or None, limit=limit)
    return JSONResponse(content={
        "ok": True,
        "account_id": account_id,
        "session_id": session_id,
        "count": len(entries),
        "entries": entries,
        "path": prompt_dump.dump_path(account_id),
    })


@router.delete("")
async def clear_dumps(account_id: str | None = Depends(parse_account_id)):
    if not account_id:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "message": "缺少 account_id（兼容别名 bot_id）"},
        )
    removed = prompt_dump.clear(account_id)
    return JSONResponse(content={"ok": True, "removed": removed, "account_id": account_id})
