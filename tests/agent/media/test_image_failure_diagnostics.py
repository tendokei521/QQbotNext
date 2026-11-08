"""失败留痕契约：QQ 图片直链过期（HTTP 400）必须「可诊断、只吵一次、不再重试」。

线上事故（2026-11-08）：``warn.log`` 里只有一行 ``图片下载失败（HTTP 400）``，
既没有 URL 也没有响应体。定位后确认根因是历史里一条 ``rkey`` 已过期的 QQ 图片直链，
CDN 返回 ``{"retcode":-5503007,"retmsg":"download url has expired"}``；而
``backfill_history_media`` 每轮都重试它，于是每一轮刷一条无信息的 WARNING。

这里锁的是**对外行为契约**（不是实现快照）：

1. 永久失败（4xx / 链接过期）→ 抛 ``MediaUnavailable``，日志里带 host / HTTP 状态 /
   CDN retcode / 脱敏后的 URL / 调用方；
2. 失败后的段被打上 ``media_error`` 标记，**后续轮次不再下载**（不刷日志、不撞 CDN）；
3. 历史回填跳过已标记的死链。
"""

from __future__ import annotations

import pytest

from app.agent.media import image as image_mod
from app.core import diagnostics

EXPIRED_BODY = '{"retcode":-5503007,"retmsg":"download url has expired","retryflag":0}'
EXPIRED_URL = (
    "https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=EhQ598I2cn3vhE4p2CAFUs8"
    "&spec=0&rkey=CAMSMNseP6_z4fG2-W1BDP8OzJTn6ZtRMTgJvmBReVKxIbWBEyf2v3gQUx6dCcJ_OG4ojA"
)


class _FakeContent:
    async def iter_chunked(self, size):  # pragma: no cover - 非 200 分支不会读流
        for chunk in ():
            yield chunk


class _FakeResponse:
    def __init__(self, status: int, body: str = "", headers: dict | None = None) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}
        self.content = _FakeContent()

    async def text(self) -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):  # noqa: ANN001
        return False


class _FakeSession:
    status = 400
    body = EXPIRED_BODY

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):  # noqa: ANN001
        return False

    def get(self, url: str) -> _FakeResponse:
        return _FakeResponse(self.status, self.body)


class _Recorder:
    """收集 ``diag.log_failure`` 的调用（不落真日志，断言字段用）。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, log, code, message="", **kwargs):  # noqa: ANN001
        self.calls.append({"code": code, "message": message, **kwargs})
        return True

    def by_code(self, code: str) -> list[dict]:
        return [c for c in self.calls if c["code"] == code]


@pytest.mark.asyncio
async def test_expired_cdn_error_logs_diagnosable_fields(monkeypatch):
    import aiohttp

    monkeypatch.setattr(aiohttp, "ClientSession", _FakeSession)
    recorder = _Recorder()
    monkeypatch.setattr(image_mod.diag, "log_failure", recorder)

    with pytest.raises(image_mod.MediaUnavailable) as caught:
        await image_mod._download_bytes(EXPIRED_URL, max_bytes=1024 * 1024)

    error = caught.value
    assert error.reason == "url_expired"
    assert (error.http, error.retcode) == (400, -5503007)

    calls = recorder.by_code("image.download_http_error")
    assert len(calls) == 1
    fields = calls[0]["fields"]
    assert fields["http"] == 400
    assert fields["host"] == "multimedia.nt.qq.com.cn"
    assert fields["retcode"] == -5503007
    assert fields["retmsg"] == "download url has expired"
    assert fields["permanent"] is True
    # 签名参数必须脱敏：日志会被导出/贴给外部
    assert "rkey=***" in fields["url"]
    assert "CAMSMNseP6_z4fG2" not in fields["url"]
    assert "fileid=***" in fields["url"]


def test_cdn_error_body_is_parsed_into_code_and_text():
    assert image_mod._parse_cdn_error(EXPIRED_BODY) == (-5503007, "download url has expired")
    assert image_mod._parse_cdn_error("not json") == (0, "not json")
    assert image_mod._parse_cdn_error("") == (0, "")


@pytest.mark.asyncio
async def test_dead_url_is_marked_and_never_downloaded_again(monkeypatch):
    downloads: list[str] = []

    async def _dead(url, *, max_bytes, timeout=0):
        downloads.append(url)
        raise image_mod.MediaUnavailable(
            "url_expired",
            http=400,
            retcode=-5503007,
            detail="download url has expired",
            url=url,
        )

    monkeypatch.setattr(image_mod, "_download_bytes", _dead)
    monkeypatch.setattr(image_mod.diag, "log_failure", _Recorder())

    seg = {"type": "image", "data": {"file": "old.jpg", "url": EXPIRED_URL}}

    assert await image_mod.persist_segment(seg, bot_id="123", origin="backfill") is None
    marker = seg["data"]["media_error"]
    assert marker["reason"] == "url_expired"
    assert marker["http"] == 400
    assert downloads == [EXPIRED_URL]

    # 第二次（下一轮）不再下载：段上有标记，直接跳过
    assert await image_mod.persist_segment(seg, bot_id="123", origin="backfill") is None
    assert await image_mod.persist_images([seg], bot_id="123", origin="inbound") == 0
    assert downloads == [EXPIRED_URL], "死链被打标记后仍被重试"


@pytest.mark.asyncio
async def test_backfill_skips_segments_already_marked_dead(monkeypatch):
    downloads: list[str] = []

    async def _should_not_run(url, *, max_bytes, timeout=0):
        downloads.append(url)
        return b"", ""

    monkeypatch.setattr(image_mod, "_download_bytes", _should_not_run)

    entry = {
        "role": "user",
        "message_id": "123",
        "base": {
            "segments": [
                {
                    "type": "image",
                    "data": {
                        "file": "old.jpg",
                        "url": EXPIRED_URL,
                        "media_error": {"reason": "url_expired", "http": 400, "at": 1},
                    },
                }
            ]
        },
    }
    assert await image_mod.backfill_history_media([entry], bot_id="123") == 0
    assert downloads == []


def test_log_failure_is_structured_and_once_per_key():
    diagnostics.reset_failure_state()

    class _Log:
        def __init__(self) -> None:
            self.warnings: list[str] = []
            self.debugs: list[str] = []

        def warning(self, text: str) -> None:
            self.warnings.append(text)

        def debug(self, text: str, **_kwargs) -> None:
            self.debugs.append(text)

    log = _Log()
    kwargs = dict(
        fields={
            "http": 400,
            "host": "multimedia.nt.qq.com.cn",
            "retcode": -5503007,
            "retmsg": "download url has expired",
            "url": diagnostics.redact_url(EXPIRED_URL),
        },
        once=True,
        once_key="123:url_expired",
        account="123",
    )
    assert diagnostics.log_failure(log, "image.unavailable", "图片取不到", **kwargs) is True
    assert diagnostics.log_failure(log, "image.unavailable", "图片取不到", **kwargs) is False

    assert len(log.warnings) == 1, log.warnings
    line = log.warnings[0]
    assert "code=image.unavailable" in line
    assert "http=400" in line
    assert "retcode=-5503007" in line
    assert "host=multimedia.nt.qq.com.cn" in line
    assert "caller=" in line
    assert "rkey=***" in line
    assert diagnostics.failure_count(
        "image.unavailable", once_key="123:url_expired", account="123"
    ) == 2
