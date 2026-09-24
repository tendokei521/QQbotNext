"""诊断「点了断开，过一会儿账号又自己连上」。

原理：`_supervise` 是固定 10s 周期的循环（`await asyncio.sleep(10)` + 循环体耗时，
实测节拍 ≈ 10.00x 秒，相位随循环体耗时缓慢漂移）。它会把「status=disconnected 且
auto_connect=True」的连接当成掉线自动连回，因此这种「假重连」有两个指纹：

1. 同一 index 的「连接成功」时间戳落在 ≈10s 的节拍网格上（相邻多次间隔 ≈10.0x 秒）；
2. 「断开连接 → 连接成功」的间隔 ≤10.5s，且**明显大于**同一日志里直接点「重连」的
   握手耗时（后者是同一请求内的断开+连接，通常 <1.5s）。

对照：用户自己点「重连」产生的断开→连接，间隔 = WS 握手耗时（局域网实测 0.03~1s）。

用法: venv\\Scripts\\python.exe scripts\\diag_disconnect_pairs.py 1\\log\\qqbot-logs-20260924071806
     （参数是日志目录，递归读取其中所有 *.log）
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LINE = re.compile(
    r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+) - \w+ - \[Main\] 机器人索引: (\d+) (断开连接|连接成功|连接失败)"
)
FMT = "%Y-%m-%d %H:%M:%S.%f"
# 直接点「重连」= 同一 HTTP 请求内 disconnect+connect，握手耗时上限（超过就算嫌疑）
RECONNECT_HANDSHAKE_MAX = 1.5
# 监督循环周期上界（sleep(10) + 循环体耗时），超过它不可能是监督循环干的
SUPERVISE_GAP_MAX = 10.5


def load_events(log_dir: Path) -> list[tuple[datetime, int, str]]:
    events: list[tuple[datetime, int, str]] = []
    seen: set[str] = set()
    for path in sorted(log_dir.rglob("*.log")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = LINE.search(line)
            if not m:
                continue
            key = line.strip()
            if key in seen:  # 同一行可能同时出现在 debug.log / user.log
                continue
            seen.add(key)
            events.append((datetime.strptime(m.group(1), FMT), int(m.group(2)), m.group(3)))
    events.sort(key=lambda e: e[0])
    return events


def pair_disconnects(events: list[tuple[datetime, int, str]]) -> list[tuple]:
    by_index: dict[int, list] = {}
    for e in events:
        by_index.setdefault(e[1], []).append(e)

    pairs = []
    for index, evs in sorted(by_index.items()):
        for pos, (t0, _i, kind) in enumerate(evs):
            if kind != "断开连接":
                continue
            nxt = next((e for e in evs[pos + 1:] if e[2] in ("连接成功", "连接失败")), None)
            if nxt is None:
                continue
            pairs.append((index, t0, nxt[0], nxt[2], (nxt[0] - t0).total_seconds()))
    pairs.sort(key=lambda p: p[1])
    return pairs


def main(log_dir: str) -> int:
    root = Path(log_dir)
    if not root.exists():
        print(f"日志目录不存在: {root}")
        return 1

    events = load_events(root)
    disconnects = [e for e in events if e[2] == "断开连接"]
    connects = [e for e in events if e[2] == "连接成功"]
    print(f"扫描 {root}: 断开 {len(disconnects)} 次 / 连接成功 {len(connects)} 次")
    if not events:
        print("未匹配到连接事件（确认日志里含「机器人索引: N 断开连接/连接成功」）")
        return 1

    pairs = pair_disconnects(events)
    suspect = [p for p in pairs if RECONNECT_HANDSHAKE_MAX < p[4] <= SUPERVISE_GAP_MAX]
    print(f"\n断开 → 连接 配对 {len(pairs)} 组：")
    for index, t0, t1, kind, gap in pairs:
        if gap <= RECONNECT_HANDSHAKE_MAX:
            tag = "（握手耗时内 → 像是点了「重连」）"
        elif gap <= SUPERVISE_GAP_MAX:
            tag = "（≤10.5s → 疑似监督循环自动连回）"
        else:
            tag = "（间隔超过监督循环周期 → 另有原因/重启）"
        print(f"  #{index} {t0} 断开 -> {t1} {kind}  gap={gap:6.2f}s {tag}")

    # 节拍网格：把「连接成功」按 10s 取余，若大量落在同一相位即为监督循环节拍
    phases = sorted(round(t.timestamp() % 10, 2) for t, _i, k in connects if k == "连接成功")
    print("\n连接成功的 10s 相位（同一相位扎堆 = 监督循环节拍）:")
    print("  " + ", ".join(f"{p:.2f}" for p in phases))

    print(f"\n结论：疑似被监督循环连回 {len(suspect)} 组")
    if suspect:
        print("  若这些断开都是用户点的「断开」，则是本 bug；升级后可复验此数为 0。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "logs"))
