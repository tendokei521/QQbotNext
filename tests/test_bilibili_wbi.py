"""bilibili_parser 的 WBI 签名与取流单测（全离线，不联网）。

覆盖：签名纯函数固定向量、安全字符过滤、密钥长度校验，
以及 BilibiliAPI 取密钥（容忍 -101）与 playurl 签名 / -403 重试。
"""

import json
import urllib.parse

from module.modules.bilibili_parser import bilibili_api as bapi
from module.modules.bilibili_parser import wbi

KEY = "0123456789abcdef0123456789abcdef"


# ==================== 签名纯函数 ====================


def test_extract_keys_real_values():
    """实测抓到的真实密钥对：能推导出 32 位 mixin_key。"""
    img = "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png"
    sub = "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"
    key = wbi.extract_keys(img, sub)
    assert len(key) == 32
    assert key == wbi.get_mixin_key("7cd084941338484aae1ad9425b84077c4932caff0ff746eab6f01bf08b70ac45")


def test_get_mixin_key_is_deterministic():
    orig = "".join(chr(ord("a") + i % 26) for i in range(64))
    assert wbi.get_mixin_key(orig) == wbi.get_mixin_key(orig)


def test_get_mixin_key_short_orig_raises():
    """原文长度异常必须报错，而不是静默产出错误签名。"""
    try:
        wbi.get_mixin_key("tooshort")
        raise AssertionError("应当抛出 ValueError")
    except ValueError:
        pass


def test_enc_wbi_adds_wts_and_wrid():
    out = wbi.enc_wbi({"bvid": "BV1zq836rEbk", "cid": 41052212697}, KEY, ts=1700000000)
    assert out["wts"] == "1700000000"
    assert len(out["w_rid"]) == 32  # md5 hex
    assert out["bvid"] == "BV1zq836rEbk"
    assert out["cid"] == "41052212697"


def test_enc_wbi_fixed_vector():
    """固定向量：确保算法不被意外改动。"""
    import hashlib

    out = wbi.enc_wbi({"a": "1", "b": "2"}, KEY, ts=1700000000)
    expect = hashlib.md5(("a=1&b=2&wts=1700000000" + KEY).encode()).hexdigest()
    assert out["w_rid"] == expect


def test_enc_wbi_key_order_independent():
    a = wbi.enc_wbi({"b": "2", "a": "1"}, KEY, ts=1700000000)
    b = wbi.enc_wbi({"a": "1", "b": "2"}, KEY, ts=1700000000)
    assert a["w_rid"] == b["w_rid"]


def test_enc_wbi_drops_none_and_bool_as_js():
    out = wbi.enc_wbi({"a": 1, "b": None, "c": True}, KEY, ts=1)
    assert "b" not in out
    assert out["c"] == "true"


def test_sanitize_filters_unsafe_chars():
    """前端 encodeURIComponent 会转义 !'()* ，签名前需剔除。"""
    out = wbi.enc_wbi({"q": "a!'()*b"}, KEY, ts=1)
    assert out["q"] == "ab"


def test_signed_query_contains_wts_and_wrid():
    query = wbi.signed_query({"bvid": "BV1zq836rEbk", "cid": 1}, KEY)
    params = dict(urllib.parse.parse_qsl(query))
    assert params["bvid"] == "BV1zq836rEbk"
    assert params["cid"] == "1"
    assert params["wts"].isdigit()
    assert len(params["w_rid"]) == 32


# ==================== API 层（假 GET，不联网） ====================

NAV_PAYLOAD = {
    "code": -101,
    "message": "账号未登录",
    "data": {
        "wbi_img": {
            "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
            "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
        }
    },
}


class FakeResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _fake_api(calls: list, responses: list):
    """构造一个未进入上下文的 BilibiliAPI，GET 换成按序返回假响应。"""
    api = bapi.BilibiliAPI()
    nav = FakeResp(NAV_PAYLOAD)

    async def fake_get(url, params=None, headers=None, timeout=60):
        calls.append(url)
        if url == bapi.BILIBILI_API_NAV:
            return nav
        return FakeResp(responses.pop(0))

    api.GET = fake_get
    return api


async def test_get_mixin_key_tolerates_not_logged_in_and_caches():
    """nav 返回 -101 也要能拿到密钥；第二次调用走缓存不再请求。"""
    from module.modules.bilibili_parser.bilibili_api import BilibiliAPI

    BilibiliAPI._wbi_cache = {}
    calls: list = []
    api = _fake_api(calls, [])
    key = await api.get_mixin_key()
    assert len(key) == 32
    assert calls == [bapi.BILIBILI_API_NAV]

    assert await api.get_mixin_key() == key
    assert calls == [bapi.BILIBILI_API_NAV]  # 命中缓存


async def test_get_playurl_single_signs_query_and_returns_data():
    from module.modules.bilibili_parser.bilibili_api import BilibiliAPI

    BilibiliAPI._wbi_cache = {}
    calls: list = []
    play = {"code": 0, "data": {"quality": 64, "format": "mp4720", "durl": [{"url": "http://x", "size": 1}]}}
    api = _fake_api(calls, [play])

    data = await api.get_playurl_single("BV1zq836rEbk", 41052212697, qn=64, cookie="SESSDATA=abc")
    assert data["format"] == "mp4720"

    url = calls[-1]
    assert url.startswith(bapi.BILIBILI_API_PLAYURL_WBI + "?")
    params = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
    assert params["bvid"] == "BV1zq836rEbk"
    assert params["cid"] == "41052212697"
    assert params["qn"] == "64"
    assert params["fnval"] == "0"  # 单文件模式
    assert len(params["w_rid"]) == 32


async def test_get_playurl_single_refreshes_wbi_key_on_403():
    """-403 视为密钥轮换：刷新密钥后重试一次并成功。"""
    from module.modules.bilibili_parser.bilibili_api import BilibiliAPI

    BilibiliAPI._wbi_cache = {}
    calls: list = []
    api = _fake_api(calls, [
        {"code": -403, "message": "权限不足"},
        {"code": 0, "data": {"quality": 64, "durl": [{"url": "http://x", "size": 1}]}},
    ])

    data = await api.get_playurl_single("BV1zq836rEbk", 1)
    assert data == {"quality": 64, "durl": [{"url": "http://x", "size": 1}]}
    assert calls.count(bapi.BILIBILI_API_NAV) == 2  # 首次取 key + 403 后强刷


async def test_get_playurl_single_retries_on_risk_control(monkeypatch):
    """风控返回 v_voucher（code=0 无 durl）→ 退避后重试成功。"""
    from module.modules.bilibili_parser.bilibili_api import BilibiliAPI

    BilibiliAPI._wbi_cache = {}
    sleeps: list = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(bapi, "_sleep", fake_sleep)

    calls: list = []
    api = _fake_api(calls, [
        {"code": 0, "data": {"v_voucher": "voucher-xxx"}},
        {"code": 0, "data": {"quality": 64, "format": "mp4720", "durl": [{"url": "http://x", "size": 9}]}},
    ])

    data = await api.get_playurl_single("BV1zq836rEbk", 1, qn=64)
    assert data["format"] == "mp4720"
    assert sleeps == [2.0]  # 只退避一次
    assert len([u for u in calls if u.startswith(bapi.BILIBILI_API_PLAYURL_WBI)]) == 2  # 重签重发


async def test_get_playurl_single_gives_up_after_retries(monkeypatch):
    """连续风控 → 重试耗尽后返回 None（不抛异常）。"""
    from module.modules.bilibili_parser.bilibili_api import BilibiliAPI

    BilibiliAPI._wbi_cache = {}
    sleeps: list = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(bapi, "_sleep", fake_sleep)

    calls: list = []
    api = _fake_api(calls, [{"code": 0, "data": {"v_voucher": "x"}}] * bapi.PLAYURL_RETRIES)

    assert await api.get_playurl_single("BV1zq836rEbk", 1) is None
    assert sleeps == [2.0, 5.0, 10.0]  # 退避三次（最后一次不再等待）


async def test_get_playurl_single_returns_none_on_business_error():
    from module.modules.bilibili_parser.bilibili_api import BilibiliAPI

    BilibiliAPI._wbi_cache = {}
    calls: list = []
    api = _fake_api(calls, [{"code": -404, "message": "啥都木有"}])
    assert await api.get_playurl_single("BV1zq836rEbk", 1) is None


def test_json_dumps_payload_shape_is_stable():
    """守住 nav 返回结构的解析假设（data.wbi_img.img_url / sub_url）。"""
    payload = json.loads(json.dumps(NAV_PAYLOAD))
    wbi_img = payload["data"]["wbi_img"]
    assert wbi.extract_keys(wbi_img["img_url"], wbi_img["sub_url"])
