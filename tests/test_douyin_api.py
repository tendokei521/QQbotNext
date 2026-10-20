"""douyin_parser 的网络层单测（全离线：假 GET / 假 stream）。

覆盖：短链归一（跳转 → 落地页 HTML 兜底）、详情请求的签名与降级（a_bogus → X-Bogus）、
主页作品翻页与去重、流式下载（重试回滚 / 原子改名 / 半成品清理）、按 bot 隔离的作品去重。
"""

import os
from urllib.parse import parse_qsl, urlparse

from app.plugins.douyin_parser import douyin_api as api_mod
from app.plugins.douyin_parser.douyin_api import DouyinAPI

DETAIL_OK = {
    "status_code": 0,
    "aweme_detail": {"aweme_id": "7534291258635816251", "aweme_type": 0},
}


class FakeResp:
    def __init__(self, payload=None, *, url: str = "", text: str = "", status: int = 200):
        self._payload = payload
        self.url = url
        self.text = text
        self.status_code = status
        self.content = b"x" if payload is not None else b""

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _api(handler) -> DouyinAPI:
    """构造未进入上下文的 DouyinAPI，GET 换成 handler(url, params, headers, timeout)。"""
    api = DouyinAPI()

    async def fake_get(url, params=None, headers=None, timeout=60):
        return handler(url, params=params, headers=headers, timeout=timeout)

    api.GET = fake_get
    return api


def _query(url: str) -> dict:
    return dict(parse_qsl(urlparse(url).query))


# ==================== 短链归一 ====================


async def test_resolve_short_link_returns_final_url():
    api = _api(lambda url, **kw: FakeResp(url="https://www.douyin.com/video/7534291258635816251"))
    assert await api.resolve_short_link("https://v.douyin.com/iRNBho6u/") == (
        "https://www.douyin.com/video/7534291258635816251"
    )


async def test_resolve_short_link_falls_back_to_input_on_error():
    def boom(url, **kw):
        raise ConnectionError("network down")

    assert await _api(boom).resolve_short_link("https://v.douyin.com/x/") == "https://v.douyin.com/x/"


async def test_resolve_aweme_id_direct_link_needs_no_request():
    def boom(url, **kw):
        raise AssertionError("直链不该发请求")

    api = _api(boom)
    assert await api.resolve_aweme_id("https://www.douyin.com/video/7534291258635816251") == "7534291258635816251"
    assert await api.resolve_aweme_id("https://www.douyin.com/note/7000000000000000001") == "7000000000000000001"


async def test_resolve_aweme_id_via_redirect():
    api = _api(lambda url, **kw: FakeResp(url="https://www.douyin.com/video/7534291258635816251"))
    assert await api.resolve_aweme_id("https://v.douyin.com/iRNBho6u/") == "7534291258635816251"


async def test_resolve_aweme_id_via_landing_page_html():
    """短链跳转后 URL 里没有 ID（如分享落地页）时，从 HTML 里兜底提取。"""
    html = '<script>window.__DATA__={"aweme_id":"7534291258635816251"}</script>'
    api = _api(lambda url, **kw: FakeResp(url="https://www.douyin.com/share/landing", text=html))
    assert await api.resolve_aweme_id("https://v.douyin.com/iRNBho6u/") == "7534291258635816251"


async def test_resolve_aweme_id_returns_none_when_unresolvable():
    api = _api(lambda url, **kw: FakeResp(url="https://www.douyin.com/", text="<html>无</html>"))
    assert await api.resolve_aweme_id("https://v.douyin.com/iRNBho6u/") is None


# ==================== 作品详情 ====================


async def test_get_aweme_detail_signs_with_abogus():
    calls: list = []

    def handler(url, params, headers, timeout):
        calls.append({"url": url, "headers": headers})
        return FakeResp(DETAIL_OK)

    api = _api(handler)
    data = await api.get_aweme_detail("7534291258635816251", cookie="ttwid=abc")

    assert data == DETAIL_OK
    assert len(calls) == 1
    query = _query(calls[0]["url"])
    assert calls[0]["url"].startswith(api_mod.API_DETAIL + "?")
    assert query["aweme_id"] == "7534291258635816251"
    assert query["device_platform"] == "webapp"
    assert query["aid"] == "6383"
    assert len(query["a_bogus"]) > 100  # a_bogus 已拼上
    assert calls[0]["headers"]["Cookie"] == "ttwid=abc"
    assert calls[0]["headers"]["Referer"] == "https://www.douyin.com/video/7534291258635816251"


async def test_get_aweme_detail_falls_back_to_xbogus():
    """a_bogus 那一路失败（非 200 / 业务码非 0）时必须再试 X-Bogus。"""
    seen: list = []

    def handler(url, params, headers, timeout):
        seen.append(url)
        if "a_bogus=" in url:
            return FakeResp({"status_code": 8, "aweme_detail": None})
        return FakeResp(DETAIL_OK)

    data = await _api(handler).get_aweme_detail("7534291258635816251")

    assert data == DETAIL_OK
    assert len(seen) == 2
    assert "a_bogus=" in seen[0]
    assert "X-Bogus=" in seen[1]


async def test_get_aweme_detail_none_when_filtered():
    """status_code=0 但没有 aweme_detail（内容被过滤/删除）→ 视为失败。"""
    api = _api(lambda url, **kw: FakeResp({"status_code": 0, "aweme_detail": None}))
    assert await api.get_aweme_detail("1") is None


async def test_get_aweme_detail_none_on_http_error_or_bad_json():
    api = _api(lambda url, **kw: FakeResp(DETAIL_OK, status=403))
    assert await api.get_aweme_detail("1") is None

    api = _api(lambda url, **kw: FakeResp(ValueError("not json")))
    assert await api.get_aweme_detail("1") is None


# ==================== 主页作品 ====================


async def test_get_user_aweme_ids_paginates_and_dedupes():
    pages = [
        {"status_code": 0, "has_more": 1, "max_cursor": 100,
         "aweme_list": [{"aweme_id": "1"}, {"aweme_id": "2"}]},
        {"status_code": 0, "has_more": 0, "max_cursor": 200,
         "aweme_list": [{"aweme_id": "2"}, {"aweme_id": "3"}]},
    ]
    cursors: list = []

    def handler(url, params, headers, timeout):
        cursors.append(_query(url)["max_cursor"])
        return FakeResp(pages[len(cursors) - 1])

    ids = await _api(handler).get_user_aweme_ids("MS4wLjABAAAA_x", max_pages=5)

    assert ids == ["1", "2", "3"]  # 跨页去重
    assert cursors == ["0", "100"]  # has_more=0 后停止翻页


async def test_get_user_aweme_ids_stops_on_failure_and_clamps_pages():
    calls: list = []

    def handler(url, params, headers, timeout):
        calls.append(url)
        return FakeResp(None)  # 请求失败

    assert await _api(handler).get_user_aweme_ids("MS4wLjABAAAA_x", max_pages=99) == []
    # 一次业务尝试 = a_bogus + X-Bogus 各一发；失败即停，不继续翻页空转
    assert len(calls) == 2

    calls.clear()
    assert await _api(handler).get_user_aweme_ids("") == []  # 无 sec_uid 直接返回
    assert calls == []


# ==================== 流式下载 ====================


class FakeStreamResp:
    def __init__(self, chunks, status: int = 200, fail_after: int | None = None):
        self._chunks = chunks
        self.status_code = status
        self._fail_after = fail_after

    async def aiter_content(self, chunk_size=0):
        for index, chunk in enumerate(self._chunks):
            if self._fail_after is not None and index >= self._fail_after:
                raise ConnectionError("HTTP/2 stream not closed cleanly")
            yield chunk
        if self._fail_after is not None and self._fail_after >= len(self._chunks):
            raise ConnectionError("HTTP/2 stream not closed cleanly")


class FakeStream:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


def _api_with_stream(responses: list) -> DouyinAPI:
    api = DouyinAPI()
    queue = list(responses)
    api.stream_calls = []

    def fake_stream(method, url, headers=None, timeout=60):
        api.stream_calls.append(url)
        return FakeStream(queue.pop(0) if queue else FakeStreamResp([b"extra"]))

    api.stream = fake_stream
    return api


async def test_download_writes_file_and_replaces_part(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "_sleep", _no_sleep)
    dest = str(tmp_path / "v.mp4")
    api = _api_with_stream([FakeStreamResp([b"ab", b"cd"])])

    await api.download("http://cdn/x", dest, timeout=5)

    with open(dest, "rb") as fh:
        assert fh.read() == b"abcd"
    assert not os.path.exists(dest + ".part")  # 原子改名，无残留


async def test_download_retries_and_rolls_back_partial(tmp_path, monkeypatch):
    """中断后必须回滚到起始偏移再重试，文件里不得出现重复字节。"""
    monkeypatch.setattr(api_mod, "_sleep", _no_sleep)
    dest = str(tmp_path / "v.mp4")
    api = _api_with_stream([FakeStreamResp([b"AB"], fail_after=1), FakeStreamResp([b"AB", b"CD"])])

    await api.download("http://cdn/x", dest, timeout=5)

    with open(dest, "rb") as fh:
        assert fh.read() == b"ABCD"  # 不是 ABABCD


async def test_download_cleans_part_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "_sleep", _no_sleep)
    dest = str(tmp_path / "v.mp4")
    api = _api_with_stream([FakeStreamResp([b"x"], status=500)] * api_mod.DOWNLOAD_RETRIES)

    try:
        await api.download("http://cdn/x", dest, timeout=5)
        raise AssertionError("应抛出异常")
    except RuntimeError as exc:
        assert "HTTP 500" in str(exc)

    assert not os.path.exists(dest)
    assert not os.path.exists(dest + ".part")


async def _no_sleep(seconds):
    return None


async def test_download_aborts_when_exceeding_max_bytes(tmp_path, monkeypatch):
    """体积上限是边下边卡的：超限立即中断、清理半成品，且不再重试。"""
    monkeypatch.setattr(api_mod, "_sleep", _no_sleep)
    dest = str(tmp_path / "v.mp4")
    api = _api_with_stream([FakeStreamResp([b"AAAA", b"BBBB"])])

    try:
        await api.download("http://cdn/x", dest, timeout=5, max_bytes=6)
        raise AssertionError("应抛出 DownloadTooLarge")
    except api_mod.DownloadTooLarge:
        pass

    assert len(api.stream_calls) == 1  # 超限是确定放弃，不重试
    assert not os.path.exists(dest)
    assert not os.path.exists(dest + ".part")


async def test_download_within_max_bytes_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "_sleep", _no_sleep)
    dest = str(tmp_path / "v.mp4")
    api = _api_with_stream([FakeStreamResp([b"AAAA", b"BBBB"])])

    await api.download("http://cdn/x", dest, timeout=5, max_bytes=8)

    with open(dest, "rb") as fh:
        assert fh.read() == b"AAAABBBB"


# ==================== 去重 ====================


def test_filter_aweme_dedup_isolates_bots_and_expires():
    DouyinAPI._aweme_cache = {}
    assert DouyinAPI.filter_aweme_dedup(["1", "2"], 60, bot_id=10001) == ["1", "2"]
    # 同一 bot 在窗口内重复 → 跳过
    assert DouyinAPI.filter_aweme_dedup(["1", "3"], 60, bot_id=10001) == ["3"]
    # 另一个 bot 不受影响
    assert DouyinAPI.filter_aweme_dedup(["1"], 60, bot_id=10002) == ["1"]
    # 超时后重新放行
    assert DouyinAPI.filter_aweme_dedup(["1"], 0, bot_id=10001) == ["1"]
