"""WebUI 请求/响应 DTO（Pydantic）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class BotConfigItem(BaseModel):
    ws_url: str = ""
    owner_id: Optional[int] = None
    auto_connect: bool = False


class BotsConfigUpdate(BaseModel):
    bots: List[BotConfigItem] = Field(default_factory=list)


class ModuleConfigUpdate(BaseModel):
    """模块配置更新（键值对）。"""

    model_config = {"extra": "allow"}

    @classmethod
    def from_dict(cls, data: dict) -> "ModuleConfigUpdate":
        return cls(**data)

    def to_dict(self) -> dict:
        return dict(self.model_dump())


class WebuiConfigUpdate(BaseModel):
    logs: Optional[dict] = None


class LogsConfigUpdate(BaseModel):
    visible_levels: Optional[List[str]] = None
    max_lines: Optional[int] = None
    console_height: Optional[int] = None


class SingleServiceUpdate(BaseModel):
    single_service: Dict[str, bool] = Field(default_factory=dict)


class MultiGroupUpdate(BaseModel):
    multi_group: Dict[str, Any] = Field(default_factory=dict)
