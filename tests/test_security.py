"""安全回归测试：密码脱敏 / task_id 路径穿越 / access_token 打码。"""

import pytest

from app.infrastructure.config.config_service import mask_ws_url, restore_ws_url
from app.services.bot_service import PASSWORD_MASK, _mask_password_config


def test_password_fields_are_masked():
    schema = {"api_key": {"type": "password"}, "model": {"type": "string"}}
    cfg = {"api_key": "sk-secret", "model": "deepseek-chat"}
    masked = _mask_password_config(cfg, schema)
    assert masked["api_key"] == PASSWORD_MASK
    assert masked["model"] == "deepseek-chat"  # 非 password 不受影响


def test_password_mask_empty_stays_empty():
    schema = {"api_key": {"type": "password"}}
    assert _mask_password_config({"api_key": ""}, schema)["api_key"] == ""


def test_ws_url_token_masked():
    original = "ws://192.168.1.1:3003/?access_token=onebot114514&x=1"
    masked = mask_ws_url(original)
    assert "onebot114514" not in masked
    assert "access_token=****" in masked
    assert "&x=1" in masked  # 其余参数保留
    # 无 token 的 URL 原样
    assert mask_ws_url("ws://localhost:8080/ws/") == "ws://localhost:8080/ws/"


def test_ws_url_token_restored_on_save():
    original = "ws://192.168.1.1:3003/?access_token=onebot114514&x=1"
    masked = mask_ws_url(original)
    restored = restore_ws_url(masked, original)
    assert restored == original
    # 用户新填的 token（非打码）不被覆盖
    assert restore_ws_url("ws://h/?access_token=newtoken", original) == "ws://h/?access_token=newtoken"


async def test_save_bots_restores_masked_token(config_service):
    await config_service.save_bots([
        {"ws_url": "ws://h/?access_token=realtoken", "owner_id": 1, "auto_connect": True},
    ])
    # WebUI 提交打码 URL → 保存后还原
    await config_service.save_bots([
        {"ws_url": "ws://h/?access_token=****", "owner_id": 1, "auto_connect": True},
    ])
    assert config_service.get_bots()[0]["ws_url"] == "ws://h/?access_token=realtoken"
    # 对外接口拆分：ws_url 为纯地址，access_token 独立字段回显真实值（供配置页编辑）
    public = config_service.get_bots_public()[0]
    assert public["ws_url"] == "ws://h/"
    assert public["access_token"] == "realtoken"


async def test_save_bots_independent_token_field(config_service):
    """独立 access_token 字段：未提交/打码 → 保留旧值；新值 → 采用；空串 → 清除。"""
    await config_service.save_bots([
        {"ws_url": "ws://h/?access_token=realtoken", "owner_id": 1},
    ])
    # 1) 未提交 token 字段（改 URL 其他部分）→ 保留旧 token
    await config_service.save_bots([
        {"ws_url": "ws://h/", "owner_id": 1},
    ])
    assert config_service.get_bots()[0]["ws_url"] == "ws://h/?access_token=realtoken"
    # 2) 提交打码哨兵 → 保留旧 token
    await config_service.save_bots([
        {"ws_url": "ws://h/", "access_token": "****", "owner_id": 1},
    ])
    assert config_service.get_bots()[0]["ws_url"] == "ws://h/?access_token=realtoken"
    # 3) 提交新值 → 采用新 token
    await config_service.save_bots([
        {"ws_url": "ws://h/", "access_token": "newtoken", "owner_id": 1},
    ])
    assert config_service.get_bots()[0]["ws_url"] == "ws://h/?access_token=newtoken"
    # 4) 提交空串（显式清除）→ 删除 token
    await config_service.save_bots([
        {"ws_url": "ws://h/", "access_token": "", "owner_id": 1},
    ])
    assert config_service.get_bots()[0]["ws_url"] == "ws://h/"
    assert config_service.get_bots_public()[0]["access_token"] == ""


def test_split_join_ws_url():
    from app.infrastructure.config.config_service import join_ws_url, split_ws_url

    # 拆分：token 拆出，其余 query 保留在 base
    base, token = split_ws_url("ws://h:3003/?access_token=abc&x=1")
    assert base == "ws://h:3003/?x=1"
    assert token == "abc"
    # 无 token
    assert split_ws_url("ws://h:3003/") == ("ws://h:3003/", "")
    assert split_ws_url("") == ("", "")
    # 拼接：有 token 补参；无 token 原样
    assert join_ws_url("ws://h/", "abc") == "ws://h/?access_token=abc"
    assert join_ws_url("ws://h/?x=1", "abc") == "ws://h/?x=1&access_token=abc"
    assert join_ws_url("ws://h/", "") == "ws://h/"


def test_llm_history_task_id_path_traversal_rejected():
    from app.llm.history import HistoryManager

    hm = HistoryManager("testbot")
    # 合法 task_id（uuid4 hex）通过
    assert "history_abcdef123456.json" in hm._file_path("abcdef123456")
    # 路径穿越 / 非法字符被拒
    for bad in ("../secret", "..%2fetc", "a/b", "abc!", "ABCdef123456"):
        with pytest.raises(ValueError):
            hm._file_path(bad)
    # load_history 对非法 task_id 返回 None 而非抛异常
    assert hm.load_history("../etc/passwd") is None


