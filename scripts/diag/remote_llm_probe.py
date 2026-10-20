"""检查远端 data/llm/<bot_id>/ 的历史与任务数据，判断哪些账号真正进入过 LLM 流水线。

用法: venv\\Scripts\\python.exe scripts\\remote_llm_probe.py 1\\data\\llm
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def _ts(v) -> str:
    try:
        return datetime.fromtimestamp(float(v)).strftime("%m-%d %H:%M:%S")
    except Exception:
        return str(v)


def main(root: str) -> None:
    base = Path(root)
    for acct in sorted(p for p in base.iterdir() if p.is_dir() and p.name.isdigit()):
        print("=" * 70)
        print(f"账号 {acct.name}")
        hist = acct / "history" / "history.db"
        if hist.is_file():
            con = sqlite3.connect(str(hist))
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            tables = [r[0] for r in cur.execute("select name from sqlite_master where type='table'")]
            print(f"  history.db 表: {tables}")
            for t in tables:
                try:
                    rows = cur.execute(f"select * from {t}").fetchall()  # noqa: S608
                except Exception as e:
                    print(f"    {t}: 读取失败 {e}")
                    continue
                print(f"    {t}: {len(rows)} 行")
                if not rows:
                    continue
                cols = list(rows[0].keys())
                tcol = next((c for c in ("updated_at", "created_at", "timestamp", "last_time", "time") if c in cols), None)
                recent = rows
                if tcol:
                    try:
                        recent = sorted(rows, key=lambda r: float(r[tcol] or 0))[-4:]
                    except Exception:
                        recent = rows[-4:]
                for r in recent:
                    d = dict(r)
                    keep = {k: (d[k] if not isinstance(d[k], str) or len(d[k]) < 60 else d[k][:60] + "...") for k in cols
                            if k in ("id", "session_id", "type", "task_id", "updated_at", "created_at", "last_time", "time", "bot_id")}
                    if tcol and tcol in keep:
                        keep[tcol] = f"{keep[tcol]} ({_ts(keep[tcol])})"
                    print("      ", json.dumps(keep, ensure_ascii=False))
            con.close()
        else:
            print("  无 history.db")
        for extra in ("tasks_data.json", "proactive_data.json"):
            f = acct / extra
            if f.is_file():
                print(f"  {extra}: {f.read_text(encoding='utf-8', errors='replace')[:300]}")


if __name__ == "__main__":
    main(sys.argv[1])
