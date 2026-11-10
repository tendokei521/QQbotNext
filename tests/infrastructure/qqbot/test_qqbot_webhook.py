"""Webhook 验签契约：Ed25519 seed 派生、事件签名、回调地址验证。"""

from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.infrastructure.qqbot.webhook import (
    _seed,
    validation_signature,
    verify_signature,
)

#: 官方 sign.html 的 DEMO：secret → seed（前 32 字节）
SECRET = "naOC0ocQE3shWLAfffVLB1rhYPG7"


def test_seed_matches_official_demo():
    assert _seed(SECRET) == b"naOC0ocQE3shWLAfffVLB1rhYPG7naOC"


def test_signature_roundtrip_and_tamper_detection():
    body = json.dumps({"op": 0, "t": "GROUP_AT_MESSAGE_CREATE", "d": {"id": "m1"}}).encode()
    timestamp = "1725442341"
    private = Ed25519PrivateKey.from_private_bytes(_seed(SECRET))
    signature = private.sign(timestamp.encode() + body).hex()

    assert verify_signature(SECRET, timestamp, body, signature) is True
    assert verify_signature(SECRET, timestamp, body + b" ", signature) is False
    assert verify_signature("wrong-secret", timestamp, body, signature) is False
    assert verify_signature(SECRET, timestamp, body, "zzzz") is False


def test_validation_signature_is_verifiable():
    plain_token = "Arq0D5A61EgUu4OxUvOp"
    event_ts = "1725442341"
    signature = validation_signature(SECRET, plain_token, event_ts)

    public = Ed25519PrivateKey.from_private_bytes(_seed(SECRET)).public_key()
    public.verify(bytes.fromhex(signature), (event_ts + plain_token).encode())


def test_empty_secret_rejected():
    with pytest.raises(ValueError):
        _seed("")
