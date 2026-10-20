"""语义化权限系统测试。"""

from app.domain.events import GroupMessageEvent
from app.plugin.authority import (
    PERMISSIONS,
    ROLE_RANK,
    check_module_enabled,
    check_module_permission,
    compute_event_permission,
    roster_lists,
)
from app.plugin.base import (
    BaseModule,
    ModuleAuthority,
    ModuleConfig,
    ModuleContext,
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

# ==================== 名册身份（agent_owner_list / agent_manager_list） ====================


def test_roster_owner_beats_group_role():
    """群主不等于有资格调教 Bot：名册里的拥有者压过群主。"""
    event = _event(role="owner", owner_id=1)
    assert compute_event_permission(event, owner_list={"100"}) == "owner"


def test_roster_manager_sits_between_group_owner_and_owner():
    assert ROLE_RANK["group_owner"] < ROLE_RANK["manager"] < ROLE_RANK["owner"]


def test_roster_manager_identity():
    event = _event(role="member", owner_id=1)
    assert compute_event_permission(event, manager_list=[100]) == "manager"


def test_owner_roster_beats_manager_roster():
    event = _event(role="member", owner_id=1)
    role = compute_event_permission(event, owner_list=["100"], manager_list=["100"])
    assert role == "owner"


def test_bot_owner_still_wins_with_empty_rosters():
    event = _event(role="member", owner_id=100)
    assert compute_event_permission(event, owner_list=set(), manager_list=set()) == "owner"


def test_unlisted_user_keeps_group_role():
    event = _event(role="admin", owner_id=1)
    assert compute_event_permission(event, owner_list={"999"}, manager_list={"998"}) == "group_admin"


def test_manager_policy_requires_manager():
    mod = _make_module("manager")
    member = _perm_event()
    assert check_module_permission(mod, member) is False

    manager = _event()
    compute_event_permission(manager, manager_list={"100"})
    assert check_module_permission(mod, manager) is True

    owner = _event()
    compute_event_permission(owner, owner_list={"100"})
    assert check_module_permission(mod, owner) is True


def test_manager_policy_not_downgraded_in_private_chat():
    """manager 是名册身份而不是群角色，私聊里不该被降级成 member。"""
    mod = _make_module("manager")
    event = _event()
    event.event_type = "message_private"
    compute_event_permission(event, manager_list={"100"})
    assert check_module_permission(mod, event) is True

    other = _event()
    other.event_type = "message_private"
    compute_event_permission(other)
    assert check_module_permission(mod, other) is False


def test_roster_lists_parses_common_shapes():
    assert roster_lists({}) == (set(), set())
    assert roster_lists({"agent_owner_list": ["1", " 2 ", ""]}) == ({"1", "2"}, set())
    assert roster_lists({"agent_owner_list": "3,4"}) == ({"3", "4"}, set())
    assert roster_lists({"agent_manager_list": [5, 6]}) == (set(), {"5", "6"})
    # 配置对象异常不应让权限判断崩掉
    assert roster_lists(object()) == (set(), set())


def test_every_permission_policy_has_rank():
    """PERMISSIONS 里每档都必须有等级，否则 check_module_permission 会静默按 member 处理。"""
    for policy in PERMISSIONS:
        if policy == "everyone":
            continue
        assert policy in ROLE_RANK, f"权限档位 {policy} 没有 ROLE_RANK"
