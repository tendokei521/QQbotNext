"""Bot 连接与账号配置 API。

依赖一律通过 `Depends(get_bot_service)` / `Depends(get_config_service)` 注入——
读一个路由就能看出它依赖谁，不必追到装配根。

## 身份：路径里放 account_id

历史实现用数组下标 ``/api/bots/{index}/connect``。下标在删账号后会整体前移，
同一个 URL 隔天可能指向另一个账号，因此改为**用户设定的账号 id**：

    POST /api/bots/{account_id}/connect
    POST /api/bots/config/delete/{account_id}

返回文案与日志一律用**账号名**（``name``），id 只做寻址。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.core.logger import logger
from app.infrastructure.onebot.endpoint import (
    EndpointError,
    is_reverse,
    normalize_mode,
    parse_endpoint,
    suggest_address,
)
from app.webui.api.deps import get_bot_service, get_config_service

router = APIRouter(tags=["bots"])


def _ok(message: str, **extra):
    return JSONResponse(content={"status": "success", "message": message, **extra})


def _err(status: int, message: str):
    return JSONResponse(status_code=status, content={"status": "error", "message": message})


def _validate_endpoints(bots: list) -> list[dict]:
    """校验账号 ``ws_url`` 的形态，返回 ``[{id, error}]``（空列表 = 全部可用）。

    为什么放在 API 层而不是 ``save_bots``：``save_bots`` 还服务脚本/测试等
    程序化调用，那里塞入严格校验会误伤"先存配置、后填地址"的用法；而 WebUI
    的保存/新增是用户输入的唯一入口，必须当场拦下。

    空地址不算错误（新账号可能还没填）；**填了就必须能用**。反向模式额外要求
    写明端口——监听地址没有端口，对端根本无从填写。
    """
    problems: list[dict] = []
    for index, bot in enumerate(bots):
        if not isinstance(bot, dict):
            continue
        raw = str(bot.get("ws_url") or "").strip()
        if not raw:
            continue
        mode = normalize_mode(bot.get("conn_mode"))
        try:
            parse_endpoint(raw, require_port=is_reverse(mode))
        except EndpointError as e:
            problems.append({"id": str(bot.get("id") or f"#{index + 1}"), "error": str(e)})
    return problems


@router.get("/bots")
async def api_bots(bot_service=Depends(get_bot_service)):
    return JSONResponse(content={"bots": bot_service.get_bots_data()})


@router.post("/bots/{account_id}/connect")
async def connect_bot(
    account_id: str,
    bot_service=Depends(get_bot_service),
    config_service=Depends(get_config_service),
):
    bot_cfg = config_service.get_bot(account_id)
    if bot_cfg is None:
        return _err(404, f"账号 {account_id} 不存在")
    name = bot_cfg.get("name") or account_id
    conn_mode = bot_cfg.get("conn_mode")
    if account_id not in bot_service.gateway.connections:
        await bot_service.gateway.add_bot(
            bot_cfg.get("ws_url", ""), None, bot_cfg.get("auto_connect", False),
            account_id=account_id, conn_mode=conn_mode or "", name=name,
        )
    else:
        await bot_service.gateway.readd_bot(
            bot_cfg.get("ws_url", ""), None, account_id, conn_mode=conn_mode
        )

    if await bot_service.connect(account_id):
        return _ok(f"账号「{name}」已连接")
    return _err(500, f"账号「{name}」连接失败")


@router.post("/bots/{account_id}/disconnect")
async def disconnect_bot(
    account_id: str,
    bot_service=Depends(get_bot_service),
    config_service=Depends(get_config_service),
):
    await bot_service.disconnect(account_id)
    name = config_service.get_account_name(account_id)
    return _ok(f"账号「{name}」已断开")


@router.post("/bots/{account_id}/reconnect")
async def reconnect_bot(
    account_id: str,
    bot_service=Depends(get_bot_service),
    config_service=Depends(get_config_service),
):
    name = config_service.get_account_name(account_id)
    if await bot_service.reconnect(account_id):
        return _ok(f"账号「{name}」已重连")
    return _err(500, f"账号「{name}」重连失败")


@router.get("/bots/config")
async def api_bots_config(config_service=Depends(get_config_service)):
    # 对外打码 access_token
    return JSONResponse(content={"bots": config_service.get_bots_public()})


@router.post("/bots/config/save")
async def api_bots_config_save(request: Request, config_service=Depends(get_config_service)):
    try:
        data = await request.json()
    except Exception as e:
        # 绝不能把解析失败折成空 dict 再往下走：save_bots 会按 payload 删账号，
        # 于是「body 被代理截断 / 非法 JSON / 前端字段名漂移」会把配置清空，
        # 还回一句「配置已保存」。其他写接口缺字段一律 400。
        return _err(400, f"请求体不是合法 JSON，未改动任何账号配置: {e}")
    bots = data.get("bots") if isinstance(data, dict) else None
    if not isinstance(bots, list):
        # 空数组合法（= 用户主动删光所有账号），但**缺字段**和**类型错误**必须拒绝
        return _err(400, "请求体缺少 bots 数组，未改动任何账号配置")
    problems = _validate_endpoints(bots)
    if problems:
        detail = "；".join(f"{p['id']}: {p['error']}" for p in problems[:3])
        more = f"（共 {len(problems)} 个）" if len(problems) > 3 else ""
        return _err(400, f"连接地址不可用，未改动任何账号配置{more}：{detail}")
    try:
        await config_service.save_bots(bots)
    except ValueError as e:
        return _err(400, str(e))
    return _ok("配置已保存")


@router.post("/bots/config/add")
async def api_bots_config_add(
    request: Request,
    bot_service=Depends(get_bot_service),
    cfg_service=Depends(get_config_service),
):
    """新增一个账号配置。

    收 ``{"id": 可选, "name": 可选, "conn_mode": "forward"|"reverse", "ws_url": 可选}``：

    - ``id`` 是账号唯一凭据（用户填；缺省自动分配 ``botN``，脚本调用友好）；
    - ``name`` 是展示名（缺省「账号 N」）；
    - ``ws_url`` 留空时按模式给默认建议（端口避开已有账号）——新卡片一出生就是
      可用地址，不必等用户点连接才发现「反向地址必须写端口」。
    """
    body: dict = {}
    try:
        parsed = await request.json()
        if isinstance(parsed, dict):
            body = parsed
    except Exception:
        body = {}   # 无 body 是合法调用（脚本可以只 POST 一个连接方式）

    mode = normalize_mode(body.get("conn_mode"))
    ws_url = str(body.get("ws_url") or "").strip()
    if not ws_url:
        ws_url = suggest_address(mode, [b.get("ws_url", "") for b in cfg_service.get_bots()])
    # 用户显式给了地址就先校验：新建即报错，比"卡片存下了但连不上"好排查
    try:
        parse_endpoint(ws_url, require_port=is_reverse(mode))
    except EndpointError as e:
        return _err(400, f"连接地址不可用: {e}")
    try:
        # Reuse BotService.add_bot: persist the config AND register a gateway connection
        # (status "disconnected") at once, so /api/bots (status list) includes the new
        # account immediately and the cards / account selector refresh right away.
        account_id = await bot_service.add_bot(
            ws_url, None, False, conn_mode=mode,
            account_id=str(body.get("id") or "").strip() or None,
            name=str(body.get("name") or "").strip(),
        )
    except ValueError as e:
        return _err(400, str(e))
    name = cfg_service.get_account_name(account_id)
    return _ok(
        f"已添加账号「{name}」",
        id=account_id, name=name, conn_mode=mode, ws_url=ws_url,
    )


@router.post("/bots/config/delete/{account_id}")
async def api_bots_config_delete(
    account_id: str,
    bot_service=Depends(get_bot_service),
    cfg_service=Depends(get_config_service),
):
    name = cfg_service.get_account_name(account_id)
    ok = await bot_service.delete_bot(account_id)
    if not ok:
        return _err(404, f"账号 {account_id} 不存在")
    # 立即对齐 gateway 连接映射，避免前端短暂错位
    await bot_service.gateway.reconcile()
    return _ok(f"已删除账号「{name}」")


@router.get("/bots/groups")
async def get_all_bots_groups(bot_service=Depends(get_bot_service)):
    return JSONResponse(content={"bots_groups": bot_service.get_bots_groups()})


@router.get("/bots/{account_id}")
async def api_bot(account_id: str, bot_service=Depends(get_bot_service)):
    info = bot_service.gateway.get_bot_info(account_id)
    if info:
        return JSONResponse(content=info)
    return _err(404, f"账号 {account_id} 不存在")


# ==================== 诊断台：收包事件监视 + 发送测试消息 ====================
#: 单条事件 raw 的 JSON 序列化上限（超出则逐字段截断，防止响应体被超大 payload 撑爆）
EVENT_RAW_MAX_CHARS = 4000
_EVENT_RAW_FIELD_MAX = 500


def _trim_event_raw(raw):
    """限制事件 raw 体积：正常事件原样返回；超大 payload 逐字段截断后返回。"""
    if not isinstance(raw, dict):
        return raw
    try:
        serialized = json.dumps(raw, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"preview": str(raw)[:EVENT_RAW_MAX_CHARS]}
    if len(serialized) <= EVENT_RAW_MAX_CHARS:
        return raw
    trimmed: dict = {}
    for key, value in raw.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        trimmed[key] = text if len(text) <= _EVENT_RAW_FIELD_MAX else text[:_EVENT_RAW_FIELD_MAX] + "…（已截断）"
    return trimmed


def _parse_group_id(value) -> int | None:
    """把请求里的 group_id 解析成正整数；缺省/非法都返回 None（由调用方给中文提示）。"""
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value).strip()
    if text.isdigit() and int(text) > 0:
        return int(text)
    return None


def _account_or_404(account_id: str, bot_service, config_service):
    """取账号展示名；账号既不在配置也不在连接表里 → 返回 404 响应。"""
    conn = bot_service.gateway.connections.get(str(account_id))
    if conn is None and config_service.get_bot(account_id) is None:
        return None, _err(404, f"账号 {account_id} 不存在")
    name = config_service.get_account_name(account_id) or str(account_id)
    return name, None


@router.get("/bots/{account_id}/events")
async def api_bot_events(
    account_id: str,
    limit: int = 50,
    bot_service=Depends(get_bot_service),
    config_service=Depends(get_config_service),
):
    """诊断台：该账号最近收到的原始事件（协议端到底有没有把事件送进来）。

    ``drops``：当前连接内因事件队列背压被丢弃的条数（>0 说明处理能力跟不上，
    诊断台里那些 ``dropped=true`` 的事件就是「收到了但没进处理链」）。
    """
    name, error = _account_or_404(account_id, bot_service, config_service)
    if error is not None:
        return error
    gateway = bot_service.gateway
    conn = gateway.connections.get(str(account_id))
    events = [
        {**record, "raw": _trim_event_raw(record.get("raw"))}
        for record in gateway.recent_events(str(account_id), limit)
    ]
    return JSONResponse(content={
        "events": events,
        "listening": bool(conn is not None and gateway.reverse_link.listening(str(account_id))),
        "connected": bool(conn is not None and conn.status == "connected"),
        "drops": int(gateway.event_queue_drops.get(str(account_id), 0) or 0),
        "account": {"id": str(account_id), "name": name},
    })


@router.post("/bots/{account_id}/events/clear")
async def api_bot_events_clear(
    account_id: str,
    bot_service=Depends(get_bot_service),
    config_service=Depends(get_config_service),
):
    """诊断台：清空该账号的事件历史。"""
    _, error = _account_or_404(account_id, bot_service, config_service)
    if error is not None:
        return error
    bot_service.gateway.clear_events(str(account_id))
    return _ok("已清空")


@router.post("/bots/{account_id}/test-message")
async def api_bot_test_message(
    account_id: str,
    request: Request,
    bot_service=Depends(get_bot_service),
    config_service=Depends(get_config_service),
):
    """诊断台：以该账号身份发一条测试群消息（验证「发送链路」是否真的通）。

    ``group_id`` 缺省时用该账号最近一条群消息的群号；仍没有则 400 并给中文提示。
    发送失败时把 OneBot 的 retcode / message 原样回传，便于区分「配置问题」还是
    「群号问题」。
    """
    name, error = _account_or_404(account_id, bot_service, config_service)
    if error is not None:
        return error
    try:
        body = await request.json()
    except Exception as e:
        return _err(400, f"请求体不是合法 JSON: {e}")
    if not isinstance(body, dict):
        return _err(400, "请求体必须是 JSON 对象")

    text = str(body.get("text") or "").strip()
    if not text:
        return _err(400, "测试消息内容不能为空")
    if len(text) > 500:
        return _err(400, f"测试消息内容最多 500 字（当前 {len(text)} 字）")

    conn = bot_service.gateway.connections.get(str(account_id))
    if conn is None or conn.status != "connected":
        return _err(400, f"账号「{name}」当前未连接，无法发送测试消息")

    raw_group_id = body.get("group_id")
    group_id = _parse_group_id(raw_group_id)
    if raw_group_id not in (None, "") and group_id is None:
        return _err(400, "群号必须是正整数")
    if group_id is None:
        group_id = bot_service.gateway.latest_group_id(str(account_id))
    if not group_id:
        return _err(400, "还没有收到过群消息，请先填写群号")

    try:
        response = await conn.send_group_msg(group_id, text)
    except Exception as e:
        logger.warning(f"[BotAPI] 账号「{name}」发送测试消息异常: {e}")
        return _err(500, f"发送失败: {e}")
    if not response or response.get("status") != "ok":
        reason = (response or {}).get("message") or (response or {}).get("wording")
        if not response:
            reason = "连接已断开或请求被拒绝"
        retcode = (response or {}).get("retcode")
        suffix = f"（retcode={retcode}）" if retcode is not None else ""
        return _err(400, f"发送失败: {reason}{suffix}")
    return _ok("测试消息已发送")
