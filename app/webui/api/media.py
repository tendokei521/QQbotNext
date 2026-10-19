"""本地图片仓 API：看 / 清单 / 删 / 清理。

会话里的图片现在是一个**指向**（``data.media.sha``），字节落在
``data/llm/<bot_id>/media/``（每账号一个目录）。模型请求把字节内联成 base64 送出去，
前端则通过这里读同一份字节——两边指向同一个 sha，所以界面上"看 / 换 / 删"动的是
模型下一轮会用的那份内容。

设计约束：

- **强缓存**：文件名就是内容哈希，同一 sha 的字节永不改变（``immutable``），
  浏览器可以缓存一年；"换图"是换 sha，不会命中旧缓存；
- **只按 sha 取文件**：``sha`` 必须是 16 位十六进制，且最终路径必须落在媒体目录内
  （挡目录穿越）；扩展名只用于给浏览器一个像样的 Content-Type；
- **删除必须先删引用**：仍被会话引用的图拒绝删除，并把"谁在用"列出来。
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse

from app.llm import image_media
from app.services.session_history_service import SessionHistoryService
from app.webui.api.deps import parse_bot_id

router = APIRouter(prefix="/media", tags=["media"])

#: 内容寻址的文件永不改变，浏览器可放心长期缓存
_IMMUTABLE = "public, max-age=31536000, immutable"


def _service(bot_id: int | None) -> SessionHistoryService:
    if bot_id is None:
        raise ValueError("缺少 bot_id")
    return SessionHistoryService(bot_id)


def _err(status: int, message: str, **extra):
    return JSONResponse(status_code=status, content={"status": "error", "message": message, **extra})


def _ok(message: str, **extra):
    return JSONResponse(content={"status": "success", "message": message, **extra})


@router.get("")
async def list_media(request: Request, bot_id: int | None = Depends(parse_bot_id)):
    """媒体仓概况：张数 / 占用 / 被引用数 / 可清理数 / 文件清单。"""
    try:
        data = _service(bot_id).media_stats()
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        return _err(500, f"读取媒体清单失败: {e}")
    return JSONResponse(content={"ok": True, **data})


@router.post("/purge")
async def purge_media(request: Request, bot_id: int | None = Depends(parse_bot_id)):
    """清理所有"没有会话引用"的图片（被引用的永不删）。"""
    try:
        result = _service(bot_id).purge_media()
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        return _err(500, f"清理失败: {e}")
    return _ok(
        f"已清理 {result.get('removed', 0)} 张图片，"
        f"保留被引用 {result.get('kept_referenced', 0)} 张",
        **result,
    )


@router.post("/gc")
async def gc_media(request: Request, bot_id: int | None = Depends(parse_bot_id)):
    """按 TTL + 容量上限清理（同样只碰无人引用的文件）。"""
    try:
        result = _service(bot_id).gc_media()
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        return _err(500, f"清理失败: {e}")
    return _ok(f"已清理 {result.get('removed', 0)} 张过期图片", **result)


@router.delete("/{sha}")
async def delete_media(sha: str, request: Request, bot_id: int | None = Depends(parse_bot_id)):
    """删除一张本地图片；**仍被引用时拒绝**并给出引用位置。"""
    try:
        result = _service(bot_id).delete_media(sha)
    except ValueError as e:
        return _err(400, str(e))
    except Exception as e:
        return _err(500, f"删除图片失败: {e}")
    if not result.get("ok"):
        references = result.get("references") or []
        return _err(409 if references else 404, str(result.get("reason", "删除失败")), references=references)
    return _ok("图片已删除", freed=result.get("freed", 0))


@router.get("/{name}")
async def read_media(name: str, request: Request, bot_id: int | None = Depends(parse_bot_id)):
    """读取一张本地图片（``<sha>`` 或 ``<sha>.<ext>``，扩展名只影响 Content-Type）。"""
    if bot_id is None:
        return _err(400, "缺少 bot_id")
    sha, _, ext = str(name or "").partition(".")
    sha = sha.strip().lower()
    if not image_media.SHA_RE.match(sha):
        return _err(400, "非法的图片标识")
    path = image_media.path_of(bot_id, sha)
    if not path:
        return _err(404, "图片不存在（可能已被清理）")
    directory = os.path.abspath(image_media.media_dir(bot_id))
    resolved = os.path.abspath(path)
    if not resolved.startswith(directory + os.sep):
        return _err(400, "非法的图片路径")
    media_type = image_media.MIME_BY_EXT.get(ext.lower(), "") or image_media.mime_from_name(resolved)
    return FileResponse(
        resolved,
        media_type=media_type,
        headers={
            "Cache-Control": _IMMUTABLE,
            # 抖音/微信内置浏览器会猜类型，明确关掉嗅探，避免"图被当页面"下载
            "X-Content-Type-Options": "nosniff",
        },
        filename=os.path.basename(resolved),
    )


__all__ = ["router"]
