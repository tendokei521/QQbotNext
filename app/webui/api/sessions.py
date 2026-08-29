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


@router.put("/{session_id}/conversations/{task_id}/messages/{index}")
async def edit_message(
    session_id: str,
    task_id: str,
    index: int,
    request: Request,
    bot_id: int | None = Depends(parse_bot_id),
):
    try:
        body = await request.json()
    except Exception:
        body = {}
    content = str((body or {}).get("content", "") or "")
    try:
        conversation = _service(bot_id).edit_message(session_id, task_id, index, content)
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        return _err(500, f"编辑消息失败: {e}")
    return _ok("消息已更新", conversation=conversation)


@router.post("/{session_id}/conversations/{task_id}/messages")
async def add_message(
    session_id: str,
    task_id: str,
    request: Request,
    bot_id: int | None = Depends(parse_bot_id),
):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        conversation = _service(bot_id).add_message(session_id, task_id, body or {})
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        return _err(500, f"添加消息失败: {e}")
    return _ok("消息已添加", conversation=conversation)


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


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    request: Request,
    bot_id: int | None = Depends(parse_bot_id),
):
    try:
        deleted = _service(bot_id).delete_session(session_id)
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        return _err(500, f"删除会话失败: {e}")
    if deleted == 0:
        return _err(404, f"会话不存在或没有归档: {session_id}")
    return _ok(f"会话已删除（{deleted} 个对话）", deleted=deleted)


@router.get("/{session_id}/export")
async def export_session(
    session_id: str,
    request: Request,
    bot_id: int | None = Depends(parse_bot_id),
):
    try:
        data = _service(bot_id).export_session_json(session_id)
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        return _err(500, f"导出会话失败: {e}")
    if data is None:
        return _err(404, f"会话不存在: {session_id}")
    filename = f"{session_id}.session.json"
    return Response(
        content=json.dumps(data, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/bulk-delete")
async def bulk_delete_sessions(request: Request, bot_id: int | None = Depends(parse_bot_id)):
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    session_ids = [str(s).strip() for s in (body.get("session_ids") or []) if str(s).strip()]
    task_ids = [str(t).strip() for t in (body.get("task_ids") or []) if str(t).strip()]
    try:
        service = _service(bot_id)
        if session_ids:
            result = service.bulk_delete_sessions(session_ids)
        elif task_ids:
            session_id = str(body.get("session_id", "") or "").strip()
            if not session_id:
                return _err(400, "缺少 session_id 或 session_ids")
            result = service.bulk_delete_conversations(session_id, task_ids)
        else:
            return _err(400, "缺少要删除的会话或对话")
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        return _err(500, f"批量删除失败: {e}")
    return _ok("批量删除完成", **result)


@router.post("/restore")
async def restore_sessions(request: Request, bot_id: int | None = Depends(parse_bot_id)):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        result = _service(bot_id).restore_session(body or {})
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        return _err(500, f"恢复失败: {e}")
    return _ok("恢复完成", **result)


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
