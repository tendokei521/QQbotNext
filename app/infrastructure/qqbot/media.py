"""官方机器人出站富媒体：本地/远端资源 → ``file_info`` → ``msg_type=7``。

官方要求先上传换 ``file_info`` 再发消息（``media.file_info``）：

- **URL 上传**：资源已在公网（http/https）→ 交给平台下载转存，一步完成；
- **分片上传**：本地文件 → ``upload_prepare`` 拿 ``upload_id`` + 预签名 URL →
  逐片 ``PUT`` → 每片 ``upload_part_finish`` → 带 ``upload_id`` 调 ``files`` 合并；
- ``file_info`` 有 TTL，按「场景 + 目标 + 内容摘要」缓存，过期前复用。

单聊与群聊的上传接口不互通，所以缓存键必须带 scene/target。
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import time
from pathlib import Path
from typing import Any

from app.core.logger import logger

from .api import QqBotAPI

#: 消息段类型 → 官方 file_type（1 图片 / 2 视频 / 3 语音 / 4 文件）
FILE_TYPES: dict[str, int] = {
    "image": 1,
    "video": 2,
    "record": 3,
    "voice": 3,
    "file": 4,
}

#: 官方硬限制 200MB
MAX_FILE_BYTES = 200 * 1024 * 1024
#: 预上传 md5_10m 的取样长度（官方口径：前 10002432 字节）
MD5_10M_BYTES = 10002432
#: file_info 提前过期的安全余量（秒）
TTL_MARGIN = 30.0

_DATA_URL_RE = re.compile(r"^data:(?P<mime>[a-z0-9.+-]+/[a-z0-9.+-]+);base64,(?P<payload>.+)$", re.IGNORECASE | re.DOTALL)


class MediaError(RuntimeError):
    """富媒体上传失败（不可用/超限/格式不支持）。"""


class MediaUploader:
    def __init__(self, api: QqBotAPI, *, log: Any = None) -> None:
        self.api = api
        self.log = log or logger
        #: (scene, target, file_type, digest) → (file_info, expires_at)
        self._cache: dict[tuple[str, str, int, str], tuple[str, float]] = {}

    @property
    def _session(self):
        """HTTP 会话由连接在 open() 时创建，这里每次现取（不能缓存 None）。"""
        return getattr(self.api, "_session", None)

    async def resolve(self, scene: str, target: str, segment: Any) -> str:
        """把一个 image/record/video/file 段上传成 ``file_info``（带缓存）。"""
        data = getattr(segment, "data", None) or {}
        seg_type = str(getattr(segment, "type", "") or "")
        file_type = FILE_TYPES.get(seg_type)
        if file_type is None:
            raise MediaError(f"不支持的媒体段类型: {seg_type}")

        source = str(data.get("url") or data.get("file") or "").strip()
        if not source:
            raise MediaError("媒体段缺少 file/url，无法上传")

        if source.startswith(("http://", "https://")):
            digest = f"url:{source}"
            return await self._cached(scene, target, file_type, digest, lambda: self._upload_url(
                scene, target, source, file_type
            ))

        payload, filename = self._read_local(source, seg_type)
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        return await self._cached(scene, target, file_type, digest, lambda: self._upload_bytes(
            scene, target, payload, filename, file_type
        ))

    async def _cached(self, scene: str, target: str, file_type: int, digest: str, factory) -> str:
        key = (scene, str(target), file_type, digest)
        now = time.time()
        hit = self._cache.get(key)
        if hit and hit[1] > now:
            return hit[0]
        file_info, ttl = await factory()
        # ttl=0 表示长期有效；否则留一点安全余量
        expires = now + (float(ttl) - TTL_MARGIN if ttl and ttl > TTL_MARGIN else 300.0)
        if ttl == 0:
            expires = now + 86400.0
        self._cache[key] = (file_info, expires)
        return file_info

    async def _upload_url(self, scene: str, target: str, url: str, file_type: int) -> tuple[str, int]:
        data = await self.api.upload_file(scene, target, {
            "file_type": file_type,
            "url": url,
            "srv_send_msg": False,
        })
        file_info = str((data or {}).get("file_info") or "")
        if not file_info:
            raise MediaError("上传接口没有返回 file_info")
        return file_info, int((data or {}).get("ttl") or 0)

    async def _upload_bytes(
        self, scene: str, target: str, payload: bytes, filename: str, file_type: int
    ) -> tuple[str, int]:
        if not payload:
            raise MediaError("文件内容为空")
        if len(payload) > MAX_FILE_BYTES:
            raise MediaError(f"文件超过官方 200MB 上限（{len(payload)} 字节）")
        md5_10m = hashlib.md5(payload[:MD5_10M_BYTES]).hexdigest()
        prepare = await self.api.upload_prepare(scene, target, {
            "file_type": file_type,
            "file_size": str(len(payload)),
            "file_name": filename,
            "md5": hashlib.md5(payload).hexdigest(),
            "sha1": hashlib.sha1(payload).hexdigest(),
            "md5_10m": md5_10m,
        })
        upload_id = str((prepare or {}).get("upload_id") or "")
        parts = (prepare or {}).get("parts") or []
        if not upload_id or not parts:
            raise MediaError("预上传响应缺少 upload_id/parts")

        session = self._session
        if session is None:
            raise MediaError("HTTP 会话尚未建立")
        for part in parts:
            index = int((part or {}).get("index") or 0)
            url = str((part or {}).get("presigned_url") or "")
            size = int((part or {}).get("block_size") or 0) or len(payload) // max(1, len(parts))
            start = index * size
            chunk = payload[start:start + size]
            if not url:
                raise MediaError(f"分片 {index} 缺少预签名 URL")
            async with session.put(url, data=chunk) as resp:
                if resp.status >= 400:
                    raise MediaError(f"分片 {index} 上传失败（HTTP {resp.status}）")
            await self.api.upload_part_finish(scene, target, {
                "upload_id": upload_id,
                "part_index": index,
                "block_size": str(len(chunk)),
                "md5": hashlib.md5(chunk).hexdigest(),
            })

        data = await self.api.upload_file(scene, target, {
            "file_type": file_type,
            "srv_send_msg": False,
            "file_name": filename,
            "upload_id": upload_id,
        })
        file_info = str((data or {}).get("file_info") or "")
        if not file_info:
            raise MediaError("合并上传没有返回 file_info")
        return file_info, int((data or {}).get("ttl") or 0)

    def _read_local(self, source: str, seg_type: str) -> tuple[bytes, str]:
        """把本地指向（路径 / file:// / base64:// / data:）读成字节。"""
        text = source.strip()
        if text.startswith("base64://"):
            return self._decode_b64(text[len("base64://"):], seg_type), self._default_name(seg_type)
        match = _DATA_URL_RE.match(text)
        if match:
            return self._decode_b64(match.group("payload"), seg_type), self._default_name(seg_type)
        if text.startswith("file://"):
            text = text[len("file://"):]
        path = Path(os.path.expanduser(text))
        if not path.is_file():
            raise MediaError(f"本地文件不存在: {path}")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise MediaError(f"文件超过官方 200MB 上限: {path.name}")
        return path.read_bytes(), path.name

    @staticmethod
    def _decode_b64(payload: str, seg_type: str) -> bytes:
        try:
            return base64.b64decode(payload, validate=False)
        except Exception as e:  # noqa: BLE001
            raise MediaError(f"base64 媒体解码失败: {e}") from e

    @staticmethod
    def _default_name(seg_type: str) -> str:
        ext = {"image": "png", "record": "silk", "voice": "silk", "video": "mp4", "file": "bin"}.get(seg_type, "bin")
        return f"media.{ext}"
