"""账号↔连接↔Agent 运行时的绑定回归测试。

背景（线上问题）：连接是可以被换号复用的，而 Agent 运行时是按**账号** keyed 的
长生命周期对象。两者绑定漂移时表现为「账号大幅变更后 agent 配置不生效、LLM 不回复」。

自 v4 起「账号」= 用户设定的 **account_id**（字符串，不可变），不再用数组下标、
也不再用登录后的 QQ 号当身份。本文件固定住四条不变量：

1. 事件的 `account_id` 取自连接（账号身份），`login_id` 才是 payload 里的 QQ；
2. AgentNode 用 `event.account_id` 查运行时，查不到必须留痕（不许静默吞掉）；
3. 登录换号时运行时被重绑到当前连接，幽灵运行时被回收；
4. `/api/bots` 公布的 QQ 与展示账号一致（离线回退上次登录快照）。
"""

from __future__ import annotations

from app.agent.runtime import AgentManager
from app.domain.events import PrivateMessageEvent
from app.infrastructure.onebot.codec import decode
from app.plugin.base import ModulePermission
from app.plugin.nodes.base import MessageContext
from app.plugin.nodes.inbound import AgentNode

# --------------------------------------------------------------- 测试替身

ACC = "main"
QQ_OLD = 3437542570
QQ_NEW = 3569937952


class _FakeBot:
    """最小 IBot 替身：只暴露 codec/查询路径用到的字段。"""

    def __init__(self, account_id: str, bot_id: int | None) -> None:
        self.account_id = account_id
        self.account_name = f"账号{account_id}"
        self.bot_id = bot_id
        self.owner_id = None
        self.ws_url = f"ws://fake/{account_id}"
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


# --------------------------------------- 1. 账号身份 = 连接，QQ = payload.self_id


def test_codec_keeps_account_identity_while_qq_comes_from_payload():
    """连接上缓存的 QQ 可能过期，但**账号身份**来自连接本身（account_id）。"""
    stale = _FakeBot(account_id=ACC, bot_id=QQ_OLD)  # 连接缓存的 QQ 已被换号
    event = decode(
        {"post_type": "message", "message_type": "private", "time": 1,
         "user_id": 100, "self_id": QQ_NEW, "message": []},
        stale,
    )
    assert event.self_id == QQ_NEW
    assert event.bot_id == QQ_NEW, "事件的 bot_id 应以 self_id 为准"
    assert event.login_id == QQ_NEW
    assert event.account_id == ACC, "账号身份必须来自连接，不能是 QQ 号"


def test_codec_falls_back_to_conn_bot_id_when_payload_lacks_self_id():
    """payload 没有 self_id（异常上报）时，用连接上的 bot_id 兜底 QQ。"""
    conn = _FakeBot(account_id="acc9", bot_id=QQ_NEW)
    event = decode(
        {"post_type": "message", "message_type": "private", "time": 1,
         "user_id": 100, "message": []},
        conn,
    )
    assert event.self_id == 0
    assert event.bot_id == QQ_NEW
    assert event.login_id == QQ_NEW
    assert event.account_id == "acc9"


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

    def get_runtime(self, account_id):
        self.queried.append(account_id)
        return self._runtimes.get(account_id)

    def runtimes(self):
        return dict(self._runtimes)


class _Gw:
    connections: dict = {}


class _WebuiCfg:
    def get_webui_config(self):
        return {}


def _priv(account_id, self_id, bot_id=None):
    return PrivateMessageEvent(
        event_type="message_private", message_type="private", time=1,
        user_id=100, self_id=self_id, message=[], bot=None,
        bot_id=bot_id if bot_id is not None else self_id,
        account_id=account_id,
    )


async def _run_node(node, event):
    async def next_():
        return None

    await node.process(MessageContext(event=event, bot=None, state={}), next_)


async def test_agent_node_resolves_runtime_by_account_id():
    """事件按 account_id 命中运行时——即使登录的 QQ 变了，配置仍然跟着账号走。"""
    real = _Rt()
    mgr = _AgentMgr({ACC: real})
    node = AgentNode(mgr, _WebuiCfg(), _Gw())

    called = []

    async def fake_handle(module, event):
        called.append(module)

    import app.agent.loop.chat as llm_chat

    original = llm_chat.handle
    llm_chat.handle = fake_handle
    try:
        await _run_node(node, _priv(account_id=ACC, self_id=QQ_NEW))
    finally:
        llm_chat.handle = original

    assert mgr.queried == [ACC], "应按账号 id 查询运行时"
    assert called == [real], "应把事件交给该账号的运行时"


async def test_agent_node_logs_when_account_has_no_runtime(caplog):
    """账号没有运行时必须留痕——静默跳过正是线上「配置不生效」的观感来源。"""
    mgr = _AgentMgr({})
    node = AgentNode(mgr, _WebuiCfg(), _Gw())

    import logging

    with caplog.at_level(logging.WARNING):
        await _run_node(node, _priv(account_id=ACC, self_id=QQ_NEW))

    assert any("无 Agent 运行时" in r.message for r in caplog.records)


# ------------------------------------------------- 3. 换号时的运行时重绑与回收


def test_bind_account_rebinds_runtime_to_the_live_connection(monkeypatch, tmp_path):
    """同一账号换了连接（换号/重连）：运行时必须重绑到当前连接。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    mgr = AgentManager(_CfgSvc(), _TaskMgr())
    old_conn = _FakeBot(account_id=ACC, bot_id=QQ_NEW)
    new_conn = _FakeBot(account_id=ACC, bot_id=QQ_NEW)
    try:
        runtime = mgr.bind_account(ACC, bot=old_conn)
        assert runtime.bot is old_conn

        same = mgr.bind_account(ACC, bot=new_conn)
        assert same is runtime, "同一账号不应重建运行时"
        assert runtime.bot is new_conn, "应重绑到当前持有该账号的连接"
        assert runtime.ctx.bot is new_conn
    finally:
        mgr.shutdown()


def test_bind_account_evicts_runtime_whose_connection_was_taken_over(monkeypatch, tmp_path):
    """连接被换号：旧账号的运行时必须被回收，否则会按旧人设走新连接发言。"""
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    mgr = AgentManager(_CfgSvc(), _TaskMgr())
    conn = _FakeBot(account_id="accA", bot_id=QQ_OLD)
    try:
        old_runtime = mgr.bind_account("accA", bot=conn)

        # 同一条连接重登成了「另一个账号」（连接被别的账号接管）
        conn.account_id = "accB"
        conn.bot_id = QQ_NEW
        new_runtime = mgr.bind_account("accB", bot=conn)

        assert mgr.get_runtime("accA") is None, "旧账号运行时应被回收"
        assert mgr.get_runtime("accB") is new_runtime
        assert new_runtime.bot is conn
        assert old_runtime is not new_runtime
    finally:
        mgr.shutdown()


def test_evicted_runtime_does_not_close_shared_session_manager(monkeypatch, tmp_path):
    """回收单个运行时的软停止不得关掉共享的会话管理器。

    ``SessionManager`` 是按账号的进程级单例，被同一账号的所有运行时共享；
    若回收时把它 ``close()`` 掉，随后登录重建的运行时（拿到同一个单例）会对着
    已关闭的历史库操作，报 "Cannot operate on a closed database" 并静默丢掉回复。
    """
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    mgr = AgentManager(_CfgSvc(), _TaskMgr())
    conn = _FakeBot(account_id="accA", bot_id=QQ_OLD)
    try:
        runtime = mgr.bind_account("accA", bot=conn)
        session_mgr = runtime.session_mgr

        # 触发回收（连接被另一个账号接管）
        conn.account_id = "accB"
        conn.bot_id = QQ_NEW
        mgr.bind_account("accB", bot=conn)

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
    conn = _FakeBot(account_id=ACC, bot_id=QQ_OLD)
    try:
        runtime = mgr.bind_account(ACC, bot=conn)
        runtime.detach_bot()
        assert runtime.bot is None
        assert runtime.ctx.bot is None
    finally:
        mgr.shutdown()


# ------------------------------------------------- 4. /api/bots 公布的 QQ 一致性


def _effective_bot_id(conn, last_account):
    """直接借用 OneBotGateway 的账号公布逻辑（避开真实连接依赖）。"""
    from app.infrastructure.onebot.gateway import OneBotGateway

    return OneBotGateway._effective_bot_id(conn, last_account)


def test_effective_bot_id_falls_back_to_last_account_snapshot():
    """重连窗口里 conn.bot_id 还是 0：公布值应回退上次登录 QQ，而不是 0/null。"""
    conn = _FakeBot(account_id=ACC, bot_id=0)
    assert _effective_bot_id(conn, {"user_id": "3569937952"}) == 3569937952

    conn.bot_id = QQ_OLD
    assert _effective_bot_id(conn, {"user_id": "3569937952"}) == QQ_OLD, "实时值优先"

    assert _effective_bot_id(_FakeBot(account_id="x", bot_id=0), None) is None
    assert _effective_bot_id(_FakeBot(account_id="x", bot_id=0), {"user_id": ""}) is None
