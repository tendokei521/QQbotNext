"""全局配置中心：基于 pydantic-settings，从 .env / 环境变量 / 默认值加载。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """应用全局配置。所有环境变量以 `QQBOT_` 为前缀（见 .env.example）。"""

    model_config = SettingsConfigDict(
        env_prefix="QQBOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 运行模式
    debug: bool = False

    # WebUI
    webui_host: str = "127.0.0.1"
    webui_port: int = 9200
    webui_token: str = ""

    # 数据 / 日志
    db_path: Path = Field(default=ROOT_DIR / "data" / "app.db")
    log_dir: Path = Field(default=ROOT_DIR / "logs")

    # Bot 连接
    ws_connect_timeout: int = 30
    ws_ping_interval: int = 30
    ws_ping_timeout: int = 10

    # 调度器
    scheduler_interval: int = 30

    # 模块根目录（module/modules + module/configs + module/data）
    module_dir: Path = Field(default=ROOT_DIR / "module")

    @property
    def modules_dir(self) -> Path:
        """业务模块主体目录（module/modules）。"""
        return self.module_dir / "modules"

    @property
    def module_configs_dir(self) -> Path:
        """模块配置目录（module/configs）。"""
        return self.module_dir / "configs"

    @property
    def module_data_dir(self) -> Path:
        """模块数据目录（module/data）。"""
        return self.module_dir / "data"

    @property
    def project_root(self) -> Path:
        return ROOT_DIR


@lru_cache
def load_settings() -> Settings:
    """加载并缓存全局设置（进程内单例）。"""
    return Settings()


def get_settings() -> Settings:
    return load_settings()
