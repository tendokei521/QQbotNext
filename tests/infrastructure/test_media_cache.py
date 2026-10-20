"""共享媒体缓存策略单测（``app/infrastructure/fs/media_cache.py``，全离线）。

bilibili_parser / douyin_parser 都依赖这套「只下载一次 + TTL/容量淘汰」策略，
因此这里覆盖纯函数（含 sweep 的实际删除行为）。
"""

import os

from app.infrastructure.fs import media_cache


def test_human_size_and_duration():
    assert media_cache.human_size(512) == "512.0B"
    assert media_cache.human_size(2048) == "2.0KB"
    assert media_cache.human_size(45 * 1024 * 1024) == "45.0MB"
    assert media_cache.human_size(3 * 1024 ** 3) == "3.0GB"

    assert media_cache.format_duration(125120) == "02:05"
    assert media_cache.format_duration(3_600_000) == "1:00:00"
    assert media_cache.format_duration(0) == "-"
    assert media_cache.format_duration(None) == "-"


def test_is_fresh_ttl(tmp_path):
    path = str(tmp_path / "v.mp4")
    assert media_cache.is_fresh(path, 60) is False  # 文件不存在

    with open(path, "wb") as fh:
        fh.write(b"x")
    now = os.path.getmtime(path) + 10
    assert media_cache.is_fresh(path, 60, now=now) is True
    assert media_cache.is_fresh(path, 60, now=now + 3600) is False
    assert media_cache.is_fresh(path, 0, now=now + 10 ** 6) is True  # TTL<=0 永不过期


def test_pick_stale_by_ttl_and_capacity():
    entries = [
        ("old.mp4", 1000.0, 10),
        ("mid.mp4", 2000.0, 10),
        ("new.mp4", 3000.0, 10),
    ]
    now = 3000.0
    assert media_cache.pick_stale(entries, 10, 0, now=now) == ["old.mp4", "mid.mp4"]
    assert media_cache.pick_stale(entries, 0, 25, now=now) == ["old.mp4"]
    assert media_cache.pick_stale(entries, 0, 0, now=now) == []


def test_sweep_removes_expired_and_reports(tmp_path):
    """sweep 真正删文件：过期文件被清掉，新鲜文件保留。"""
    old = tmp_path / "old.mp4"
    new = tmp_path / "new.mp4"
    old.write_bytes(b"x" * 10)
    new.write_bytes(b"y" * 10)
    os.utime(old, (1000.0, 1000.0))
    os.utime(new, (9_999_999_999.0, 9_999_999_999.0))  # 未来时间戳 → 视为新鲜

    removed = media_cache.sweep(str(tmp_path), ttl_minutes=60, max_mb=0)

    assert removed == [str(old)]
    assert not old.exists()
    assert new.exists()


def test_sweep_on_missing_dir_is_silent(tmp_path):
    """目录不存在只返回空列表，不抛异常（清理失败不该影响业务）。"""
    assert media_cache.sweep(str(tmp_path / "nope"), 60, 10) == []
