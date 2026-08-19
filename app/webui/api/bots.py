"""Bot 连接与账号配置 API。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.webui.api.deps import get_container

router = APIRouter(tags=["bots"])


def _ok(message: str, **extra):
    return JSONResponse(content={"status": "success", "message": message, **extra})


def _err(status: int, message: str):
    return JSONResponse(status_code=status, content={"status": "error", "message": message})


@router.get("/bots")
async def api_bots(request: Request):
    from app.services.bot_service import BotService

    container = get_container(request)
    bot_service = container.get(BotService)
    return JSONResponse(content={"bots": bot_service.get_bots_data()})


@router.post("/bots/{index}/connect")
async def connect_bot(index: int, request: Request):
    from app.services.bot_service import BotService
    from app.infrastructure.config.config_service import ConfigService

    container = get_container(request)
    bot_service = container.get(BotService)
    config_service = container.get(ConfigService)

    bots_config = config_service.get_bots()
    if index < 0 or index >= len(bots_config):
        return _err(404, f"Bot at index {index} not in config")
    bot_cfg = bots_config[index]
    if index not in bot_service.gateway.connections:
        # 显式传 index，避免 add_bot 缺省索引（max+1）与配置索引脱节
        await bot_service.gateway.add_bot(bot_cfg.get("ws_url", ""), bot_cfg.get("owner_id"),
                                          bot_cfg.get("auto_connect", False), index=index)
    else:
        await bot_service.gateway.readd_bot(bot_cfg.get("ws_url", ""), bot_cfg.get("owner_id"), index)

    success = await bot_service.connect(index)
    if success:
        bot_id = await bot_service.get_bot_id(index) or index
        return _ok(f"Bot {bot_id} connected")
    return _err(500, f"Bot at index {index} connection failed")


@router.post("/bots/{index}/disconnect")
async def disconnect_bot(index: int, request: Request):
    from app.services.bot_service import BotService

    container = get_container(request)
    await container.get(BotService).disconnect(index)
    return _ok(f"Bot at index {index} disconnected")


@router.post("/bots/{index}/reconnect")
async def reconnect_bot(index: int, request: Request):
    from app.services.bot_service import BotService

    container = get_container(request)
    bot_service = container.get(BotService)
    success = await bot_service.reconnect(index)
    if success:
        bot_id = await bot_service.get_bot_id(index) or index
        return _ok(f"Bot {bot_id} reconnected")
    return _err(500, f"Bot at index {index} reconnection failed")


@router.get("/bots/config")
async def api_bots_config(request: Request):
    from app.infrastructure.config.config_service import ConfigService

    container = get_container(request)
    # 对外打码 access_token
    return JSONResponse(content={"bots": container.get(ConfigService).get_bots_public()})


@router.post("/bots/config/save")
async def api_bots_config_save(request: Request):
    from app.infrastructure.config.config_service import ConfigService

    container = get_container(request)
    try:
        data = await request.json()
    except Exception:
        data = {}
    await container.get(ConfigService).save_bots(data.get("bots", []))
    return _ok("配置已保存")


@router.post("/bots/config/add")
async def api_bots_config_add(request: Request):
    from app.services.bot_service import BotService

    container = get_container(request)
    # Reuse BotService.add_bot: persist the config AND register a gateway connection
    # (status "disconnected") at once, so /api/bots (status list) includes the new
    # account immediately and the cards / account selector refresh right away.
    index = await container.get(BotService).add_bot("", None, False)
    return _ok("已添加新账号配置", index=index)


@router.post("/bots/config/delete/{index}")
async def api_bots_config_delete(index: int, request: Request):
    from app.services.bot_service import BotService
    from app.infrastructure.config.config_service import ConfigService

    container = get_container(request)
    bot_service = container.get(BotService)
    cfg_service = container.get(ConfigService)

    await bot_service.gateway.del_bot(index)
    ok = await cfg_service.delete_bot(index)
    if not ok:
        return _err(404, f"索引 {index} 不存在")
    # 配置列表已压缩索引，立即对齐 gateway 连接映射，避免前端短暂错位
    await bot_service.gateway.reconcile()
    return _ok("已删除账号配置")


@router.get("/bots/groups")
async def get_all_bots_groups(request: Request):
    from app.services.bot_service import BotService

    container = get_container(request)
    return JSONResponse(content={"bots_groups": container.get(BotService).get_bots_groups()})


@router.get("/bots/{index}")
async def api_bot(index: int, request: Request):
    from app.services.bot_service import BotService

    container = get_container(request)
    info = container.get(BotService).gateway.get_bot_info_by_index(index)
    if info:
        return JSONResponse(content=info)
    return _err(404, f"Bot at index {index} not found")
