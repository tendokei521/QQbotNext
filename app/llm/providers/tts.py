"""OpenAI 兼容 TTS Provider。

通过 ``POST /v1/audio/speech`` 合成语音，返回音频 bytes。
"""

from __future__ import annotations

import aiohttp

from .base import BaseProvider


class OpenAITTSProvider(BaseProvider):
    name = "openai_tts"
    capabilities = ("tts",)

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.api_key = config.get("api_key", "")
        self.api_base = (
            config.get("api_base", "") or "https://api.openai.com"
        ).rstrip("/")
        self.model = config.get("model", "tts-1")
        self.voice = config.get("voice", "alloy")
        self.timeout = int(config.get("timeout", 60) or 60)

    def _endpoint(self) -> str:
        base = self.api_base.rstrip("/")
        if base.endswith("/audio/speech"):
            return base
        if base.endswith("/v1"):
            return base + "/audio/speech"
        return base + "/v1/audio/speech"

    async def synthesize(self, text: str, *, voice: str | None = None) -> tuple[bytes, str]:
        if not self.api_key:
            raise ValueError("TTS API 密钥未配置")
        payload = {
            "model": self.model,
            "input": text,
            "voice": voice or self.voice,
            "response_format": "mp3",
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._endpoint(),
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise ValueError(f"TTS 请求失败 HTTP {resp.status}: {body}")
                audio = await resp.read()
        return audio, voice or self.voice
