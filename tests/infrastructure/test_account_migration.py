"""账号 id / name 规则与旧库迁移的单元测试。

账号身份自 v4 起是用户设定的 ``account_id``：
- id 只允许 `[A-Za-z0-9][A-Za-z0-9_-]{0,31}`，且不能占用保留字（会与「全局实例」撞车）；
- name 只用于展示与日志，可以随时改、可以重名。
"""

import pytest

from app.infrastructure.persistence.account_migration import (
    ACCOUNT_NAME_MAX,
    normalize_account_id,
    normalize_account_name,
    suggest_account_id,
)


# ==================== id 规则 ====================

@pytest.mark.parametrize("raw", ["main", "bot1", "a", "A_b-9", "x" * 32, "123"])
def test_valid_account_ids(raw):
    assert normalize_account_id(raw) == raw


@pytest.mark.parametrize(
    "raw",
    [
        "", None, "   ",            # 空
        "a" * 33,                    # 太长
        "a b",                       # 空格
        "a/b",                       # 路径分隔符（会破坏磁盘路径）
        "a.b",                       # 点
        "中文",                       # 非 ASCII
        "-lead", "_lead",            # 必须以字母/数字开头
        "a\nb",                      # 控制字符
    ],
)
def test_invalid_account_ids(raw):
    assert normalize_account_id(raw) == ""


@pytest.mark.parametrize("raw", ["none", "None", "NULL", "global", "__global__", "_global"])
def test_reserved_ids_rejected(raw):
    """保留字会与「全局实例」(None) 或内部约定撞车，必须拒绝。"""
    assert normalize_account_id(raw) == ""


def test_id_is_trimmed_but_not_case_folded():
    assert normalize_account_id("  main  ") == "main"
    # 大小写不同是不同 id（id 是外部引用，不能悄悄归一）
    assert normalize_account_id("Main") == "Main"
    assert normalize_account_id("Main") != normalize_account_id("main")


def test_suggest_account_id_skips_taken():
    assert suggest_account_id([]) == "bot1"
    assert suggest_account_id(["bot1"]) == "bot2"
    assert suggest_account_id({"bot1", "bot2", "main"}) == "bot3"
    # 只保证「不与现有 id 冲突」：取最小的空闲 botN（默认值而已，用户可以改）
    assert suggest_account_id(["bot2"]) == "bot1"


# ==================== name 规则 ====================

def test_name_normalization():
    assert normalize_account_name("  主 号  ") == "主 号"
    assert normalize_account_name("多\n行\t空白") == "多 行 空白"
    assert normalize_account_name("") == ""
    assert normalize_account_name(None) == ""
    assert normalize_account_name("   ", "账号 1") == "账号 1"
    assert len(normalize_account_name("长" * 100)) == ACCOUNT_NAME_MAX


def test_name_may_repeat_and_differ_from_id():
    """name 不是标识：可以重复、可以和 id 完全无关。"""
    assert normalize_account_name("主号") == normalize_account_name("主号")
    assert normalize_account_name("完全不同的名字") != normalize_account_id("main")
