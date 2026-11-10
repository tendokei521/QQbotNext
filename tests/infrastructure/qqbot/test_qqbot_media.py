"""富媒体上传契约：URL 直传、本地分片上传、file_info 缓存 TTL。"""

from __future__ import annotations

import hashlib

import pytest

from app.infrastructure.qqbot.media import MediaError, MediaUploader


class FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes]] = []

    def put(self, url: str, data: bytes):
        self.puts.append((url, data))
        return FakeResponse()


class FakeMediaAPI:
    def __init__(self) -> None:
        self._session = FakeSession()
        self.url_uploads: list[dict] = []
        self.prepares: list[dict] = []
        self.part_finishes: list[dict] = []
        self.merges: list[dict] = []
        self.file_info = "FILEINFO"

    async def upload_file(self, scene: str, target: str, payload: dict) -> dict:
        if "upload_id" in payload:
            self.merges.append(dict(payload))
        else:
            self.url_uploads.append(dict(payload))
        return {"file_info": self.file_info, "ttl": 300}

    async def upload_prepare(self, scene: str, target: str, payload: dict) -> dict:
        self.prepares.append(dict(payload))
        size = int(payload["file_size"])
        block = 4
        parts = []
        for index, start in enumerate(range(0, size, block)):
            parts.append({
                "index": index,
                "presigned_url": f"https://upload.example/{index}",
                "block_size": str(min(block, size - start)),
            })
        return {"upload_id": "UP1", "block_size": str(block), "parts": parts}

    async def upload_part_finish(self, scene: str, target: str, payload: dict) -> dict:
        self.part_finishes.append(dict(payload))
        return {}


class FakeSegment:
    def __init__(self, seg_type: str, data: dict) -> None:
        self.type = seg_type
        self.data = data


@pytest.mark.asyncio
async def test_url_upload_passes_url_and_caches_file_info():
    api = FakeMediaAPI()
    uploader = MediaUploader(api)  # type: ignore[arg-type]
    seg = FakeSegment("image", {"file": "https://example.com/a.png"})

    first = await uploader.resolve("group", "G1", seg)
    second = await uploader.resolve("group", "G1", seg)

    assert first == second == "FILEINFO"
    assert len(api.url_uploads) == 1, "TTL 内应复用 file_info，不重复上传"
    assert api.url_uploads[0]["url"] == "https://example.com/a.png"
    assert api.url_uploads[0]["file_type"] == 1
    assert api.url_uploads[0]["srv_send_msg"] is False


@pytest.mark.asyncio
async def test_local_file_uses_chunked_upload(tmp_path):
    api = FakeMediaAPI()
    uploader = MediaUploader(api)  # type: ignore[arg-type]
    path = tmp_path / "pic.png"
    path.write_bytes(b"0123456789")

    seg = FakeSegment("image", {"file": str(path)})
    file_info = await uploader.resolve("group", "G1", seg)

    assert file_info == "FILEINFO"
    prepare = api.prepares[0]
    assert prepare["file_size"] == "10"
    assert prepare["md5"] == hashlib.md5(b"0123456789").hexdigest()
    assert prepare["sha1"] == hashlib.sha1(b"0123456789").hexdigest()
    # 3 片：4+4+2；每片都真的 PUT 且各通知一次
    assert len(api._session.puts) == 3
    assert [p["part_index"] for p in api.part_finishes] == [0, 1, 2]
    assert api.merges[0]["upload_id"] == "UP1"
    assert api.merges[0]["srv_send_msg"] is False


@pytest.mark.asyncio
async def test_missing_local_file_raises_media_error():
    uploader = MediaUploader(FakeMediaAPI())  # type: ignore[arg-type]
    with pytest.raises(MediaError):
        await uploader.resolve("group", "G1", FakeSegment("image", {"file": "no-such-file.png"}))


@pytest.mark.asyncio
async def test_base64_data_url_is_supported():
    uploader = MediaUploader(FakeMediaAPI())  # type: ignore[arg-type]
    seg = FakeSegment("image", {"file": "data:image/png;base64,AAAA"})
    assert await uploader.resolve("group", "G1", seg) == "FILEINFO"
