"""Bot 连接与账号配置 API。

依赖一律通过 `Depends(get_bot_service)` / `Depends(get_config_service)` 注入——
读一个路由就能看出它依赖谁，不必追到装配根。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.webui.api.deps import get_bot_service, get_config_service

router = APIRouter(tags=["bots"])


def _ok(message: str, **extra):
    return JSONResponse(content={"status": "success", "message": message, **extra})


def _err(status: int, message: str):
    return JSONResponse(status_code=status, content={"status": "error", "message": message})


@router.get("/bots")
async def api_bots(bot_service=Depends(get_bot_service)):
    return JSONResponse(content={"bots": bot_service.get_bots_data()})


@router.post("/bots/{index}/connect")
async def connect_bot(
    index: int,
    bot_service=Depends(get_bot_service),
    config_service=Depends(get_config_service),
):
    bots_config = config_service.get_bots()
    if index < 0 or index >= len(bots_config):
        return _err(404, f"Bot at index {index} not in config")
    bot_cfg = bots_config[index]
    if index not in bot_service.gateway.connections:
        # 显式传 index，避免 add_bot 缺省索引（max+1）与配置索引脱节
        await bot_service.gateway.add_bot(bot_cfg.get("ws_url", ""), None,
                                          bot_cfg.get("auto_connect", False), index=index)
    else:
        await bot_service.gateway.readd_bot(bot_cfg.get("ws_url", ""), None, index)

    success = await bot_service.connect(index)
    if success:
        bot_id = await bot_service.get_bot_id(index) or index
        return _ok(f"Bot {bot_id} connected")
    return _err(500, f"Bot at index {index} connection failed")


@router.post("/bots/{index}/disconnect")
async def disconnect_bot(index: int, bot_service=Depends(get_bot_service)):
    await bot_service.disconnect(index)
    return _ok(f"Bot at index {index} disconnected")


@router.post("/bots/{index}/reconnect")
async def reconnect_bot(index: int, bot_service=Depends(get_bot_service)):
    success = await bot_service.reconnect(index)
    if success:
        bot_id = await bot_service.get_bot_id(index) or index
        return _ok(f"Bot {bot_id} reconnected")
    return _err(500, f"Bot at index {index} reconnection failed")


@router.get("/bots/config")
async def api_bots_config(config_service=Depends(get_config_service)):
    # 对外打码 access_token
    return JSONResponse(content={"bots": config_service.get_bots_public()})


@router.post("/bots/config/save")
async def api_bots_config_save(request: Request, config_service=Depends(get_config_service)):
    try:
        data = await request.json()
    except Exception as e:
        # 绝不能把解析失败折成空 dict 再往下走：save_bots 会先 **DELETE FROM bots**
        # 再按 payload 重建，于是「body 被代理截断 / 非法 JSON / 前端字段名漂移」
        # 会把全部账号配置清空，还回一句「配置已保存」。其他写接口缺字段一律 400。
        return _err(400, f"请求体不是合法 JSON，未改动任何账号配置: {e}")
    bots = data.get("bots") if isinstance(data, dict) else None
    if not isinstance(bots, list):
        # 空数组合法（= 用户主动删光所有账号），但**缺字段**和**类型错误**必须拒绝
        return _err(400, "请求体缺少 bots 数组，未改动任何账号配置")
    await config_service.save_bots(bots)
    return _ok("配置已保存")


@router.post("/bots/config/add")
async def api_bots_config_add(bot_service=Depends(get_bot_service)):
    # Reuse BotService.add_bot: persist the config AND register a gateway connection
    # (status "disconnected") at once, so /api/bots (status list) includes the new
    # account immediately and the cards / account selector refresh right away.
    index = await bot_service.add_bot("", None, False)
    return _ok("已添加新账号配置", index=index)


@router.post("/bots/config/delete/{index}")
async def api_bots_config_delete(
    index: int,
    bot_service=Depends(get_bot_service),
    cfg_service=Depends(get_config_service),
):
    await bot_service.gateway.del_bot(index)
    ok = await cfg_service.delete_bot(index)
    if not ok:
        return _err(404, f"索引 {index} 不存在")
    # 配置列表已压缩索引，立即对齐 gateway 连接映射，避免前端短暂错位
    await bot_service.gateway.reconcile()
    return _ok("已删除账号配置")


@router.get("/bots/groups")
async def get_all_bots_groups(bot_service=Depends(get_bot_service)):
    return JSONResponse(content={"bots_groups": bot_service.get_bots_groups()})


@router.get("/bots/{index}")
async def api_bot(index: int, bot_service=Depends(get_bot_service)):
    info = bot_service.gateway.get_bot_info_by_index(index)
    if info:
        return JSONResponse(content=info)
    return _err(404, f"Bot at index {index} not found")
