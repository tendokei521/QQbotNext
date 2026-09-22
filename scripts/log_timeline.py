"""把 gateway 日志整理成时间线：每个 index 的账号、每条收到的消息、每次回复。

输出用于判断「哪些消息被回复 / 哪些被吞」，并标注账号在 index 间的漂移。

用法: venv\\Scripts\\python.exe scripts\\log_timeline.py 1\\log\\debug.log
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TS = re.compile(r"^\[(?P<src>[^\]]+)\]\s+(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)")

# 例: [#0] [群名(1034395464)] 昵称(1901691195): 内容
RECV = re.compile(r"\[接收<-?\]\s*\[#(?P<idx>\d+)\]\s*(?:\[(?P<gname>[^(]*?)\((?P<gid>\d+)\)\]|\[(?P<gid2>\d+)\])\s*(?P<who>[^:]*):\s*(?P<text>.*)$")
SEND = re.compile(r"\[发送->\]\s*群\s*(?P<gid>\d+):\s*(?P<text>.*)$")
LOGIN = re.compile(r"登录成功\s*\|\s*#(?P<idx>\d+)\s*\|\s*(?P<bid>\d+)\s*\|\s*(?P<nick>.*)$")
ASSEMBLE = re.compile(r"\[LLM\]\s*\[#(?P<bid>\d+)\]\s*\[Agent\]")
SESS = re.compile(r"\[LLM\]\s*\[#(?P<bid>\d+)\]\s*(?P<what>创建会话|会话过期已归档|[^ ]*API 请求[^ ]*|流式 API 请求)[:：]?\s*(?P<rest>.*)$")
DISC = re.compile(r"机器人索引:\s*(?P<idx>\d+)\s*断开连接")
CFG = re.compile(r"机器人索引:\s*(?P<idx>\d+)\s*配置已更新")

# 只关心目标群的消息
FOCUS_GROUPS = {"466052056", "1034395464"}


def main(path: str) -> None:
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    events: list[tuple[str, str, str]] = []
    order = {"login": 0, "assemble": 1, "disc": 2, "cfg": 3, "recv": 4, "send": 5, "llm": 6}
    for raw in lines:
        m = TS.match(raw)
        if not m:
            continue
        ts = m.group("ts")
        body = raw[m.end():].strip()
        if (mm := LOGIN.search(body)):
            events.append((ts, "login", f"index {mm.group('idx')} 登录 {mm.group('bid')} ({mm.group('nick').strip()})"))
        elif (mm := ASSEMBLE.search(body)):
            events.append((ts, "assemble", f"AgentRuntime 装配 #{mm.group('bid')}"))
        elif (mm := DISC.search(body)):
            events.append((ts, "disc", f"index {mm.group('idx')} 断开"))
        elif (mm := CFG.search(body)):
            events.append((ts, "cfg", f"index {mm.group('idx')} 配置已更新（将断开重连）"))
        elif (mm := RECV.search(body)):
            gid = mm.group("gid") or mm.group("gid2") or ""
            if gid in FOCUS_GROUPS:
                who = mm.group("who").strip()
                text = mm.group("text").strip()[:40]
                events.append((ts, "recv", f"#{mm.group('idx')} [{gid}] {who}: {text}"))
        elif (mm := SEND.search(body)):
            events.append((ts, "send", f"群 {mm.group('gid')}: {mm.group('text').strip()[:40]}"))
        elif (mm := SESS.search(body)):
            events.append((ts, "llm", f"#{mm.group('bid')} {mm.group('what')} {mm.group('rest').strip()[:50]}"))
        elif "[LLM]" in body and ("流式 API 请求" in body or "创建会话" in body):
            events.append((ts, "llm", body[:120]))

    events.sort(key=lambda e: (e[0], order.get(e[1], 9)))
    for ts, kind, text in events:
        marker = {"login": "登", "assemble": "装", "disc": "断", "cfg": "改",
                  "recv": "收", "send": "发", "llm": "LLM"}.get(kind, "?")
        print(f"{ts[11:]} [{marker}] {text}")

    print()
    print("统计：")
    print("  登录事件:", sum(1 for e in events if e[1] == "login"))
    print("  运行时装配:", sum(1 for e in events if e[1] == "assemble"))
    print("  收到消息:", sum(1 for e in events if e[1] == "recv"))
    print("  发出消息:", sum(1 for e in events if e[1] == "send"))
    print("  LLM 事件:", sum(1 for e in events if e[1] == "llm"))


if __name__ == "__main__":
    main(sys.argv[1])
