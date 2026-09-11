"""bilibili_parser 的单文件取流整理、本地缓存与流式下载单测（全离线）。"""

import os

from module.modules.bilibili_parser import video
from module.modules.bilibili_parser.bilibili_api import BilibiliAPI


# ==================== 纯逻辑：整理 / 格式化 / 缓存 ====================


def test_pick_single_normalizes_durl():
    data = {
        "quality": 64,
        "format": "mp4720",
        "timelength": 125120,
        "durl": [
            {"url": "http://a", "size": 100},
            {"url": "http://b", "size": 50},
        ],
    }
    out = video.pick_single(data)
    assert out["total_size"] == 150
    assert out["quality_name"] == "720P"
    assert out["format"] == "mp4720"
    assert out["duration"] == 125120
    assert [s["url"] for s in out["segments"]] == ["http://a", "http://b"]


def test_pick_single_returns_none_on_risk_control():
    """风控返回 data 只有 v_voucher → 视作无流（调用方降级）。"""
    assert video.pick_single({"v_voucher": "xxx"}) is None
    assert video.pick_single(None) is None
    assert video.pick_single({"durl": []}) is None
    assert video.pick_single({"durl": [{"size": 1}]}) is None  # 无 url


def test_human_size_and_duration():
    assert video.human_size(512) == "512.0B"
    assert video.human_size(2048) == "2.0KB"
    assert video.human_size(45 * 1024 * 1024) == "45.0MB"
    assert video.format_duration(125120) == "02:05"
    assert video.format_duration(3_600_000) == "1:00:00"
    assert video.format_duration(0) == "-"


def test_cache_path_and_freshness(tmp_path):
    path = video.cache_path(str(tmp_path), "BV1zq836rEbk", 2)
    assert path.endswith("BV1zq836rEbk_p2.mp4")

    assert video.is_fresh(path, 60) is False  # 文件不存在

    with open(path, "wb") as fh:
        fh.write(b"x")
    now = os.path.getmtime(path) + 10
    assert video.is_fresh(path, 60, now=now) is True
    assert video.is_fresh(path, 60, now=now + 3600) is False  # 超过 TTL
    assert video.is_fresh(path, 0, now=now + 10 ** 6) is True  # TTL<=0 永不过期


def test_pick_stale_by_ttl_and_capacity():
    entries = [
        ("old.mp4", 1000.0, 10),
        ("mid.mp4", 2000.0, 10),
        ("new.mp4", 3000.0, 10),
    ]
    now = 3000.0
    # 只按 TTL：1000/2000 均已过期（TTL=10 分钟 = 600s）
    assert video.pick_stale(entries, 10, 0, now=now) == ["old.mp4", "mid.mp4"]
    # 只按容量：上限 25B → 淘汰最旧的
    assert video.pick_stale(entries, 0, 25, now=now) == ["old.mp4"]
    # 都不限制 → 不删
    assert video.pick_stale(entries, 0, 0, now=now) == []


# ==================== 流式下载（假 stream，不联网） ====================


class _FakeStreamResp:
    def __init__(self, chunks, status=200):
        self._chunks = chunks
        self.status_code = status

    async def aiter_content(self, chunk_size=0):
        for chunk in self._chunks:
            yield chunk


class _FakeStream:
    """替身：模拟 ``async with api.stream(...) as resp``。"""

    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


def _api_with_stream(chunks_by_url: dict, status: int = 200) -> BilibiliAPI:
    api = BilibiliAPI()

    def fake_stream(method, url, headers=None, timeout=60):
        return _FakeStream(_FakeStreamResp(chunks_by_url.get(url, []), status=status))

    api.stream = fake_stream
    return api


async def test_download_durl_concats_segments_and_replaces_part(tmp_path):
    dest = str(tmp_path / "v.mp4")
    api = _api_with_stream({"http://a": [b"ab", b"cd"], "http://b": [b"ef"]})

    await api.download_durl([{"url": "http://a"}, {"url": "http://b"}], dest, timeout=5)

    with open(dest, "rb") as fh:
        assert fh.read() == b"abcdef"
    assert not os.path.exists(dest + ".part")  # 原子改名，无残留


async def test_download_durl_cleans_part_on_failure(tmp_path):
    dest = str(tmp_path / "v.mp4")
    api = _api_with_stream({"http://a": [b"x"]}, status=500)

    try:
        await api.download_durl([{"url": "http://a"}], dest, timeout=5)
        raise AssertionError("应抛出异常")
    except RuntimeError as exc:
        assert "HTTP 500" in str(exc)

    assert not os.path.exists(dest)
    assert not os.path.exists(dest + ".part")  # 半成品被清理
