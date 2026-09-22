"""对远端真实配置做端到端回归：复现「账号在 index 间互换 / 同 index 换号」后
两个账号是否都还能正常进入 LLM 流水线，以及回复是否走「该账号自己的连接」。

用法: venv\\Scripts\\python.exe scripts\\diag_account_swap_e2e.py 1\\data\\app.db
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GROUP = 466052056
ASKER = 1901691195
BOT_A = 3569937952
BOT_B = 3437542570


class FakeConn:
    """满足事件/发送路径需要的连接替身。"""

    def __init__(self, index: int, bot_id: int, nickname: str = "") -> None:
        self.index = index
        self.bot_id = bot_id
        self.owner_id = None
        self.ws_url = f"ws://fake/{index}"
        self.status = "connected"
        self.auto_connect = False
        self.login_info = {"user_id": bot_id, "nickname": nickname}
        self.all_group_list = [GROUP]
        self.all_group_list_info = []
        self.websocket = object()  # 非空即"可发送"
        self.reconnect_attempts = 0
        self.last_error = None
        self.sent: list[dict] = []
        self.api_calls: list[str] = []

    async def send_group_msg(self, group_id, message, auto_escape=False):
        text = getattr(message, "text", None) or (message.get("text") if isinstance(message, dict) else str(message))
        print(f"      >>> 连接 index={self.index}（账号 {self.bot_id}）发出: {text}", flush=True)
        self.sent.append({"group_id": group_id, "text": text})
        return {"status": "ok", "data": {"message_id": len(self.sent)}}

    async def send_private_msg(self, user_id, message, auto_escape=False):
        self.sent.append({"user_id": user_id, "text": getattr(message, "text", str(message))})
        return {"status": "ok"}

    async def call_api(self, action: str, params: dict | None = None):
        self.api_calls.append(action)
        return {"status": "ok", "data": {}}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        async def _stub(*args, **kwargs):
            self.api_calls.append(name)
            return {"status": "ok", "data": {}}

        return _stub


def make_group_msg(conn: FakeConn, *, text: str, at: int, message_id: int, t: int):
    from app.domain.events import GroupInfo, GroupMessageEvent, UserInfo
    from app.domain.message import MessageSegment

    segs = [MessageSegment("at", {"qq": str(at)}), MessageSegment("text", {"text": text})]
    return GroupMessageEvent(
        event_type="message_group", post_type="message", time=t,
        user_id=ASKER, self_id=conn.bot_id, bot=conn, bot_id=conn.bot_id,
        bot_index=conn.index, owner_id=None, raw={},
        message_type="group", sub_type="normal", message_id=message_id,
        raw_message=text, message=segs,
        user=UserInfo(user_id=ASKER, nickname="老师", card="", role="member"),
        group=GroupInfo(group_id=GROUP, group_name="测试群", user_role="member"),
    )


async def main(db_file: str) -> None:
    from app.bootstrap import build_container
    from app.core.settings import Settings
    from app.infrastructure.config.config_service import ConfigService
    from app.infrastructure.persistence.database import Database
    from app.llm.manager import AgentManager
    from app.modules.dispatcher import ModuleDispatcher
    from app.services.bot_service import BotService

    tmp = Path(tempfile.mkdtemp(prefix="qqbot-e2e-"))
    shutil.copy2(db_file, tmp / "app.db")
    os.environ["QQBOT_LLM_DATA_DIR"] = str(tmp / "llm")
    (tmp / "module" / "modules").mkdir(parents=True, exist_ok=True)

    settings = Settings(
        project_root=tmp, db_path=tmp / "app.db",
        modules_dir=tmp / "module" / "modules", plugins_dir=tmp / "module" / "plugins",
        log_dir=tmp / "logs", uninstalled_modules_file=tmp / "module" / "uninstalled.json",
    )
    container = build_container(settings)
    import app.bootstrap as bs

    bs._container = container
    db = container.get(Database)
    await db.connect()
    cs = container.get(ConfigService)
    await cs.init()

    # 用真实配置的 provider 服务覆盖（app.db 已是远端库，直接可用）
    dispatcher: ModuleDispatcher = container.get(ModuleDispatcher)
    agent_manager: AgentManager = container.get(AgentManager)
    bot_service: BotService = container.get(BotService)
    gateway = bot_service.gateway

    calls: list[str] = []

    import app.llm.providers as providers_pkg

    async def patched_iter(chain, messages, **kwargs):
        calls.append("llm-call")
        yield type("Ev", (), {"type": "text", "text": "模拟回复", "tool_call": None})()

    providers_pkg.iter_stream_with_fallback = patched_iter
    import app.llm.chat as chat_mod

    chat_mod.iter_stream_with_fallback = patched_iter

    async def probe(conn: FakeConn, tag: str, *, at: int, t: int, wait: float = 12.0):
        before = len(conn.sent)
        ev = make_group_msg(conn, text=" 在不在", at=at, message_id=1000 + t, t=t)
        await dispatcher.dispatch(ev)
        rt_dbg = agent_manager.get_runtime(at)
        p = getattr(rt_dbg, "llm_pipeline", None) if rt_dbg else None
        print(f"      [dbg] {tag} interrupt_enabled={getattr(rt_dbg, 'interrupt_enabled', '?')} "
              f"gen={dict(p._session_generation) if p else {}}", flush=True)
        await asyncio.sleep(wait)
        if p is not None:
            print(f"      [dbg] {tag} after-wait gen={dict(p._session_generation)}", flush=True)
        rt = agent_manager.get_runtime(at)
        print(f"  [{tag}] @{at} → runtime={'有' if rt else '无'} "
              f"该连接发出={len(conn.sent) - before} 条"
              f"{'  ' + conn.sent[before]['text'] if len(conn.sent) > before else ''}", flush=True)

    if os.environ.get("E2E_SINGLE"):
        print("=" * 70)
        print("阶段 S：单账号隔离（仅 A 在 index0，无同群第二账号干扰）")
        print("=" * 70)
        conn_s = FakeConn(0, BOT_A, "小鳥遊ホシノ")
        gateway.connections.clear()
        gateway.connections[0] = conn_s
        await bot_service.on_bot_login(conn_s)
        await probe(conn_s, "single-A", at=BOT_A, t=99, wait=8.0)
        print("LLM 请求次数:", len(calls))
        print("临时目录:", tmp)
        await db.close()
        return

    print("=" * 70)
    print("阶段 A：远端当前状态（index0=3437542570, index1=3569937952）")
    print("=" * 70)
    conn0 = FakeConn(0, BOT_B, "才羽 ミドリ")
    conn1 = FakeConn(1, BOT_A, "小鳥遊ホシノ")
    gateway.connections[0] = conn0
    gateway.connections[1] = conn1
    await bot_service.on_bot_login(conn0)
    await bot_service.on_bot_login(conn1)
    print("  已装配账号:", sorted(str(k) for k in agent_manager.runtimes()))
    await probe(conn0, "index0", at=BOT_B, t=10)
    await probe(conn1, "index1", at=BOT_A, t=11)

    print()
    print("=" * 70)
    print("阶段 B：两账号互换（index0<->index1）——历史日志里出问题的时刻")
    print("=" * 70)
    conn0.bot_id, conn1.bot_id = BOT_A, BOT_B
    conn0.login_info = {"user_id": BOT_A, "nickname": "小鳥遊ホシノ"}
    conn1.login_info = {"user_id": BOT_B, "nickname": "才羽 ミドリ"}
    conn0.index, conn1.index = 0, 1
    gateway.connections[0], gateway.connections[1] = conn0, conn1
    await bot_service.on_bot_login(conn0)
    await bot_service.on_bot_login(conn1)
    print("  已装配账号:", sorted(str(k) for k in agent_manager.runtimes()))
    for key in (BOT_A, BOT_B):
        rt = agent_manager.get_runtime(key)
        bound = getattr(rt, "bot", None)
        print(f"  runtime {key} 绑定 index={getattr(bound, 'index', None)} "
              f"conn.bot_id={getattr(bound, 'bot_id', None)} "
              f"是 gatewdy 当前连接={bound is gateway.connections.get(getattr(bound, 'index', -1))}")
    await probe(conn0, "index0(现为A)", at=BOT_A, t=12)
    await probe(conn1, "index1(现为B)", at=BOT_B, t=13)

    print()
    print("=" * 70)
    print("阶段 C：index0 换成第三个账号，再换回 A（删除/新增 index 场景）")
    print("=" * 70)
    conn_c = FakeConn(0, 3841308038, "天童柯伊")
    gateway.connections[0] = conn_c
    await bot_service.on_bot_login(conn_c)
    print("  已装配账号:", sorted(str(k) for k in agent_manager.runtimes()))
    await probe(conn_c, "index0(新号C)", at=3841308038, t=14)

    conn_a2 = FakeConn(0, BOT_A, "小鳥遊ホシノ")
    gateway.connections[0] = conn_a2
    await bot_service.on_bot_login(conn_a2)
    print("  已装配账号:", sorted(str(k) for k in agent_manager.runtimes()))
    await probe(conn_a2, "index0(换回A)", at=BOT_A, t=15)

    print()
    print("LLM 请求次数:", len(calls))
    print("临时目录:", tmp)
    await db.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
