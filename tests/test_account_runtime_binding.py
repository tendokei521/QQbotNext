"""账号↔连接↔Agent 运行时的绑定回归测试。

背景（线上问题）：连接是 index 级对象、会被换号复用，而 Agent 运行时是按账号
keyed 的长生命周期对象。两者绑定漂移时表现为「账号大幅变更后 agent 配置不生效、
LLM 不回复」。本文件固定住四条不变量：

1. 事件的账号取自 payload 的 self_id，不依赖连接 bot_id 的刷新时机；
2. AgentNode 用权威账号查运行时，查不到必须留痕（不许静默吞掉）；
3. 登录换号时运行时被重绑到当前连接，幽灵运行时被回收；
4. `/api/bots` 公布的 bot_id 与展示账号一致（离线回退上次登录快照）。
"""

from __future__ import annotations

from app.domain.events import PrivateMessageEvent
from app.infrastructure.onebot.codec import decode
from app.llm.manager import AgentManager
from app.modules.base import ModulePermission
from app.modules.nodes import AgentNode
from app.nodes.base import MessageContext


# --------------------------------------------------------------- 测试替身


class _FakeBot:
    """最小 IBot 替身：只暴露 codec/查询路径用到的字段。"""

    def __init__(self, index: int, bot_id: int | None) -> None:
        self.index = index
        self.bot_id = bot_id
        self.owner_id = None
        self.ws_url = f"ws://fake/{index}"
        self.status = "connected"
        self.login_info = {"user_id": bot_id, "nickname": "x"} if bot_id else {}
        self.all_group_list: list[int] = []
        self.all_group_list_info: list[dict] = []


class _CfgSvc:
    def __init__(self) -> None:
        self.data: dict = {}
        self.auth: dict = {}

    def get_module_config(self, module, bot_id):
        return dict(self.data.get((module, str(bot_id)), {}) or {})

    def set_module_config(self, module, bot_id, config, persist=True):
        self.data[(module, str(bot_id))] = dict(config)

    async def save_module_config(self, module, bot_id, config):
        self.data[(module, str(bot_id))] = dict(config)

    def get_module_authority(self, module, bot_id):
        return dict(self.auth.get((module, str(bot_id)), {}) or {})

    def set_module_authority(self, module, bot_id, authority):
        self.auth[(module, str(bot_id))] = dict(authority)


class _TaskMgr:
    def create_task(self, coro, **kw):
        return None


# ------------------------------------------------- 1. 事件账号 = payload.self_id


def test_codec_prefers_payload_self_id_over_stale_conn_bot_id():
    """连接还挂着旧账号(或 0)时，事件必须仍带 payload 里的权威账号。"""
    stale = _FakeBot(index=0, bot_id=3437542570)  # 连接缓存的账号已被换号
    event = decode(
        {"post_type": "message", "message_type": "private", "time": 1,
         "user_id": 100, "self_id": 3569937952, "message": []},
        stale,
    )
    assert event.self_id == 3569937952
    assert event.bot_id == 3569937952, "事件的 bot_id 应以 self_id 为准"
    assert event.account_id == 3569937952


def test_codec_falls_back_to_conn_bot_id_when_payload_lacks_self_id():
    """payload 没有 self_id（异常上报）时，用连接上的 bot_id 兜底。"""
    conn = _FakeBot(index=1, bot_id=3569937952)
    event = decode(
        {"post_type": "message", "message_type": "private", "time": 1,
         "user_id": 100, "message": []},
        conn,
    )
    assert event.self_id == 0
    assert event.bot_id == 3569937952
    assert event.account_id == 3569937952


# ------------------------------------------------- 2. AgentNode 账号解析与留痕


class _RtConfig:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.permission = ModulePermission()

    def get(self, key, default=None):
        return {"permission": "everyone"}.get(key, default)


class _Rt:
    def __init__(self, enabled=True):
        self.config = _RtConfig(enabled)


class _AgentMgr:
    """按账号存运行时的最小替身（记录查询用的 key）。"""

    def __init__(self, runtimes: dict) -> None:
        self._runtimes = runtimes
        self.queried: list = []

    def get_runtime(self, bot_id):
        self.queried.append(bot_id)
        return self._runtimes.get(bot_id)

    def runtimes(self):
        return dict(self._runtimes)


class _Gw:
    connections: dict = {}


class _WebuiCfg:
    def get_webui_config(self):
        return {}


def _priv(bot_id, self_id):
    return PrivateMessageEvent(
        event_type="message_private", message_type="private", time=1,
        user_id=100, self_id=self_id, message=[], bot=None, bot_id=bot_id,
    )


async def _run_node(node, event):
    async def next_():
        return None

    await node.process(MessageContext(event=event, bot=None, state={}), next_)


async def test_agent_node_resolves_runtime_by_payload_account_not_stale_conn_id():
    """连接 bot_id 是旧账号，但 payload 指向真实账号 → 必须命中真实账号的运行时。"""
    real = _Rt()
    mgr = _AgentMgr({3569937952: real})
    node = AgentNode(mgr, _WebuiCfg(), _Gw())

    called = []

    async def fake_handle(module, event):
        called.append(module)

    import app.llm as llm_pkg

    original = llm_pkg.handle
    llm_pkg.handle = fake_handle
    try:
        await _run_node(node, _priv(bot_id=3437542570, self_id=3569937952))
    finally:
        llm_pkg.handle = original

    assert mgr.queried == [3569937952], "应按权威账号查询运行时"
    assert called == [real], "应把事件交给该账号的运行时"


async def test_agent_node_logs_when_account_has_no_runtime(caplog):
    """账号没有运行时必须留痕——静默跳过正是线上「配置不生效」的观感来源。"""
    mgr = _AgentMgr({})
    node = AgentNode(mgr, _WebuiCfg(), _Gw())

    import logging

    with caplog.at_level(logging.WARNING):
        await _run_node(node, _priv(bot_id=3569937952, self_id=3569937952))

    assert any("无 Agent 运行时" in r.message for r in caplog.records)


# ------------------------------------------------- 3. 换号时的运行时重绑与回收


def test_bind_account_rebinds_runtime_to_the_live_connection(monkeypatch, tmp_path):
    """账号在 index 间漂移：运行时必须重绑到当前持有该账号的连接。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    mgr = AgentManager(_CfgSvc(), _TaskMgr())
    old_conn = _FakeBot(index=0, bot_id=3569937952)
    new_conn = _FakeBot(index=1, bot_id=3569937952)
    try:
        runtime = mgr.bind_account(3569937952, bot=old_conn)
        assert runtime.bot is old_conn

        same = mgr.bind_account(3569937952, bot=new_conn)
        assert same is runtime, "同一账号不应重建运行时"
        assert runtime.bot is new_conn, "应重绑到当前持有该账号的连接"
        assert runtime.ctx.bot is new_conn
    finally:
        mgr.shutdown()


def test_bind_account_evicts_runtime_whose_connection_was_taken_over(monkeypatch, tmp_path):
    """连接被换号：旧账号的运行时必须被回收，否则会按旧人设走新连接发言。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    mgr = AgentManager(_CfgSvc(), _TaskMgr())
    conn = _FakeBot(index=0, bot_id=3437542570)
    try:
        old_runtime = mgr.bind_account(3437542570, bot=conn)

        # 同一条连接重登成了另一个账号
        conn.bot_id = 3569937952
        new_runtime = mgr.bind_account(3569937952, bot=conn)

        assert mgr.get_runtime(3437542570) is None, "旧账号运行时应被回收"
        assert mgr.get_runtime(3569937952) is new_runtime
        assert new_runtime.bot is conn
    finally:
        mgr.shutdown()


def test_evicted_runtime_does_not_close_shared_session_manager(monkeypatch, tmp_path):
    """回收单个运行时的软停止不得关掉共享的会话管理器。

    ``SessionManager`` 是按 bot_id 的进程级单例，被同一账号的所有运行时共享；
    若回收时把它 ``close()`` 掉，随后登录重建的运行时（拿到同一个单例）会对着
    已关闭的历史库操作，报 "Cannot operate on a closed database" 并静默丢掉回复。
    """
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    mgr = AgentManager(_CfgSvc(), _TaskMgr())
    conn = _FakeBot(index=0, bot_id=3437542570)
    try:
        runtime = mgr.bind_account(3437542570, bot=conn)
        session_mgr = runtime.session_mgr

        # 触发回收（连接换号）
        conn.bot_id = 3569937952
        mgr.bind_account(3569937952, bot=conn)

        # 共享单例的历史库必须仍然可用（关掉的话这里会抛
        # "Cannot operate on a closed database"）
        session = session_mgr.create_session("private_1", "private")
        session_mgr.add_message("private_1", "user", "还在吗")
        session_mgr.history.save_session(session)
        assert session_mgr.get_history("private_1")
    finally:
        mgr.shutdown()


def test_detach_bot_clears_connection(monkeypatch, tmp_path):
    """解绑后运行时不得再持有旧连接（避免把消息发到已换号的 socket 上）。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    mgr = AgentManager(_CfgSvc(), _TaskMgr())
    conn = _FakeBot(index=0, bot_id=3437542570)
    try:
        runtime = mgr.bind_account(3437542570, bot=conn)
        runtime.detach_bot()
        assert runtime.bot is None
        assert runtime.ctx.bot is None
    finally:
        mgr.shutdown()


# ------------------------------------------------- 4. /api/bots 公布的账号一致性


def _effective_bot_id(conn, last_account):
    """直接借用 OneBotGateway 的账号公布逻辑（避开真实连接依赖）。"""
    from app.infrastructure.onebot.gateway import OneBotGateway

    return OneBotGateway._effective_bot_id(conn, last_account)


def test_effective_bot_id_falls_back_to_last_account_snapshot():
    """重连窗口里 conn.bot_id 还是 0：公布值应回退上次登录账号，而不是 0/null。"""
    conn = _FakeBot(index=0, bot_id=0)
    assert _effective_bot_id(conn, {"user_id": "3569937952"}) == 3569937952

    conn.bot_id = 3437542570
    assert _effective_bot_id(conn, {"user_id": "3569937952"}) == 3437542570, "实时值优先"

    assert _effective_bot_id(_FakeBot(index=1, bot_id=0), None) is None
    assert _effective_bot_id(_FakeBot(index=1, bot_id=0), {"user_id": ""}) is None
