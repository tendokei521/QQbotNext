"""官方机器人 Webhook 回调入口（公开路径，靠 Ed25519 验签而不是 WebUI 令牌）。

使用方式：账号连接方式选 ``webhook``，在 QQ 开放平台填
``https://<公网域名>/qqbot/webhook/<account_id>``（官方只允许 80/443/8080/8443，
通常需要反向代理到本程序端口）。

安全边界：

- **op=13 回调地址验证**：用该账号的 AppSecret 派生密钥签名后返回；
- **普通事件**：必须通过 ``X-Signature-Ed25519`` 验签，否则 401；
- 验签通过后只做「入队」并立刻回 ``op=12`` ACK，重活（LLM）在后台 worker 里跑，
  避免官方等待超时重试。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.logger import logger
from app.domain.protocol import PROTOCOL_QQBOT, normalize_protocol
from app.infrastructure.config.config_service import ConfigService

router = APIRouter(tags=["qqbot-webhook"])


def _gateway(request: Request):
    from app.infrastructure.gateway_hub import GatewayHub

    return request.app.state.container.get(GatewayHub)


@router.post("/qqbot/webhook/{account_id}")
async def qqbot_webhook(account_id: str, request: Request):
    from app.infrastructure.qqbot.webhook import (
        SIGNATURE_HEADER,
        TIMESTAMP_HEADER,
        validation_signature,
        verify_signature,
    )

    config_service = request.app.state.container.get(ConfigService)
    bot_cfg = config_service.get_bot(account_id)
    if not bot_cfg:
        return JSONResponse(status_code=404, content={"message": f"账号 {account_id} 不存在"})
    if normalize_protocol(bot_cfg.get("protocol")) != PROTOCOL_QQBOT:
        return JSONResponse(status_code=404, content={"message": "该账号不是官方机器人"})
    secret = str(bot_cfg.get("app_secret") or "")
    if not secret:
        return JSONResponse(status_code=503, content={"message": "账号尚未配置 AppSecret"})

    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JSONResponse(status_code=400, content={"message": "回调体不是合法 JSON"})
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"message": "回调体必须是 JSON 对象"})

    op = int(payload.get("op", -1) or -1)
    if op == 13:
        data = payload.get("d") or {}
        plain_token = str(data.get("plain_token") or "")
        event_ts = str(data.get("event_ts") or "")
        if not plain_token or not event_ts:
            return JSONResponse(status_code=400, content={"message": "验证请求缺少 plain_token/event_ts"})
        return JSONResponse(content={
            "plain_token": plain_token,
            "signature": validation_signature(secret, plain_token, event_ts),
        })

    signature = request.headers.get(SIGNATURE_HEADER, "")
    timestamp = request.headers.get(TIMESTAMP_HEADER, "")
    if not verify_signature(secret, timestamp, raw, signature):
        logger.add_info("QQBot-Webhook").warning(
            f"[Webhook] 验签失败（account={account_id}, path={request.url.path}）"
        )
        return JSONResponse(status_code=401, content={"message": "invalid signature"})

    try:
        await _gateway(request).handle_webhook(account_id, payload)
    except Exception as e:  # noqa: BLE001 —— 入队失败也要先 ACK，避免官方反复重试同一条
        logger.add_info("QQBot-Webhook").warning(f"[Webhook] 入队失败（account={account_id}）: {e}")
    return JSONResponse(content={"op": 12})
