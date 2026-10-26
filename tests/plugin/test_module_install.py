"""插件 zip 安装测试：布局容错 / 事务回滚 / 覆盖备份 / 冲突 / 自定义页。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.application.module_install_service import ModuleInstallService
from app.infrastructure.config.config_service import ConfigService
from app.plugin.base import ServiceAccess
from app.plugin.installer import ModuleInstallError, ZipPluginInstaller
from app.plugin.registry import ModuleRegistry

ZIP_MODULE = '''
from app.plugin import BaseModule

class Module(BaseModule):
    name = "zip 插件"
    sign = "ZipMod"
    description = "zip 插件测试"
    permission = "member"
    default_config = {"y": 2}

    async def on_load(self):
        self.loaded = True
'''

BROKEN_MODULE = "raise RuntimeError('boom: 入口炸了')\n"


class _TaskStub:
    def cancel_owner(self, owner):
        return 0


def _manifest(name: str = "demo", version: str = "1.0.0", **extra) -> str:
    import json

    data = {"name": name, "display_name": f"演示 {name}", "version": version, "api_version": 1}
    data.update(extra)
    return json.dumps(data, ensure_ascii=False)


def _plugin_files(
    name: str = "demo",
    *,
    version: str = "1.0.0",
    main: str = ZIP_MODULE,
    page: bool = False,
    manifest: str | None = None,
) -> dict[str, str | bytes]:
    """插件根下的文件表（key 是相对插件根的路径）。"""
    files: dict[str, str | bytes] = {
        "module.json": manifest if manifest is not None else _manifest(name, version),
        "main.py": main,
        "__init__.py": "",
    }
    if page:
        files["pages/index.html"] = "<html><body>zip-page</body></html>"
    return files


def _write_zip(zip_path: Path, entries: dict[str, str | bytes]) -> Path:
    with zipfile.ZipFile(zip_path, "w") as archive:
        for arcname, content in entries.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(arcname, data)
    return zip_path


def _prefix(files: dict[str, str | bytes], prefix: str) -> dict[str, str | bytes]:
    return {f"{prefix}/{name}": content for name, content in files.items()}


@pytest.fixture
async def env(container, tmp_path):
    """内置根为空 + 外部安装根独立，便于观察"装到哪里"。"""
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
        staging_dir=root / ".staging",
        backup_dir=root / ".backup",
    )
    return root, reg, install_service


def _staging_entries(root: Path) -> list[Path]:
    staging = root / ".staging"
    return [path for path in staging.iterdir()] if staging.is_dir() else []


# ==================== 布局容错（审计附录 A 的 A-D） ====================


async def test_layout_a_top_dir_named_like_plugin(env, tmp_path):
    root, reg, _ = env
    zip_path = _write_zip(tmp_path / "a.zip", _prefix(_plugin_files(), "demo"))

    info = await reg.install_from_zip(zip_path)

    assert info["name"] == "demo"
    assert info["source"] == "installed"
    assert (root / "plugins" / "demo" / "main.py").is_file()
    # 插件根下直接就是 main.py，没有多一层 demo/demo
    assert not (root / "plugins" / "demo" / "demo").exists()
    assert _staging_entries(root) == []


async def test_layout_b_github_repo_main(env, tmp_path):
    """GitHub 下载的 repo-main/ 包装：必须压平成插件根。"""
    root, reg, _ = env
    zip_path = _write_zip(tmp_path / "b.zip", _prefix(_plugin_files(), "repo-main"))

    info = await reg.install_from_zip(zip_path)

    assert info["name"] == "demo"
    assert (root / "plugins" / "demo" / "main.py").is_file()
    assert not (root / "plugins" / "demo" / "repo-main").exists()
    assert reg.get("demo", None) is not None


async def test_layout_c_flat_zip(env, tmp_path):
    root, reg, _ = env
    zip_path = _write_zip(tmp_path / "c.zip", _plugin_files())

    info = await reg.install_from_zip(zip_path)

    assert info["name"] == "demo"
    assert (root / "plugins" / "demo" / "main.py").is_file()


async def test_layout_d_multiple_top_dirs_with_noise(env, tmp_path):
    """demo/ + docs/ 两个顶层目录：按"只有一个像插件"挑出 demo，丢弃 docs。"""
    root, reg, _ = env
    entries = _prefix(_plugin_files(), "demo")
    entries["docs/readme.md"] = "文档"
    zip_path = _write_zip(tmp_path / "d.zip", entries)

    info = await reg.install_from_zip(zip_path)

    assert info["name"] == "demo"
    assert (root / "plugins" / "demo" / "main.py").is_file()
    assert not (root / "plugins" / "demo" / "docs").exists()


async def test_macosx_noise_is_dropped(env, tmp_path):
    root, reg, _ = env
    entries = _prefix(_plugin_files(), "demo")
    entries["__MACOSX/demo/._module.json"] = b"\x00\x01binary"
    entries["__MACOSX/._demo"] = b"\x00"
    zip_path = _write_zip(tmp_path / "mac.zip", entries)

    info = await reg.install_from_zip(zip_path)

    assert info["name"] == "demo"
    assert (root / "plugins" / "demo" / "main.py").is_file()
    assert not (root / "plugins" / "demo" / "__MACOSX").exists()


async def test_multi_level_wrapper(env, tmp_path):
    """manifest 在 ≥2 级（repo-main/plugin/）也要能装。"""
    root, reg, _ = env
    entries = _prefix(_plugin_files(), "repo-main/plugin")
    zip_path = _write_zip(tmp_path / "deep.zip", entries)

    info = await reg.install_from_zip(zip_path)

    assert info["name"] == "demo"
    assert (root / "plugins" / "demo" / "main.py").is_file()


async def test_multiple_plugin_dirs_rejected(env, tmp_path):
    _, reg, _ = env
    entries = _prefix(_plugin_files("alpha"), "alpha")
    entries.update(_prefix(_plugin_files("beta"), "beta"))
    zip_path = _write_zip(tmp_path / "two.zip", entries)

    with pytest.raises(ModuleInstallError) as exc_info:
        await reg.install_from_zip(zip_path)

    assert "多个插件目录" in str(exc_info.value)
    assert exc_info.value.hint


# ==================== 清单 / 命名 / 安全 ====================


async def test_non_utf8_manifest_rejected_without_residue(env, tmp_path):
    root, reg, _ = env
    gbk_manifest = _manifest("demo").encode("gbk")
    entries = _plugin_files()
    entries["module.json"] = gbk_manifest
    zip_path = _write_zip(tmp_path / "gbk.zip", entries)

    with pytest.raises(ModuleInstallError) as exc_info:
        await reg.install_from_zip(zip_path)

    assert "UTF-8" in str(exc_info.value)
    assert "UTF-8" in exc_info.value.hint
    assert not (root / "plugins" / "demo").exists()
    assert _staging_entries(root) == []


async def test_manifest_missing_rejected(env, tmp_path):
    root, reg, _ = env
    entries = _plugin_files()
    entries.pop("module.json")
    zip_path = _write_zip(tmp_path / "nomanifest.zip", entries)

    with pytest.raises(ModuleInstallError) as exc_info:
        await reg.install_from_zip(zip_path)

    assert "module.json" in str(exc_info.value)
    assert not (root / "plugins").exists() or not any((root / "plugins").iterdir())


async def test_name_with_dot_rejected(env, tmp_path):
    root, reg, _ = env
    entries = _plugin_files(manifest=_manifest("my.plugin"))
    zip_path = _write_zip(tmp_path / "dot.zip", entries)

    with pytest.raises(ModuleInstallError) as exc_info:
        await reg.install_from_zip(zip_path)

    assert "name" in str(exc_info.value)
    assert not (root / "plugins" / "my.plugin").exists()
    assert _staging_entries(root) == []


@pytest.mark.parametrize("bad_name", ["中文插件", "with space", "9start", "a:b"])
async def test_illegal_module_names_rejected(env, tmp_path, bad_name):
    root, reg, _ = env
    zip_path = _write_zip(tmp_path / "bad.zip", _prefix(_plugin_files(manifest=_manifest(bad_name)), "demo"))

    with pytest.raises(ModuleInstallError):
        await reg.install_from_zip(zip_path)

    assert _staging_entries(root) == []


async def test_path_traversal_rejected(env, tmp_path):
    root, reg, _ = env
    entries = _prefix(_plugin_files(), "demo")
    entries["../evil.txt"] = "pwned"
    zip_path = _write_zip(tmp_path / "evil.zip", entries)

    with pytest.raises(ModuleInstallError) as exc_info:
        await reg.install_from_zip(zip_path)

    assert "穿越" in str(exc_info.value)
    assert not (tmp_path / "evil.txt").exists()
    assert not (root / "plugins" / "demo").exists()


async def test_absolute_path_rejected(env, tmp_path):
    _, reg, _ = env
    entries = _prefix(_plugin_files(), "demo")
    entries["/tmp/evil.txt"] = "pwned"
    zip_path = _write_zip(tmp_path / "abs.zip", entries)

    with pytest.raises(ModuleInstallError) as exc_info:
        await reg.install_from_zip(zip_path)

    assert "绝对路径" in str(exc_info.value)


async def test_too_many_files_rejected(env, tmp_path, monkeypatch):
    _, reg, _ = env
    monkeypatch.setattr("app.plugin.installer.MAX_ARCHIVE_FILES", 2)
    zip_path = _write_zip(tmp_path / "many.zip", _plugin_files())

    with pytest.raises(ModuleInstallError) as exc_info:
        await reg.install_from_zip(zip_path)

    assert "文件数过多" in str(exc_info.value)


async def test_legacy_module_py_entry_accepted(env, tmp_path):
    """旧插件可能用 module.py 当入口；loader 兼容，安装期也应放行。"""
    root, reg, _ = env
    entries = _plugin_files()
    entries.pop("main.py")
    entries["module.py"] = ZIP_MODULE  # 旧入口：loader 兼容
    zip_path = _write_zip(tmp_path / "legacy.zip", entries)
    info = await reg.install_from_zip(zip_path)
    assert info["name"] == "demo"
    assert reg.get("demo", None) is not None


async def test_missing_entry_file_rejected(env, tmp_path):
    root, reg, _ = env
    entries = _plugin_files()
    entries.pop("main.py")
    zip_path = _write_zip(tmp_path / "noentry.zip", entries)
    with pytest.raises(ModuleInstallError) as exc_info:
        await reg.install_from_zip(zip_path)
    assert "入口文件" in str(exc_info.value)
    assert not (root / "plugins" / "demo").exists()

    # 显式 entry 指向不存在的文件 → 拒绝
    entries = _plugin_files(manifest=_manifest("demo", entry="entry.py"))
    zip_path2 = _write_zip(tmp_path / "noentry2.zip", entries)
    with pytest.raises(ModuleInstallError) as exc_info:
        await reg.install_from_zip(zip_path2, force=True)
    assert "入口文件" in str(exc_info.value)


def test_gbk_filename_fallback_decode():
    """无 UTF-8 标志的条目：cp437 字节视图按 gb18030 重解。"""
    raw = "中文.txt".encode("gbk")
    info = zipfile.ZipInfo(raw.decode("cp437"))
    info.flag_bits &= ~0x800
    assert ZipPluginInstaller._decode_name(info) == "中文.txt"


# ==================== 覆盖安装 / 备份 / 回滚 ====================


async def test_overwrite_requires_force_and_keeps_backup(env, tmp_path):
    root, reg, _ = env
    v1 = _write_zip(tmp_path / "v1.zip", _prefix(_plugin_files(version="1.0.0"), "demo"))
    await reg.install_from_zip(v1)
    # 改一条配置，验证覆盖安装不丢
    reg.get("demo", None).config.set("y", 42)

    v2 = _write_zip(tmp_path / "v2.zip", _prefix(_plugin_files(version="2.0.0"), "demo"))
    with pytest.raises(ModuleInstallError) as exc_info:
        await reg.install_from_zip(v2)
    assert "已安装" in str(exc_info.value)
    assert "覆盖" in exc_info.value.hint

    info = await reg.install_from_zip(v2, force=True)
    assert info["overwritten"] is True
    assert info["backup"] is not None
    assert reg.get("demo", None).version == "2.0.0"
    assert reg.get("demo", None).config.get("y") == 42

    backup_path = Path(info["backup"])
    assert backup_path.is_dir()
    assert backup_path.name.startswith("demo-")
    assert (backup_path / "module.json").is_file()


async def test_new_install_load_failure_rolls_back(env, tmp_path):
    root, reg, _ = env
    zip_path = _write_zip(tmp_path / "broken.zip", _prefix(_plugin_files(main=BROKEN_MODULE), "demo"))

    with pytest.raises(ModuleInstallError) as exc_info:
        await reg.install_from_zip(zip_path)

    assert "加载失败" in str(exc_info.value)
    assert exc_info.value.hint
    assert not (root / "plugins" / "demo").exists(), "失败安装必须回滚，不留坏目录"
    assert _staging_entries(root) == []
    assert reg.get("demo", None) is None


async def test_upgrade_load_failure_restores_previous_version(env, tmp_path):
    root, reg, _ = env
    v1 = _write_zip(tmp_path / "v1.zip", _prefix(_plugin_files(version="1.0.0"), "demo"))
    await reg.install_from_zip(v1)
    assert reg.get("demo", None).version == "1.0.0"

    v2 = _write_zip(
        tmp_path / "v2bad.zip",
        _prefix(_plugin_files(version="2.0.0", main=BROKEN_MODULE), "demo"),
    )
    with pytest.raises(ModuleInstallError) as exc_info:
        await reg.install_from_zip(v2, force=True)

    # 旧版本目录与实例都应恢复
    assert (root / "plugins" / "demo" / "main.py").is_file()
    assert reg.get("demo", None) is not None
    assert reg.get("demo", None).version == "1.0.0"
    assert "boom" in exc_info.value.hint
    assert _staging_entries(root) == []


async def test_backup_retention_keeps_three(env, tmp_path):
    root, reg, _ = env
    await reg.install_from_zip(_write_zip(tmp_path / "v1.zip", _prefix(_plugin_files(), "demo")))
    for index in range(2, 6):
        path = _write_zip(
            tmp_path / f"v{index}.zip",
            _prefix(_plugin_files(version=f"1.0.{index}"), "demo"),
        )
        await reg.install_from_zip(path, force=True)

    backups = sorted((root / ".backup").iterdir())
    assert len(backups) == 3


# ==================== 冲突 / 页面 / 软卸载 ====================


async def test_builtin_name_conflict_rejected(env, tmp_path):
    root, reg, _ = env
    builtin = root / "builtin" / "demo"
    builtin.mkdir(parents=True)
    (builtin / "module.json").write_text(_manifest("demo"), encoding="utf-8")
    (builtin / "main.py").write_text(ZIP_MODULE, encoding="utf-8")

    zip_path = _write_zip(tmp_path / "demo.zip", _prefix(_plugin_files(), "demo"))
    with pytest.raises(ModuleInstallError) as exc_info:
        await reg.install_from_zip(zip_path)

    assert "内置模块" in str(exc_info.value) or "内置" in exc_info.value.hint
    assert not (root / "plugins" / "demo").exists()


async def test_installed_module_page_is_served(env, tmp_path):
    root, reg, _ = env
    zip_path = _write_zip(tmp_path / "page.zip", _prefix(_plugin_files(page=True), "demo"))

    await reg.install_from_zip(zip_path)

    assert reg.module_has_page("demo") is True
    page = reg.module_page_path("demo")
    assert page is not None and page.is_file()
    assert page.read_text(encoding="utf-8") == "<html><body>zip-page</body></html>"


async def test_validate_zip_dry_run(env, tmp_path):
    _root, reg, _ = env
    zip_path = _write_zip(tmp_path / "preview.zip", _prefix(_plugin_files(), "demo"))

    preview = reg.validate_zip(zip_path)

    assert preview["ok"] is True
    plugin = preview["plugin"]
    assert plugin["name"] == "demo"
    assert plugin["version"] == "1.0.0"
    assert plugin["conflict"] is False
    assert plugin["files"] >= 3
    # dry-run 不落盘
    assert not (tmp_path / "plugin_root" / "plugins" / "demo").exists()


async def test_validate_zip_reports_error(env, tmp_path):
    _root, reg, _ = env
    zip_path = _write_zip(tmp_path / "bad.zip", {"readme.txt": "no manifest"})

    preview = reg.validate_zip(zip_path)

    assert preview["ok"] is False
    assert preview["hint"]


async def test_validate_zip_reports_existing_install(env, tmp_path):
    _root, reg, _ = env
    await reg.install_from_zip(_write_zip(tmp_path / "v1.zip", _prefix(_plugin_files(), "demo")))

    preview = reg.validate_zip(_write_zip(tmp_path / "v2.zip", _prefix(_plugin_files(version="2.0.0"), "demo")))

    assert preview["ok"] is True
    plugin = preview["plugin"]
    assert plugin["conflict"] is True
    assert plugin["existing_version"] == "1.0.0"
    assert "覆盖" in plugin["conflict_reason"]

    info = await reg.update_from_zip(tmp_path / "v2.zip")
    assert info["updated"] is True
    assert info["version"] == "2.0.0"
    assert reg.get("demo", None).version == "2.0.0"


async def test_delete_installed_module(env, tmp_path):
    root, reg, _ = env
    await reg.install_from_zip(_write_zip(tmp_path / "v1.zip", _prefix(_plugin_files(), "demo")))
    assert reg.get("demo", None) is not None

    result = await reg.delete_module("demo")

    assert result["deleted"] is True
    assert not (root / "plugins" / "demo").exists()
    assert reg.get("demo", None) is None
    assert reg.module_names() == []


async def test_delete_builtin_module_rejected(env, tmp_path):
    root, reg, _ = env
    builtin = root / "builtin" / "core"
    builtin.mkdir(parents=True)
    (builtin / "module.json").write_text(_manifest("core"), encoding="utf-8")
    (builtin / "main.py").write_text(ZIP_MODULE, encoding="utf-8")

    with pytest.raises(ModuleInstallError) as exc_info:
        await reg.delete_module("core")

    assert "内置模块不可删除" in str(exc_info.value)
    assert "内置" in exc_info.value.hint
    assert builtin.is_dir()


async def test_delete_unknown_module_raises_value_error(env):
    _root, reg, _ = env
    with pytest.raises(ValueError):
        await reg.delete_module("ghost")


async def test_install_then_uninstall_then_reinstall_roundtrip(env, tmp_path):
    root, reg, install_service = env
    await reg.install_from_zip(_write_zip(tmp_path / "v1.zip", _prefix(_plugin_files(), "demo")))

    await reg.uninstall_module("demo")
    assert reg.get("demo", None) is None
    assert (root / "plugins" / "demo" / "main.py").is_file()
    assert install_service.is_uninstalled("demo") is True

    assert await reg.reinstall_module("demo", None) is True
    assert reg.get("demo", None) is not None


async def test_uninstall_unknown_module_raises_value_error(env):
    _root, reg, _ = env
    with pytest.raises(ValueError):
        await reg.uninstall_module("ghost")
