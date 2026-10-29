"""首次启动生成**完整** `.env` 的契约测试。

三件事各有守门：

1. 模板与 `Settings` 字段**严格一一对应**（加了字段不登记、登记了没人读，都红）；
2. `.env.example` 就是模板渲染出来的（不许手改它造成与代码漂移）；
3. `ensure_env_file` 的两条路径：文件不存在 → 生成完整配置；文件已存在 → 只补空令牌，
   绝不追加/覆盖用户自己写的键。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.env_template import TEMPLATE_KEYS, render_example
from app.core.settings import ROOT_DIR, Settings
from app.core.webui_token import ensure_env_file, ensure_webui_token


def _settings(tmp_path: Path, token: str = "") -> Settings:
    return Settings(
        db_path=str(tmp_path / "test.db"),
        module_dir=str(tmp_path / "data" / "module"),
        log_dir=str(tmp_path / "logs"),
        webui_token=token,
    )


def _assignment_keys(text: str) -> list[str]:
    """文本里的赋值行键名（按出现顺序）。"""
    return re.findall(r"^([A-Z][A-Z0-9_]*)=", text, re.MULTILINE)


# ==================== 模板本身的守门 ====================

def test_example_leaves_secrets_blank():
    """模板里的令牌键必须留空，别把示例值当默认口令。"""
    example = render_example()
    assert "\nWEBUI_TOKEN=\n" in example


# ==================== 首启生成完整 .env ====================

def _env_path_text(value: Path | str) -> str:
    """与 `env_template._value_text` 同口径：项目内路径写相对项目根，项目外写绝对。"""
    path = Path(value)
    try:
        return path.relative_to(ROOT_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def test_creates_full_env_when_absent(tmp_path):
    env = tmp_path / ".env"
    settings = _settings(tmp_path)

    state = ensure_env_file(settings, env_path=env)

    assert state.created is True
    assert state.generated is True
    assert state.persisted is True
    text = env.read_text(encoding="utf-8")
    # 所有登记键都写进去了，且每个只出现一次
    assert _assignment_keys(text) == list(TEMPLATE_KEYS)
    assert f"WEBUI_TOKEN={state.token}" in text
    # 生效值（不是模板示例值）：这里的 db_path 是临时目录
    # 注意：测试环境的 tmp_path 可能落在项目根内（沙箱把系统临时目录设为不可写时），
    # 此时生成器按设计写相对路径——断言必须跟同一套规则走，不能写死绝对路径。
    assert f"DB_PATH={_env_path_text(tmp_path / 'test.db')}" in text
    # 就地更新 settings，WebUI 本次进程立即可用
    assert settings.webui_token == state.token


def test_generated_env_has_comments_and_no_tmp_leftover(tmp_path):
    env = tmp_path / ".env"

    ensure_env_file(_settings(tmp_path), env_path=env)

    text = env.read_text(encoding="utf-8")
    assert "# QQBot Next 配置" in text
    assert "环境变量优先于本文件" in text
    assert not list(tmp_path.glob("*.tmp")), "原子写入不能留下临时文件"


def test_generated_env_round_trips_back_into_settings(tmp_path, monkeypatch):
    """生成的 `.env` 必须能被 pydantic-settings 原样读回（路径相对项目根目录）。"""
    # 环境变量优先于 .env：先把同名环境变量清掉，才是在测文件本身
    for key in TEMPLATE_KEYS:
        monkeypatch.delenv(key, raising=False)
    # 路径以「相对项目根目录」写入，读回时按 CWD 解析 → 站到项目根再比对
    monkeypatch.chdir(ROOT_DIR)

    env = tmp_path / ".env"
    original = Settings(
        _env_file=None,
        webui_host="0.0.0.0",
        webui_port=9300,
        webui_auto_open=True,
        ws_connect_timeout=15,
        ws_ping_interval=20,
        ws_ping_timeout=5,
        webui_token="",
    )
    ensure_env_file(original, env_path=env)

    reloaded = Settings(_env_file=str(env))

    for name in ("webui_host", "webui_port", "webui_auto_open", "ws_connect_timeout",
                 "ws_ping_interval", "ws_ping_timeout"):
        assert getattr(reloaded, name) == getattr(original, name), name
    assert reloaded.webui_token == original.webui_token
    # 路径字段写成相对项目根目录的形式，读回来仍指向原位置
    assert reloaded.db_path.resolve() == original.db_path.resolve()
    assert reloaded.module_dir.resolve() == original.module_dir.resolve()


def test_existing_env_is_only_patched_for_token(tmp_path):
    """已有 `.env` → 只补空令牌，绝不追加新键、不改用户注释。"""
    env = tmp_path / ".env"
    original = "# 我自己的注释\nWEBUI_HOST=0.0.0.0\nWEBUI_TOKEN=\n"
    env.write_text(original, encoding="utf-8")

    state = ensure_env_file(_settings(tmp_path), env_path=env)

    text = env.read_text(encoding="utf-8")
    assert state.created is False
    assert state.generated is True
    assert text.startswith("# 我自己的注释\nWEBUI_HOST=0.0.0.0\n")
    assert f"WEBUI_TOKEN={state.token}" in text
    assert "PLUGINS_DIR" not in text, "已存在的文件不该被塞入新键"


def test_existing_token_is_never_overwritten(tmp_path):
    env = tmp_path / ".env"
    original = "WEBUI_TOKEN=hand-written\n"
    env.write_text(original, encoding="utf-8")

    state = ensure_env_file(_settings(tmp_path, token="hand-written"), env_path=env)

    assert state.generated is False
    assert env.read_text(encoding="utf-8") == original


def test_env_override_creates_file_but_leaves_token_blank(tmp_path, monkeypatch):
    """环境变量 WEBUI_TOKEN 优先：文件照建，令牌行留空且如实报告未固定。"""
    env = tmp_path / ".env"
    monkeypatch.setenv("WEBUI_TOKEN", "")

    state = ensure_env_file(_settings(tmp_path), env_path=env)

    text = env.read_text(encoding="utf-8")
    assert state.created is True
    assert state.persisted is False, "环境变量覆盖时不能谎报已持久化"
    assert "\nWEBUI_TOKEN=\n" in text, "读不到的值不该写进文件"
    assert _assignment_keys(text) == list(TEMPLATE_KEYS)


def test_write_failure_degrades_without_raising(tmp_path, monkeypatch):
    """写盘失败（只读目录等）不许让启动崩掉。"""
    import app.core.webui_token as mod

    env = tmp_path / ".env"
    settings = _settings(tmp_path)

    def _boom(*args, **kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(mod, "_atomic_write", _boom)
    state = ensure_env_file(settings, env_path=env)

    assert state.created is False
    assert state.persisted is False
    assert settings.webui_token == state.token, "即使没落盘，本次进程也要带上令牌"
    assert not env.exists()


def test_absent_file_goes_through_generator_not_token_patch(tmp_path, monkeypatch):
    """首启走生成器：不再只写一行 WEBUI_TOKEN。"""
    import app.core.webui_token as mod

    env = tmp_path / ".env"
    calls: list[str] = []
    monkeypatch.setattr(mod, "render_env", lambda *a, **k: calls.append("render") or "WEBUI_TOKEN=\n")

    ensure_env_file(_settings(tmp_path), env_path=env)

    assert calls == ["render"]


# ==================== 旧入口仍可用（`ensure_webui_token`） ====================

def test_token_only_entry_still_works(tmp_path):
    env = tmp_path / ".env"
    env.write_text("WEBUI_TOKEN=\n", encoding="utf-8")

    state = ensure_webui_token(_settings(tmp_path), env_path=env)

    assert state.generated is True
    assert f"WEBUI_TOKEN={state.token}" in env.read_text(encoding="utf-8")
