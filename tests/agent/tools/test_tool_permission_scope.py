"""工具级权限 / 作用域判定的回归测试。

## 事故一（本文件锁住）

``ToolSpec.allows()`` 的 docstring 写着"主动/定时等无事件场景默认放行"，
但代码把 ``event is None`` 的放行判断排在了**作用域检查之后**。
于是没声明 ``scopes=("*",)`` 的工具（``memory_*`` / ``knowledge_*`` /
``schedule_task``…）在主动消息与定时任务路径上被判成
"当前会话类型不在适用范围"，模型收到 ``error: 无权限调用工具 …`` 而静默放弃。

主动消息/定时任务的 ``ToolContext`` 天生没有 ``event``（它们不是由某条群消息触发的），
所以这条路径上"有没有事件"必须等价于"放行"。
"""

from __future__ import annotations

from app.agent.tools.spec import ToolContext, ToolSpec


def _spec(**kwargs) -> ToolSpec:
    async def _handler(ctx, args):  # pragma: no cover - 只测判定，不执行
        return "ok"

    params = {"type": "object", "properties": {}}
    return ToolSpec(name="t", description="", parameters=params, handler=_handler, **kwargs)


class _GroupMember:
    event_type = "message_group"
    permission_role = "member"
    is_bot_owner = False
    group = object()
    user_id = 1


class _GroupAdmin(_GroupMember):
    permission_role = "group_admin"


class _PrivateUser:
    event_type = "message_private"
    permission_role = "member"
    is_bot_owner = False
    user_id = 1


class _Owner(_GroupMember):
    permission_role = "owner"
    is_bot_owner = True


# ---------- 无事件场景（主动消息 / 定时任务 / 框架内部）----------


def test_no_event_allows_tool_without_wildcard_scope():
    """主动/定时路径没有事件：默认作用域工具也必须放行（历史事故一）。"""
    spec = _spec(permission="everyone")          # 默认 scopes=("group","private")
    assert spec.scopes == ("group", "private")
    assert spec.allows(ToolContext(event=None)) is True
    assert spec.allowed_for_event(None) is True


def test_no_event_allows_admin_tool_without_event():
    """没有主语就没有权限判定：group_admin 工具在无事件路径同样放行。"""
    spec = _spec(permission="group_admin", scopes=["group"])
    assert spec.allows(ToolContext(event=None)) is True


def test_missing_context_allows_framework_tool():
    """不带 ToolContext（框架直接调）时保留旧行为。"""
    assert _spec(permission="owner").allows(None) is True


# ---------- 有事件场景：作用域 ----------


def test_scope_rejects_other_session_type():
    spec = _spec(permission="everyone", scopes=["private"])
    assert spec.allows(ToolContext(event=_PrivateUser())) is True
    assert spec.allows(ToolContext(event=_GroupMember())) is False


def test_wildcard_scope_allows_both():
    spec = _spec(permission="everyone", scopes=["*"])
    assert spec.allows(ToolContext(event=_PrivateUser())) is True
    assert spec.allows(ToolContext(event=_GroupMember())) is True


# ---------- 有事件场景：权限等级 ----------


def test_group_admin_tool_denies_member_and_allows_admin():
    spec = _spec(permission="group_admin", scopes=["group"])
    assert spec.allows(ToolContext(event=_GroupMember())) is False
    assert spec.allows(ToolContext(event=_GroupAdmin())) is True


def test_group_admin_tool_downgrades_in_private_chat():
    """私聊里没有群角色可依，群管理类工具按最低等级放行（既有语义）。"""
    spec = _spec(permission="group_admin", scopes=["*"])
    assert spec.allows(ToolContext(event=_PrivateUser())) is True


def test_owner_tool_requires_bot_owner():
    spec = _spec(permission="owner", scopes=["*"])
    assert spec.allows(ToolContext(event=_GroupAdmin())) is False
    assert spec.allows(ToolContext(event=_Owner())) is True


def test_explicit_scope_wins_over_event_inference():
    """调用方显式给 scope 时以调用方为准（resolve_tool_state 的 ⑤ 层就是这样用的）。"""
    spec = _spec(permission="everyone", scopes=["group"])
    # 事件是私聊，但调用方说"这是群作用域"——按调用方的判定走
    assert spec.allowed_for_event(_PrivateUser(), scope="group") is True
    assert spec.allowed_for_event(_GroupMember(), scope="private") is False
