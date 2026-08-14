"""WebUI 请求/响应 DTO（Pydantic）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BotConfigItem(BaseModel):
    ws_url: str = ""
    owner_id: int | None = None
    auto_connect: bool = False


class BotsConfigUpdate(BaseModel):
    bots: list[BotConfigItem] = Field(default_factory=list)


class ModuleConfigUpdate(BaseModel):
    """模块配置更新（键值对）。"""

    model_config = {"extra": "allow"}

    @classmethod
    def from_dict(cls, data: dict) -> "ModuleConfigUpdate":
        return cls(**data)

    def to_dict(self) -> dict:
        return dict(self.model_dump())


class WebuiConfigUpdate(BaseModel):
    logs: dict | None = None


class LogsConfigUpdate(BaseModel):
    visible_levels: list[str] | None = None
    max_lines: int | None = None
    console_height: int | None = None


class SingleServiceUpdate(BaseModel):
    single_service: dict[str, bool] = Field(default_factory=dict)


class MultiGroupUpdate(BaseModel):
    multi_group: dict[str, Any] = Field(default_factory=dict)
