"""忠实复现：用远端 app.db + 真实网关接收/派发链路，重放「群聊 @ 不触发」。

与 diag_account_swap_e2e 的差别：本脚本走 gateway._process_event（含同群多 Bot
去重、忽略标记），而不是直接调 dispatcher，因此能定位「事件在派发前被谁吞掉」。

用法: venv\\Scripts\\python.exe scripts\\diag_group_dispatch.py 1\\data\\app.db
"""

from __future__ import annotations

import asyncio
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
    def __init__(self, index: int, bot_id: int, nickname: str = "") -> None:
        self.index = index
        self.bot_id = bot_id
        self.owner_id = None
        self.ws_url = f"ws://fake/{index}"
        self.status = "connected"
        self.auto_connect = False
        self.login_info = {"user_id": bot_id, "nickname": nickname}
        self.all_group_list = [GROUP]
        self.all_group_list_info = [{"group_id": GROUP, "group_name": "超自然现象调查部"}]
        self.websocket = object()
        self.reconnect_attempts = 0
        self.last_error = None
        self.sent: list[dict] = []
        self.api_calls: list[str] = []

    async def send_group_msg(self, group_id, message, auto_escape=False):
        text = getattr(message, "text", None) or (message.get("text") if isinstance(message, dict) else str(message))
        self.sent.append({"group_id": group_id, "text": text})
        print(f"      >>> index={self.index}（账号 {self.bot_id}）发出: {text}", flush=True)
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


def make_payload(conn: FakeConn, *, text: str, at: int, message_id: int, t: int) -> dict:
    return {
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "time": t,
        "self_id": conn.bot_id,
        "user_id": ASKER,
        "message_id": message_id,
        "raw_message": text,
        "group_id": GROUP,
        "message": [
            {"type": "at", "data": {"qq": str(at)}},
            {"type": "text", "data": {"text": text}},
        ],
        "sender": {"user_id": ASKER, "nickname": "老师", "card": "", "role": "member"},
    }


async def main(db_file: str) -> None:
    from app.bootstrap import build_container
    from app.core.settings import Settings
    from app.infrastructure.config.config_service import ConfigService
    from app.infrastructure.onebot.codec import decode
    from app.infrastructure.persistence.database import Database
    from app.llm.manager import AgentManager
    from app.services.bot_service import BotService

    tmp = Path(tempfile.mkdtemp(prefix="qqbot-grp-"))
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

    bot_service: BotService = container.get(BotService)
    gateway = bot_service.gateway
    agent_manager: AgentManager = container.get(AgentManager)

    # 把网关的派发链路接到真实 dispatcher（与 bootstrap 一致）
    from app.modules.dispatcher import ModuleDispatcher

    dispatcher: ModuleDispatcher = container.get(ModuleDispatcher)
    gateway.dispatch_handler = dispatcher.dispatch

    import app.llm.providers as providers_pkg

    calls: list[str] = []

    async def patched_iter(chain, messages, **kwargs):
        calls.append("llm")
        yield type("Ev", (), {"type": "text", "text": "模拟回复", "tool_call": None})()

    providers_pkg.iter_stream_with_fallback = patched_iter
    import app.llm.chat as chat_mod

    chat_mod.iter_stream_with_fallback = patched_iter

    # 覆写去重，暴露它到底放行还是吞掉（只影响本复现脚本的观测）
    real_wait = gateway._wait_for_message

    async def traced_wait(event, conn):
        result = await real_wait(event, conn)
        print(f"      [dedup] index={conn.index} 账号={conn.bot_id} → "
              f"{'放行' if result is not None else '吞掉(None)'}", flush=True)
        return result

    gateway._wait_for_message = traced_wait

    # 登录两个账号（index0=B 3437542570, index1=A 3569937952，与线上 13:32 一致）
    conn0 = FakeConn(0, BOT_B, "才羽 ミドリ")
    conn1 = FakeConn(1, BOT_A, "小鳥遊ホシノ")
    gateway.connections[0] = conn0
    gateway.connections[1] = conn1
    await bot_service.on_bot_login(conn0)
    await bot_service.on_bot_login(conn1)
    print("已装配运行时:", sorted(str(k) for k in agent_manager.runtimes()))
    print()

    async def replay(text: str, at: int, mid_base: int, t: int, tag: str) -> None:
        print(f"== {tag}: @{at} 「{text}」（两条事件几乎同时到达）==")
        before = [len(conn0.sent), len(conn1.sent)]
        for conn in (conn1, conn0):   # 线上这条是 #1 先到
            payload = make_payload(conn, text=text, at=at, message_id=mid_base + conn.index, t=t)
            event = decode(payload, conn)
            print(f"   事件: index={conn.index} 账号={conn.bot_id} self_id={event.self_id} "
                  f"bot_id={event.bot_id} account_id={event.account_id}", flush=True)
            await gateway._process_event(event, conn, None, _NullLogger())
        await asyncio.sleep(10)
        print(f"   → index0 发出 {len(conn0.sent) - before[0]} 条, "
              f"index1 发出 {len(conn1.sent) - before[1]} 条", flush=True)
        for c in (conn0, conn1):
            for item in c.sent[before[c.index == 1 and 1 or 0]:]:
                pass
        print()

    await replay("在吗（抱）", BOT_A, 9000, 1789000000, "群聊 @A(3569937952)")
    async def replay2(text, at, mid_base, t, tag):
        print(f"== {tag}: @{at} 「{text}」==")
        sent0, sent1 = len(conn0.sent), len(conn1.sent)
        for conn in (conn0, conn1):
            payload = make_payload(conn, text=text, at=at, message_id=mid_base + conn.index, t=t)
            event = decode(payload, conn)
            await gateway._process_event(event, conn, None, _NullLogger())
        await asyncio.sleep(10)
        print(f"   → index0(账号{conn0.bot_id}) 发出 {len(conn0.sent)-sent0} 条, "
              f"index1(账号{conn1.bot_id}) 发出 {len(conn1.sent)-sent1} 条", flush=True)
        print()

    await replay2("在吗", BOT_B, 9500, 1789000100, "群聊 @B(3437542570)")
    print("LLM 请求次数:", len(calls))
    await db.close()


class _NullLogger:
    def debug(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        print("   [warn]", a, flush=True)

    def error(self, *a, **k):
        print("   [error]", a, flush=True)

    def exception(self, *a, **k):
        print("   [exc]", a, flush=True)

    def prefix(self, *_a, **_k):
        return self

    def add_info(self, *_a, **_k):
        return self


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
