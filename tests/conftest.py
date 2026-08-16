"""测试公共夹具：临时目录 / 容器 / SQLite。"""

import asyncio
import os
from pathlib import Path

import pytest

from app.bootstrap import build_container
from app.core.settings import Settings
from app.infrastructure.config.config_service import ConfigService
from app.infrastructure.persistence.database import Database


@pytest.fixture(autouse=True)
def _isolate_llm_data(tmp_path, monkeypatch):
    """把 LLM 数据目录（定时任务/历史/主动状态）隔离到临时目录。

    防止测试进程把假 runtime（bot_id=1/5/99 等）的任务/历史文件写进
    真实 data/llm，污染生产数据与日志。
    """
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm_data"))
    yield


@pytest.fixture
def settings(tmp_path):
    return Settings(
        debug=True,
        db_path=str(tmp_path / "test.db"),
        module_dir=str(tmp_path / "module"),
        log_dir=str(tmp_path / "logs"),
        webui_port=19999,
        # 显式清空鉴权 token：覆盖 .env/环境变量，保证测试不因用户本地配置 401
        webui_token="",
    )


@pytest.fixture
def project_root(tmp_path):
    """构造一个带 legacy JSON 配置的临时项目根目录。"""
    root = tmp_path / "project"
    (root / "webserver").mkdir(parents=True)
    (root / "webui").mkdir(parents=True)
    (root / "module" / "configs" / "demo").mkdir(parents=True)
    (root / "webserver" / "webconfig.json").write_text(
        '{"bots": [{"ws_url": "ws://127.0.0.1:1", "owner_id": 10001, "auto_connect": false}]}',
        encoding="utf-8",
    )
    (root / "webui" / "webui_config.json").write_text(
        '{"logs": {"visible_levels": ["info"], "max_lines": 10}, "single_service": {}}',
        encoding="utf-8",
    )
    (root / "module" / "configs" / "demo" / "config.json").write_text(
        '{"12345": {"key1": "v1", "num": 42}}', encoding="utf-8"
    )
    (root / "module" / "configs" / "demo" / "authority.json").write_text(
        '{"12345": {"enabled": true, "group_mode": "whitelist", "group_list": ["1"], '
        '"user_mode": "blacklist", "user_list": []}}',
        encoding="utf-8",
    )
    return root


@pytest.fixture
async def container(settings):
    c = build_container(settings)
    db = c.get(Database)
    await db.connect()
    cfg = c.get(ConfigService)
    await cfg.init()
    yield c
    await db.close()


@pytest.fixture
async def db(container):
    return container.get(Database)


@pytest.fixture
async def config_service(container):
    return container.get(ConfigService)
