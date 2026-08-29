"""会话历史管理 API：本地会话数据查看 / 编辑 / 导出。

与 Provider 预设同级，WebUI 顶层「会话数据」面板使用。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from app.services.session_history_service import SessionHistoryService
from app.webui.api.deps import parse_bot_id

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _service(bot_id: int | None) -> SessionHistoryService:
    if bot_id is None:
        raise ValueError("缺少 bot_id")
    return SessionHistoryService(bot_id)


def _err(status: int, message: str):
    return JSONResponse(status_code=status, content={"status": "error", "message": message})


def _ok(message: str, **extra):
    return JSONResponse(content={"status": "success", "message": message, **extra})


@router.get("")
async def list_sessions(request: Request, bot_id: int | None = Depends(parse_bot_id)):
    try:
        sessions = _service(bot_id).list_sessions()
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        return _err(500, f"读取会话列表失败: {e}")
    return JSONResponse(content={"ok": True, "sessions": sessions})


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request, bot_id: int | None = Depends(parse_bot_id)):
    try:
        session = _service(bot_id).get_session(session_id)
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        return _err(500, f"读取会话失败: {e}")
    if session is None:
        return _err(404, f"会话不存在: {session_id}")
    return JSONResponse(content={"ok": True, "session": session})


@router.get("/{session_id}/conversations/{task_id}")
async def get_conversation(
    session_id: str,
    task_id: str,
    request: Request,
    bot_id: int | None = Depends(parse_bot_id),
):
    try:
        conversation = _service(bot_id).get_conversation(task_id)
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        return _err(500, f"读取对话失败: {e}")
    if conversation is None:
        return _err(404, f"对话不存在: {task_id}")
    return JSONResponse(content={"ok": True, "conversation": conversation})


@router.put("/{session_id}/conversations/{task_id}/rename")
async def rename_conversation(
    session_id: str,
    task_id: str,
    request: Request,
    bot_id: int | None = Depends(parse_bot_id),
):
    try:
        body = await request.json()
    except Exception:
        body = {}
    title = str((body or {}).get("title", "") or "").strip()
    if not title:
        return _err(400, "标题不能为空")
    try:
        conversation = _service(bot_id).rename_conversation(session_id, task_id, title)
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        return _err(500, f"重命名失败: {e}")
    return _ok("对话已重命名", conversation=conversation)


@router.delete("/{session_id}/conversations/{task_id}/messages/{index}")
async def delete_message(
    session_id: str,
    task_id: str,
    index: int,
    request: Request,
    bot_id: int | None = Depends(parse_bot_id),
):
    try:
        conversation = _service(bot_id).delete_message(session_id, task_id, index)
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        return _err(500, f"删除消息失败: {e}")
    return _ok("消息已删除", conversation=conversation)


@router.delete("/{session_id}/conversations/{task_id}")
async def delete_conversation(
    session_id: str,
    task_id: str,
    request: Request,
    bot_id: int | None = Depends(parse_bot_id),
):
    try:
        ok = _service(bot_id).delete_conversation(session_id, task_id)
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        return _err(500, f"删除对话失败: {e}")
    if not ok:
        return _err(404, f"对话不存在: {task_id}")
    return _ok("对话已删除")


@router.get("/{session_id}/conversations/{task_id}/export")
async def export_conversation(
    session_id: str,
    task_id: str,
    request: Request,
    bot_id: int | None = Depends(parse_bot_id),
    format: str = Query(default="text", pattern="^(text|json)$"),
):
    try:
        service = _service(bot_id)
        if format == "json":
            data = service.export_json(task_id)
            if data is None:
                return _err(404, f"对话不存在: {task_id}")
            filename = f"{task_id}.json"
            return Response(
                content=json.dumps(data, ensure_ascii=False, indent=2),
                media_type="application/json; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        text = service.export_text(task_id)
        if text is None:
            return _err(404, f"对话不存在: {task_id}")
        filename = f"{task_id}.txt"
        return PlainTextResponse(
            text,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        return _err(500, f"导出失败: {e}")
