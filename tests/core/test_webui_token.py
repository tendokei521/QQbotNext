"""WebUI 令牌自动生成与 `.env` 回写的单元测试。

重点覆盖三件事：
1. 空令牌 → 生成 + 写盘 + 就地更新 settings（WebUI 靠后者立即生效）；
2. 已有令牌 → 原样复用，绝不改写 `.env`（用户手填的值不能被覆盖）；
3. `.env` 其它内容（注释、其它键、CRLF 换行）在回写后保持不变。
"""

import re

from app.core.settings import Settings
from app.core.webui_token import (
    TOKEN_BYTES,
    access_url,
    ensure_webui_token,
    local_url,
    resolve_env_path,
)


def _settings(tmp_path, token: str = "") -> Settings:
    return Settings(
        db_path=str(tmp_path / "test.db"),
        module_dir=str(tmp_path / "module"),
        log_dir=str(tmp_path / "logs"),
        webui_token=token,
    )


def test_generates_token_when_empty(tmp_path):
    env = tmp_path / ".env"
    env.write_text("WEBUI_TOKEN=\n", encoding="utf-8")
    settings = _settings(tmp_path)

    state = ensure_webui_token(settings, env_path=env)

    assert state.generated is True
    assert state.persisted is True
    assert state.token
    # 就地更新 settings 是 WebUI 本次进程立即生效的前提
    assert settings.webui_token == state.token
    assert f"WEBUI_TOKEN={state.token}" in env.read_text(encoding="utf-8")


def test_generated_token_is_unpredictable_and_long_enough(tmp_path):
    tokens = {ensure_webui_token(_settings(tmp_path), env_path=tmp_path / ".env").token for _ in range(5)}
    assert len(tokens) == 5, "每次生成都应是新令牌"
    # token_urlsafe(32) → 43 字符；给下限断言即可，不锁死具体长度
    assert all(len(t) >= 32 for t in tokens)


def test_keeps_existing_token_and_never_rewrites_file(tmp_path):
    env = tmp_path / ".env"
    original = "WEBUI_TOKEN=my-own-token\nOTHER=1\n"
    env.write_text(original, encoding="utf-8")
    settings = _settings(tmp_path, token="my-own-token")

    state = ensure_webui_token(settings, env_path=env)

    assert state.generated is False
    assert state.token == "my-own-token"
    assert settings.webui_token == "my-own-token"
    # 已有令牌时不应产生任何写操作（mtime 与内容都不变）
    assert env.read_text(encoding="utf-8") == original


def test_preserves_comments_and_other_keys(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# ===== WebUI =====\n"
        "# 访问 WebUI 所需的 Bearer Token；留空则自动生成\n"
        "WEBUI_HOST=127.0.0.1\n"
        "WEBUI_TOKEN=\n"
        "DEBUG=false\n",
        encoding="utf-8",
    )

    state = ensure_webui_token(_settings(tmp_path), env_path=env)
    text = env.read_text(encoding="utf-8")

    assert f"WEBUI_TOKEN={state.token}" in text
    # 注释、其它键必须原样保留
    assert "# ===== WebUI =====" in text
    assert "# 访问 WebUI 所需的 Bearer Token；留空则自动生成" in text
    assert "WEBUI_HOST=127.0.0.1" in text
    assert "DEBUG=false" in text
    # 只有一处 WEBUI_TOKEN 赋值行，没有重复追加
    assert len(re.findall(r"^WEBUI_TOKEN=", text, re.MULTILINE)) == 1


def test_preserves_crlf_newlines(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(b"DEBUG=false\r\nWEBUI_TOKEN=\r\n")

    ensure_webui_token(_settings(tmp_path), env_path=env)

    raw = env.read_bytes()
    assert b"WEBUI_TOKEN=" in raw
    assert b"\r\n" in raw, "CRLF 文件的换行风格应保留"
    assert b"\n" not in raw.replace(b"\r\n", b""), "不应混入裸 LF"


def test_appends_key_when_missing(tmp_path):
    env = tmp_path / ".env"
    env.write_text("DEBUG=false", encoding="utf-8")  # 无结尾换行、无 WEBUI_TOKEN

    state = ensure_webui_token(_settings(tmp_path), env_path=env)
    text = env.read_text(encoding="utf-8")

    assert f"WEBUI_TOKEN={state.token}" in text
    assert text.startswith("DEBUG=false")
    # 追加时不能把原有最后一行粘在一起
    assert "DEBUG=falseWEBUI_TOKEN" not in text


def test_creates_env_file_when_absent(tmp_path):
    env = tmp_path / ".env"
    assert not env.exists()

    state = ensure_webui_token(_settings(tmp_path), env_path=env)

    assert env.exists()
    assert f"WEBUI_TOKEN={state.token}" in env.read_text(encoding="utf-8")


def test_only_the_assignment_line_is_replaced(tmp_path):
    """注释里提到 WEBUI_TOKEN 的行不能被当成赋值行改掉。"""
    env = tmp_path / ".env"
    env.write_text(
        "# 例如 WEBUI_TOKEN=abc 这样写\n"
        "WEBUI_TOKEN=old\n",
        encoding="utf-8",
    )

    state = ensure_webui_token(_settings(tmp_path), env_path=env)
    text = env.read_text(encoding="utf-8")

    assert "# 例如 WEBUI_TOKEN=abc 这样写" in text
    assert f"WEBUI_TOKEN={state.token}" in text


def test_readonly_env_degrades_without_raising(tmp_path, monkeypatch):
    """写盘失败不许让启动崩掉：降级为「仅本进程有效」并如实上报。"""
    import app.core.webui_token as mod

    env = tmp_path / ".env"
    settings = _settings(tmp_path)

    def _boom(*args, **kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(mod, "_write_token", _boom)
    state = ensure_webui_token(settings, env_path=env)

    assert state.generated is True
    assert state.persisted is False
    # 即使没落盘，本次进程也必须已经带上令牌
    assert settings.webui_token == state.token


def test_resolve_env_path_prefers_cwd_then_repo_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # 当前目录没有 .env → 回退仓库根
    from app.core.settings import ROOT_DIR

    assert resolve_env_path() == ROOT_DIR / ".env"

    (tmp_path / ".env").write_text("", encoding="utf-8")
    assert resolve_env_path() == tmp_path / ".env"


def test_env_override_reports_not_persisted(tmp_path, monkeypatch):
    """环境变量 WEBUI_TOKEN 优先于 .env：显式设空串时写盘无效，必须如实报告。

    否则用户会以为令牌已固定，实际每次重启都变（登录密码跟着变）。
    """
    env = tmp_path / ".env"
    env.write_text("WEBUI_TOKEN=\n", encoding="utf-8")
    monkeypatch.setenv("WEBUI_TOKEN", "")

    state = ensure_webui_token(_settings(tmp_path), env_path=env)

    assert state.generated is True
    assert state.persisted is False, "环境变量覆盖时不能谎报已持久化"
    assert env.read_text(encoding="utf-8") == "WEBUI_TOKEN=\n", "既然读不到，就不该改写文件"


def test_token_bytes_constant_sane():
    # 至少 128 bit：明文静态令牌不该太短
    assert TOKEN_BYTES * 8 >= 128


def test_local_url_falls_back_to_loopback():
    """0.0.0.0 / :: 是监听地址、无法直接访问，给出的链接必须是回环地址。"""
    assert local_url("0.0.0.0", 9200) == "http://127.0.0.1:9200"
    assert local_url("::", 9200) == "http://127.0.0.1:9200"
    assert local_url("", 9200) == "http://127.0.0.1:9200"
    assert local_url("192.168.1.5", 9200) == "http://192.168.1.5:9200"


def test_access_url_carries_accesstoken():
    assert access_url("127.0.0.1", 9200, "abc123") == "http://127.0.0.1:9200/?accesstoken=abc123"


def test_access_url_encodes_unsafe_characters():
    """用户手填的口令可能含 URL 特殊字符，必须转义，否则链接会被截断。"""
    from urllib.parse import parse_qs, urlparse

    url = access_url("127.0.0.1", 9200, "a b&c=d#e")
    assert " " not in url and "#" not in url
    assert parse_qs(urlparse(url).query)["accesstoken"] == ["a b&c=d#e"]