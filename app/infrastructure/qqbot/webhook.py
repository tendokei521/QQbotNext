"""官方 Webhook 接入：Ed25519 验签 + 回调地址验证。

官方算法（`bot.q.qq.com/wiki/.../sign.html`）：

- 密钥由 Bot Secret 反复拼接（``secret`` 重复到 ≥32 字节后截断 32 字节）作为 seed，
  派生 Ed25519 私钥/公钥；
- 事件验签：``X-Signature-Ed25519``（hex）+ ``X-Signature-Timestamp``，
  签名体 = ``timestamp + raw body``；
- 回调地址验证（op=13）：对 ``event_ts + plain_token`` 签名，响应
  ``{"plain_token": ..., "signature": <hex>}``；
- 普通事件回 ``{"op": 12}`` 作为 ACK。

依赖 ``cryptography``（纯 Python 侧只做验签/签名，不碰网络）。
"""

from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SEED_SIZE = 32
SIGNATURE_HEADER = "X-Signature-Ed25519"
TIMESTAMP_HEADER = "X-Signature-Timestamp"


def _seed(secret: str) -> bytes:
    """Bot Secret → 32 字节 seed（与官方 Go 示例的 repeat 逻辑一致）。"""
    raw = str(secret or "").encode("utf-8")
    if not raw:
        raise ValueError("Bot Secret 为空，无法派生签名密钥")
    seed = raw
    while len(seed) < SEED_SIZE:
        seed = seed + seed
    return seed[:SEED_SIZE]


def _private_key(secret: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(_seed(secret))


def verify_signature(secret: str, timestamp: str, body: bytes, signature_hex: str) -> bool:
    """校验一条回调请求的签名；任何格式错误都按失败处理（fail-closed）。"""
    if not secret or not timestamp or not signature_hex:
        return False
    try:
        signature = bytes.fromhex(str(signature_hex))
    except ValueError:
        return False
    if len(signature) != 64:
        return False
    message = str(timestamp).encode("utf-8") + (body or b"")
    try:
        _private_key(secret).public_key().verify(signature, message)
    except InvalidSignature:
        return False
    return True


def validation_signature(secret: str, plain_token: str, event_ts: str) -> str:
    """回调地址验证（op=13）的应答签名（hex）。"""
    message = (str(event_ts) + str(plain_token)).encode("utf-8")
    return _private_key(secret).sign(message).hex()
