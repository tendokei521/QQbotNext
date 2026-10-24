"""运行时数据路径的默认值契约。

`module/`（仓库根）已废弃：插件运行时数据统一进 `data/module/`。
这条测试钉住「默认值 + 派生路径」这一组数字，免得哪天又悄悄长出第二个运行时目录。
"""

from __future__ import annotations

from pathlib import Path

from app.core.settings import ROOT_DIR, Settings


def test_module_dir_defaults_under_data(tmp_path, monkeypatch):
    """`MODULE_DIR` 默认落在 `data/module`，不是仓库根的 `module/`。"""
    # 环境变量优先于默认值：先清掉同名环境变量（本机可能残留老的 MODULE_DIR=module）
    monkeypatch.delenv("MODULE_DIR", raising=False)
    s = Settings(_env_file=None, db_path=str(tmp_path / "t.db"), log_dir=str(tmp_path / "logs"))

    assert s.module_dir == ROOT_DIR / "data" / "module"
    assert s.module_dir.name == "module" and s.module_dir.parent.name == "data"


def test_module_paths_are_derived_from_module_dir(tmp_path):
    """configs / data / plugins(zip) / 软卸载状态 全部挂在 `module_dir` 下。"""
    base = tmp_path / "custom" / "runtime"
    s = Settings(
        _env_file=None,
        db_path=str(tmp_path / "t.db"),
        log_dir=str(tmp_path / "logs"),
        module_dir=str(base),
    )

    assert s.module_configs_dir == base / "configs"
    assert s.module_data_dir == base / "data"
    assert s.zip_plugins_dir == base / "plugins"
    assert s.uninstalled_modules_file == base / "uninstalled_modules.json"


def test_no_repo_root_module_data_path_is_used(tmp_path, monkeypatch):
    """插件运行时数据默认**不**写进仓库根 `module/`（老布局）。"""
    monkeypatch.delenv("MODULE_DIR", raising=False)
    s = Settings(_env_file=None, db_path=str(tmp_path / "t.db"), log_dir=str(tmp_path / "logs"))

    assert Path(s.module_data_dir).parts[:1] != ("module",)
    assert ROOT_DIR / "module" not in Path(s.module_data_dir).parents
