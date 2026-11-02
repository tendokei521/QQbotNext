"""内置模块自定义页的契约测试（页面文件层面，不起容器）。

锁三件事：
1. 带自定义页的内置模块都有自己的 `pages/index.html`；
2. 页面只依赖框架提供的公共资源（SDK/CSS），不引外部 CDN、不碰 `window.parent`；
3. 页面用声明式表单（`QQBOT.form`），不自己拼 fetch/保存逻辑。
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLUGINS = ROOT / "app" / "plugins"

#: 仍有自定义页的内置模块（其余模块已改回 schema 表单）
PAGE_MODULES = ("llm_trigger", "llm_context")


def _pages() -> dict[str, str]:
    return {m: (PLUGINS / m / "pages" / "index.html").read_text(encoding="utf-8") for m in PAGE_MODULES}


def test_each_core_module_ships_a_page():
    for module in PAGE_MODULES:
        page = PLUGINS / module / "pages" / "index.html"
        assert page.is_file(), f"{module} 缺少 pages/index.html"


def test_pages_only_depend_on_framework_assets():
    for module, html in _pages().items():
        assert "/api/plugin-sdk.js" in html, module
        assert "/api/plugin-sdk.css" in html, module
        # 不引外链（离线/内网也要能用）
        assert "http://" not in html and "https://" not in html, module
        # 不碰父窗口（保持插件页可独立打开）
        assert "window.parent" not in html, module
        assert "document.parent" not in html, module


def test_pages_use_declarative_form():
    for module, html in _pages().items():
        assert "QQBOT.form(" in html, module
        # 页面自己不发请求：保存/鉴权/防抖都在 SDK 里
        assert "fetch(" not in html, module
        assert "saveConfig(" not in html, module


def test_pages_reference_existing_config_keys():
    """页面里出现的 key 必须是该模块 schema 里真实存在的键（防拼错键静默失效）。"""
    import importlib

    for module, html in _pages().items():
        schema = importlib.import_module(f"app.plugins.{module}.config_schema").SCHEMA
        # 只校验形如 `key: 'xxx'` 的字段声明
        import re

        declared = set(re.findall(r"\bkey:\s*'([a-z_]+)'", html))
        assert declared, module
        missing = declared - set(schema)
        assert not missing, f"{module} 页面引用了不存在的键: {sorted(missing)}"
