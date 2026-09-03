"""OpenAI 兼容 STT Provider。

通过 ``POST /v1/audio/transcriptions`` 上传音频，返回识别文本。
"""

from __future__ import annotations

from pathlib import Path

import aiohttp

from .base import BaseProvider


class OpenAIWhisperSTTProvider(BaseProvider):
    name = "openai_stt"
    capabilities = ("stt",)

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.api_key = config.get("api_key", "")
        self.api_base = (
            config.get("api_base", "") or "https://api.openai.com"
        ).rstrip("/")
        self.model = config.get("model", "whisper-1")
        self.timeout = int(config.get("timeout", 120) or 120)

    def _endpoint(self) -> str:
        base = self.api_base.rstrip("/")
        if base.endswith("/audio/transcriptions"):
            return base
        if base.endswith("/v1"):
            return base + "/audio/transcriptions"
        return base + "/v1/audio/transcriptions"

    async def transcribe(self, audio_file: str | Path, *, model: str | None = None) -> str:
        if not self.api_key:
            raise ValueError("STT API 密钥未配置")
        audio = Path(audio_file)
        if not audio.is_file():
            raise ValueError(f"音频文件不存在: {audio}")
        filename = audio.name or "audio.mp3"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        form = aiohttp.FormData()
        form.add_field("model", model or self.model)
        form.add_field(
            "file",
            audio.open("rb"),
            filename=filename,
            content_type="application/octet-stream",
        )
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._endpoint(),
                data=form,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise ValueError(f"STT 请求失败 HTTP {resp.status}: {body}")
                result = await resp.json()
        return str(result.get("text") or "")
