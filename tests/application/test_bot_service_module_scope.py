"""模块列表的账号作用域：不能拿「回退实例」的启停冒充请求账号的状态。

历史 bug（用户可见："某个账号的插件开关总是全部开启"）：账号还没装配模块实例时，
``get_modules_data(account_id)`` 回退到全局 / 别的账号的实例，直接读它的
``authority.enabled``（全局默认 True）→ 该账号页面显示所有插件都开着。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.application.bot_service import BotService
from app.plugin.base import ModuleAuthority, ModuleConfig


class _ConfigService:
    """acc1 显式关了 demo，全局层（None）是默认开。"""

    def get_module_config(self, module: str, bot_id) -> dict:
        return {}

    def get_module_authority(self, module: str, bot_id) -> dict:
        return {
            "enabled": bot_id != "acc1",
            "group_mode": "blacklist",
            "group_list": [],
            "user_mode": "blacklist",
            "user_list": [],
        }

    def get_bots(self) -> list:
        return []

    def get_bot_accounts(self) -> dict:
        return {}


class _Registry:
    """只有一个全局实例（bot_id=None）：模拟「acc1 还没装配」。"""

    def __init__(self) -> None:
        self.instance = _Instance()

    def loaded_map(self) -> dict:
        return {"demo": {None: self.instance}}

    def catalog_entries(self) -> list:
        return []

    def module_has_page(self, name: str) -> bool:
        return False


class _Instance:
    module_name = "demo"
    name = "演示插件"
    sign = "Module"
    description = ""
    permission = "member"
    category = "未分类"
    tags: list = []
    order = 100
    hidden = False
    pinned = False
    provides: tuple = ()
    supersedes: tuple = ()
    source = "builtin"
    version = "1.0.0"
    config_schema: dict = {}
    default_config = {"flag": True}
    bot_id = None

    def __init__(self) -> None:
        service = _ConfigService()
        self.config = ModuleConfig(self.module_name, None, self.default_config, service)
        self.authority = ModuleAuthority(self.module_name, None, service)


def _service() -> BotService:
    return BotService(
        gateway=SimpleNamespace(),
        registry=_Registry(),
        config_service=_ConfigService(),
        dispatcher=SimpleNamespace(dispatch=lambda *args, **kwargs: None),
    )


def test_modules_data_uses_requested_account_switch():
    data = _service().get_modules_data("acc1")
    entry = data["demo"]
    assert entry["account_id"] == "acc1", "回退实例的 bot_id 不能冒充请求账号"
    assert entry["enabled"] is False, "acc1 显式关了 demo，不能显示成全局默认的开启"


def test_modules_data_global_scope_keeps_global_default():
    data = _service().get_modules_data(None)
    assert data["demo"]["enabled"] is True
