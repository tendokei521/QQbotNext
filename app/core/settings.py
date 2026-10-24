"""全局配置中心：基于 pydantic-settings，从 .env / 环境变量 / 默认值加载。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """应用全局配置。从 .env / 环境变量读取（变量名 = 字段名大写，如 WEBUI_HOST，见 `.env.example`）。

    字段清单与 `.env` 键一一对应，唯一来源是 `app/core/env_template.py`：
    加字段必须同时在那张表里登记，否则 `tests/core/test_env_template.py` 会红。

    历史包袱：`debug` / `scheduler_interval` 两个字段**从未被任何代码读取**
    （日志级别写死在 `app/core/logger.py`，定时任务按 job 自己的时间点 sleep），
    已随"首启生成完整 .env"一并删除，避免 `.env` 里堆着写了没用的开关。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # WebUI
    webui_host: str = "127.0.0.1"
    webui_port: int = 9200
    webui_token: str = ""
    #: 启动后自动用浏览器打开 WebUI（本地启动脚本把它置 true）。
    #: 打开的是带 `?accesstoken=` 的一键登录链接，进入后立刻换成会话 cookie。
    webui_auto_open: bool = False

    # 数据 / 日志
    db_path: Path = Field(default=ROOT_DIR / "data" / "app.db")
    log_dir: Path = Field(default=ROOT_DIR / "logs")

    # Bot 连接
    ws_connect_timeout: int = 30
    ws_ping_interval: int = 30
    ws_ping_timeout: int = 10

    # 插件**运行时数据**根目录（configs / data / plugins(zip) / uninstalled_modules.json）。
    # 业务插件的**代码**不在这里，见下面的 `plugins_dir`（默认 app/plugins）。
    module_dir: Path = Field(default=ROOT_DIR / "module")

    #: 业务插件代码目录。默认随仓库走（`app/plugins`）；测试与特殊部署可覆盖。
    plugins_dir: Path = Field(default=ROOT_DIR / "app" / "plugins")

    @property
    def zip_plugins_dir(self) -> Path:
        """外部 zip 插件安装目录（`<module_dir>/plugins`，用户安装的，不进仓库）。"""
        return self.module_dir / "plugins"

    @property
    def uninstalled_modules_file(self) -> Path:
        """软卸载模块状态文件（module/uninstalled_modules.json）。"""
        return self.module_dir / "uninstalled_modules.json"

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
