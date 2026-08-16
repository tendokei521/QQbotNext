"""语义化权限系统测试。"""

from app.domain.events import GroupMessageEvent
from app.modules.authority import (
    check_module_enabled,
    check_module_permission,
    compute_event_permission,
)
from app.modules.base import (
    BaseModule,
    ModuleAuthority,
    ModuleConfig,
    ModuleContext,
    ModulePermission,
    ServiceAccess,
)


class _Service:
    def __init__(self, data=None):
        self.data = data or {}

    def get_module_config(self, *a, **k):
        return {}

    def get_module_authority(self, module, bot_id):
        return self.data or {}


def _make_module(permission, auth_data=None):
    _permission = permission

    class M(BaseModule):
        name = "测试"
        sign = "Test"
        description = ""
        permission = _permission
        subscribe = ("message_group",)

        async def handle(self, event):
            pass

    svc = _Service(auth_data)
    auth = ModuleAuthority("test", 1, svc)
    config = ModuleConfig("test", 1, {}, svc)
    ctx = ModuleContext(
        module_name="test",
        bot_id=1,
        config=config,
        authority=auth,
        services=ServiceAccess(),
    )
    return M(ctx)


def _event(role="member", owner_id=1):
    return GroupMessageEvent(
        event_type="message_group", post_type="message", message_type="group",
        user_id=100, self_id=1, message_id=1, time=0,
        owner_id=owner_id,
        user=type("U", (), {"user_id": 100, "role": role})(),
        group=type("G", (), {"group_id": 10})(),
        raw={"sender": {"role": role}},
    )


def _perm_event(role="member", owner_id=1):
    event = _event(role=role, owner_id=owner_id)
    compute_event_permission(event)
    return event


def test_scope_whitelist_group():
    mod = _make_module(
        "member",
        {
            "group_mode": "whitelist",
            "group_list": ["10"],
            "user_mode": "blacklist",
            "user_list": [],
        },
    )
    event = _perm_event()
    assert check_module_permission(mod, event) is True

    mod2 = _make_module(
        "member",
        {
            "group_mode": "whitelist",
            "group_list": ["99"],
            "user_mode": "blacklist",
            "user_list": [],
        },
    )
    assert check_module_permission(mod2, event) is False


def test_scope_blacklist_user():
    mod = _make_module(
        "member",
        {
            "group_mode": "blacklist",
            "group_list": [],
            "user_mode": "blacklist",
            "user_list": ["100"],
        },
    )
    event = _perm_event()
    assert check_module_permission(mod, event) is False


def test_member_allows_member():
    mod = _make_module("member")
    assert check_module_permission(mod, _perm_event()) is True


def test_group_admin_blocks_member():
    mod = _make_module("group_admin")
    assert check_module_permission(mod, _perm_event()) is False


def test_group_admin_allows_admin():
    mod = _make_module("group_admin")
    assert check_module_permission(mod, _perm_event(role="admin")) is True


def test_group_owner_allows_owner():
    mod = _make_module("group_owner")
    assert check_module_permission(mod, _perm_event(role="owner")) is True


def test_owner_only_requires_bot_owner():
    mod = _make_module("owner")
    # 群主但不是 Bot 拥有者 → 拒绝
    assert check_module_permission(mod, _perm_event(role="owner")) is False
    # Bot 拥有者 → 通过
    assert check_module_permission(mod, _perm_event(role="member", owner_id=100)) is True


def test_everyone_allows_all():
    mod = _make_module("everyone")
    assert check_module_permission(mod, _perm_event()) is True


def test_module_enabled_flag():
    mod = _make_module("member", {"enabled": False})
    assert mod.authority.enabled is False
    assert check_module_enabled(mod) is False
