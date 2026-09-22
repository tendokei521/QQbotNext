"""对远端 app.db 副本做只读诊断（账号/配置/运行时解析）。

用法: venv\\Scripts\\python.exe scripts\\remote_check.py <app.db 路径>
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


async def main(db_file: str) -> None:
    from app.core.task_manager import TaskManager
    from app.infrastructure.config.config_service import ConfigService
    from app.infrastructure.persistence.database import Database
    from app.llm.config import AGENT_CONFIG_MODULE
    from app.llm.manager import AgentManager

    db = Database(db_file)
    await db.connect()
    cs = ConfigService(db, ROOT)
    await cs.init()

    print("== bots（index → ws_url / auto_connect）==")
    for i, b in enumerate(cs.get_bots_public()):
        print(f"  index {i}: {b.get('ws_url')} token={b.get('access_token')!r} auto={b.get('auto_connect')}")
    print("== 上次登录账号快照 ==")
    for idx, info in sorted(cs.get_bot_accounts().items()):
        print(f"  index {idx} → {info.get('user_id')} ({info.get('nickname')})")
    print("== agent 配置存在的 bot_id ==")
    stored = cs.get_all_module_configs(AGENT_CONFIG_MODULE)
    for bid in sorted(stored, key=str):
        print(f"  {bid}  (键数 {len(stored[bid])})")
    print("== single_service / multi_group ==")
    webui = cs.get_webui_config()
    print("  single_service:", json.dumps(webui.get("single_service"), ensure_ascii=False))
    print("  multi_group   :", json.dumps(webui.get("multi_group"), ensure_ascii=False))

    print()
    print("== 按 bot_id 复现 AgentRuntime 解析 ==")
    mgr = AgentManager(config_service=cs, task_manager=TaskManager())
    for bid in sorted(stored, key=str):
        key = int(bid) if str(bid).isdigit() else bid
        rt = mgr.ensure_runtime(key)
        cfg = rt.config
        chain = rt.provider_chain()
        print(f"--- bot_id {bid} ---")
        print(f"    enabled={cfg.enabled} permission={cfg.get('permission')} "
              f"group_enable={cfg.get('group_enable')} trigger_at={cfg.get('trigger_at')} "
              f"stream={cfg.get('stream_output')} pool={cfg.get('provider_model_pool')}")
        if chain:
            c0 = chain[0]
            print(f"    provider_chain[0]: model={c0.get('model')!r} api_base={c0.get('api_base')!r} "
                  f"api_key={'有' if c0.get('api_key') else '**无**'}")
        else:
            print("    provider_chain 为空 → 流水线会在 api_key 检查处静默 return")
        print(f"    运行时键类型={type(key).__name__}  运行时可查(数字)={mgr.get_runtime(key) is not None} "
              f"运行时可查(字符串)={mgr.get_runtime(bid) is not None}")
    await db.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
