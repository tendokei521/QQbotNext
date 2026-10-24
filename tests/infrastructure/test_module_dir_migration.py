"""`module/` → `data/module/` 一次性迁移（`app/infrastructure/fs/migrate.py`）。

守的是四条契约：整棵树搬过去、目标同名文件优先、幂等、以及失败不吵不崩。
"""

from __future__ import annotations

import json

from app.infrastructure.fs.migrate import migrate_legacy_module_dir


def _legacy_tree(root):
    """造一棵典型的旧 `module/`：data/ + configs/ + 软卸载状态文件。"""
    legacy = root / "module"
    (legacy / "data" / "group_log" / "1" / "group_2").mkdir(parents=True)
    (legacy / "data" / "group_log" / "1" / "group_2" / "2026-10-22.jsonl").write_text(
        '{"k": 1}\n', encoding="utf-8"
    )
    (legacy / "configs" / "demo").mkdir(parents=True)
    (legacy / "configs" / "demo" / "config.json").write_text('{"a": 1}', encoding="utf-8")
    (legacy / "uninstalled_modules.json").write_text(
        json.dumps({"demo": {"source": "local"}}), encoding="utf-8"
    )
    return legacy


def test_absent_legacy_dir_does_nothing(tmp_path):
    """没有旧目录时不留痕：不建目标目录，不报错。"""
    target = tmp_path / "data" / "module"

    assert migrate_legacy_module_dir(target, tmp_path) == []
    assert not target.exists(), "没事可做时不该顺手创建 data/module"


def test_moves_whole_tree_and_removes_legacy_dir(tmp_path):
    """整棵树原样搬到 data/module，旧目录搬空后删除。"""
    legacy = _legacy_tree(tmp_path)
    target = tmp_path / "data" / "module"

    moved = migrate_legacy_module_dir(target, tmp_path)

    assert sorted(moved) == sorted([
        "configs/demo/config.json",
        "data/group_log/1/group_2/2026-10-22.jsonl",
        "uninstalled_modules.json",
    ])
    assert (target / "data" / "group_log" / "1" / "group_2" / "2026-10-22.jsonl").read_text(
        encoding="utf-8"
    ) == '{"k": 1}\n'
    assert json.loads((target / "uninstalled_modules.json").read_text(encoding="utf-8")) == {
        "demo": {"source": "local"}
    }
    assert not legacy.exists(), "搬空的旧 module/ 应被删除"


def test_target_file_wins_and_conflict_is_reported(tmp_path):
    """目标已有同名文件 → 保留目标（不覆盖），旧文件不动，旧目录留着等人工确认。"""
    legacy = _legacy_tree(tmp_path)
    target = tmp_path / "data" / "module"
    target.mkdir(parents=True)
    (target / "uninstalled_modules.json").write_text(
        json.dumps({"demo": {"source": "zip"}}), encoding="utf-8"
    )

    migrate_legacy_module_dir(target, tmp_path)

    assert json.loads((target / "uninstalled_modules.json").read_text(encoding="utf-8")) == {
        "demo": {"source": "zip"}
    }, "目标那份是新的，不能被旧文件覆盖"
    assert (legacy / "uninstalled_modules.json").exists(), "冲突的旧文件不删，留给人工"
    assert not (legacy / "data").exists(), "其余无冲突的部分照常搬走"


def test_second_run_is_noop(tmp_path):
    """幂等：搬完再跑一次，什么都不发生。"""
    _legacy_tree(tmp_path)
    target = tmp_path / "data" / "module"

    assert migrate_legacy_module_dir(target, tmp_path), "第一次应该真的搬了东西"
    assert migrate_legacy_module_dir(target, tmp_path) == []


def test_module_dir_pointing_at_legacy_dir_is_skipped(tmp_path):
    """用户显式把 MODULE_DIR 指回 `module/`：没有可搬的，也不许删它。"""
    legacy = _legacy_tree(tmp_path)

    assert migrate_legacy_module_dir(legacy, tmp_path) == []
    assert (legacy / "uninstalled_modules.json").exists()


def test_merge_does_not_duplicate_existing_dirs(tmp_path):
    """目标已有同名目录：往里合并，而不是整目录顶掉。"""
    _legacy_tree(tmp_path)
    target = tmp_path / "data" / "module"
    (target / "data" / "group_log" / "9").mkdir(parents=True)
    (target / "data" / "group_log" / "9" / "keep.jsonl").write_text("x\n", encoding="utf-8")

    migrate_legacy_module_dir(target, tmp_path)

    assert (target / "data" / "group_log" / "9" / "keep.jsonl").exists(), "目标已有内容必须保留"
    assert (target / "data" / "group_log" / "1" / "group_2" / "2026-10-22.jsonl").exists()
