"""统一编目测试：双根扫描 / 同名冲突 / 清单缺失 / 来源解析 / 加载失败可见。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.application.module_install_service import ModuleInstallService
from app.infrastructure.config.config_service import ConfigService
from app.plugin.base import ServiceAccess
from app.plugin.catalog import ModuleCatalog
from app.plugin.registry import ModuleRegistry

MODULE_SRC = '''
from app.plugin import BaseModule

class Module(BaseModule):
    name = "编目测试"
    sign = "CatalogTest"
    description = ""
    permission = "member"

    async def on_load(self):
        self.loaded = True
'''

BAD_SRC = "raise RuntimeError('catalog boom')\n"


class _TaskStub:
    def cancel_owner(self, owner):
        return 0


def _manifest(name: str, version: str = "1.0.0", **extra) -> str:
    data = {"name": name, "display_name": f"显示 {name}", "version": version, "api_version": 1}
    data.update(extra)
    return json.dumps(data, ensure_ascii=False)


def _make_module(
    root: Path,
    name: str,
    *,
    version: str = "1.0.0",
    with_manifest: bool = True,
    main: str = MODULE_SRC,
    page: bool = False,
) -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "main.py").write_text(main, encoding="utf-8")
    (path / "__init__.py").write_text("", encoding="utf-8")
    if with_manifest:
        (path / "module.json").write_text(_manifest(name, version), encoding="utf-8")
    if page:
        (path / "pages").mkdir()
        (path / "pages" / "index.html").write_text("<html>catalog-page</html>", encoding="utf-8")
    return path


@pytest.fixture
def catalog_env(tmp_path):
    builtin = tmp_path / "builtin"
    installed = tmp_path / "installed"
    builtin.mkdir()
    installed.mkdir()
    state = ModuleInstallService(tmp_path / "uninstalled_modules.json")
    return ModuleCatalog(builtin_dir=builtin, installed_dir=installed, install_service=state), builtin, installed


# ==================== 扫描 ====================


def test_scan_merges_two_roots(catalog_env):
    catalog, builtin, installed = catalog_env
    _make_module(builtin, "alpha", version="1.1.0", page=True)
    _make_module(installed, "beta", version="2.0.0")

    entries = {entry.name: entry for entry in catalog.scan()}

    assert list(entries) == ["alpha", "beta"]
    assert entries["alpha"].source == "builtin"
    assert entries["alpha"].builtin is True
    assert entries["alpha"].version == "1.1.0"
    assert entries["alpha"].display_name == "显示 alpha"
    assert entries["alpha"].has_page is True
    assert entries["alpha"].installed_at is None
    assert entries["beta"].source == "installed"
    assert entries["beta"].builtin is False
    assert entries["beta"].installed_at is not None
    assert entries["beta"].has_page is False


def test_scan_skips_private_dirs(catalog_env, tmp_path):
    catalog, builtin, _installed = catalog_env
    _make_module(builtin, "visible")
    _make_module(builtin, "_private")
    _make_module(builtin, ".hidden")

    assert [entry.name for entry in catalog.scan()] == ["visible"]


def test_scan_builtin_missing_manifest_is_warning(catalog_env, caplog):
    catalog, builtin, _installed = catalog_env
    _make_module(builtin, "legacy", with_manifest=False)

    with caplog.at_level(logging.WARNING):
        entries = catalog.scan()

    entry = entries[0]
    assert entry.status == "manifest_missing"
    assert entry.version == "0.0.0"
    assert "module.json" in entry.error
    assert "兜底" in caplog.text


def test_scan_builtin_invalid_manifest_still_fallbacks(catalog_env):
    catalog, builtin, _installed = catalog_env
    path = _make_module(builtin, "broken")
    (path / "module.json").write_text("{not json", encoding="utf-8")

    entry = catalog.scan()[0]

    assert entry.status == "manifest_missing"
    assert entry.version == "0.0.0"
    assert "JSON" in entry.error


def test_scan_installed_missing_manifest_is_error(catalog_env):
    catalog, _builtin, installed = catalog_env
    _make_module(installed, "badinstall", with_manifest=False)

    entry = catalog.scan()[0]

    assert entry.status == "error"
    assert entry.builtin is False
    assert "module.json" in entry.error


def test_scan_installed_name_mismatch_is_error(catalog_env):
    catalog, _builtin, installed = catalog_env
    path = _make_module(installed, "dirname")
    (path / "module.json").write_text(_manifest("other_name"), encoding="utf-8")

    entry = catalog.scan()[0]

    assert entry.status == "error"
    assert "不一致" in entry.error


def test_scan_conflict_builtin_wins(catalog_env):
    catalog, builtin, installed = catalog_env
    builtin_path = _make_module(builtin, "demo", version="1.0.0")
    installed_path = _make_module(installed, "demo", version="9.9.9")

    entries = catalog.scan()

    assert len(entries) == 1
    entry = entries[0]
    assert entry.source == "builtin"
    assert entry.status == "conflict"
    assert "外部副本" in entry.error
    assert catalog.get("demo").path == builtin_path
    conflicts = catalog.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0]["name"] == "demo"
    assert conflicts[0]["builtin_path"] == str(builtin_path)
    assert conflicts[0]["installed_path"] == str(installed_path)
    # 冲突不影响解析：默认仍取内置，显式 installed 才取外部
    assert catalog.resolve_path("demo") == builtin_path
    assert catalog.resolve_path("demo", source="installed") == installed_path


def test_scan_marks_uninstalled_entries(catalog_env):
    catalog, builtin, installed = catalog_env
    _make_module(builtin, "core")
    _make_module(installed, "plugin")
    catalog.install_service.uninstall("plugin", source="installed", display_name="插件", version="1.0.0")
    catalog.install_service.uninstall("core", source="builtin")

    entries = {entry.name: entry for entry in catalog.scan()}

    assert entries["core"].status == "uninstalled"
    assert entries["plugin"].status == "uninstalled"


# ==================== 路径解析（P0-2） ====================


def test_resolve_path_source_priority(catalog_env):
    catalog, builtin, installed = catalog_env
    builtin_path = _make_module(builtin, "demo")
    installed_path = _make_module(installed, "ziponly", page=True)

    assert catalog.resolve_path("demo") == builtin_path
    assert catalog.resolve_path("demo", source="builtin") == builtin_path
    assert catalog.resolve_path("demo", source="installed") is None
    assert catalog.resolve_path("ziponly") == installed_path
    assert catalog.resolve_path("ziponly", source="installed") == installed_path
    assert catalog.resolve_path("ziponly", source="installed").joinpath("pages/index.html").is_file()
    assert catalog.resolve_path("ghost") is None


@pytest.mark.parametrize("bad_name", ["../etc/passwd", "a/b", "C:evil", "..", "", "_private"])
def test_resolve_path_rejects_unsafe_names(catalog_env, bad_name):
    catalog, _builtin, _installed = catalog_env
    assert catalog.resolve_path(bad_name) is None


def test_refresh_picks_up_new_modules(catalog_env):
    catalog, builtin, _installed = catalog_env
    assert catalog.scan() == []

    _make_module(builtin, "late")
    assert catalog.get("late") is None

    catalog.refresh()
    assert [entry.name for entry in catalog.scan()] == ["late"]
    assert catalog.get("late") is not None


# ==================== 注册表消费编目 ====================


@pytest.fixture
async def registry_env(container, tmp_path):
    root = tmp_path / "plugin_root"
    builtin = root / "builtin"
    installed = root / "plugins"
    builtin.mkdir(parents=True)
    installed.mkdir(parents=True)
    cfg = container.get(ConfigService)
    svc = ServiceAccess(cache=None, config_service=cfg, task_manager=_TaskStub(), settings=None)
    install_service = ModuleInstallService(root / "uninstalled_modules.json")
    reg = ModuleRegistry(
        plugins_dir=builtin,
        zip_plugins_dir=installed,
        config_service=cfg,
        services=svc,
        install_service=install_service,
    )
    return root, reg


async def test_load_failure_is_visible_and_leaves_no_ghost(registry_env):
    root, reg = registry_env
    _make_module(root / "builtin", "okmod")
    _make_module(root / "plugins", "badmod", main=BAD_SRC)

    loaded = await reg.load_all(bot_id=123)

    assert loaded == 1
    assert reg.module_names() == ["okmod"], "加载失败的模块不能留幽灵条目"
    assert reg.get("badmod", 123) is None
    assert "boom" in (reg.load_error("badmod") or "")

    entries = {entry.name: entry for entry in reg.catalog_entries()}
    assert entries["badmod"].status == "error"
    assert "boom" in entries["badmod"].error
    assert entries["okmod"].status == "ok"


async def test_installed_missing_manifest_rejected_by_loader(registry_env):
    root, reg = registry_env
    _make_module(root / "plugins", "nomanifest", with_manifest=False)

    assert await reg.load_single("nomanifest", 123) is False

    assert reg.get("nomanifest", 123) is None
    assert "module.json" in (reg.load_error("nomanifest") or "")
    entry = {entry.name: entry for entry in reg.catalog_entries()}["nomanifest"]
    assert entry.status == "error"


async def test_discover_skips_error_entries(registry_env):
    root, reg = registry_env
    _make_module(root / "builtin", "okmod")
    _make_module(root / "plugins", "broken", with_manifest=False)

    assert reg.discover() == 1
    assert reg.loaded_map() == {}


async def test_load_error_cleared_after_fix(registry_env):
    root, reg = registry_env
    path = _make_module(root / "plugins", "fixme", main=BAD_SRC)
    assert await reg.load_single("fixme", 123) is False
    assert reg.load_error("fixme")

    (path / "main.py").write_text(MODULE_SRC, encoding="utf-8")
    assert await reg.reload_single("fixme", 123) is True
    assert reg.load_error("fixme") is None


async def test_load_all_does_not_precreate_ghost_entries(registry_env):
    """旧实现先 `_modules[name] = {}` 再加载：坏模块会留下空壳。"""
    root, reg = registry_env
    _make_module(root / "plugins", "badmod", main=BAD_SRC)

    await reg.load_all(bot_id=123)

    assert "badmod" not in reg.loaded_map()
