#!/usr/bin/env python
"""更新命令行：检查 / 暂存 / 应用 / 回滚（计划 P5-5 的用户入口）。

在 WebUI 做出更新面板之前，这是唯一的安全更新方式；它复用
``app.application.update.UpdateService``，所以备份/回滚语义与程序内一致。

用法::

    python scripts/update.py status                       # 当前版本 / 待应用 / 可回滚
    python scripts/update.py check --release rel.json     # 用本地 release JSON 比对（离线）
    python scripts/update.py stage --kit QQBotNext-2.0.0-kit.zip
    python scripts/update.py apply                        # 备份并切上线（重启后生效）
    python scripts/update.py rollback                     # 退回上一版（含数据库备份）

更新前会自动备份 ``data/app.db`` 与 ``.env``；回滚时一并恢复。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.application.update import UpdateError, UpdateService  # noqa: E402


def _service() -> UpdateService:
    data_root = Path(__file__).resolve().parents[1] / "data"
    return UpdateService(ROOT, data_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QQBot Next 更新工具")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="查看当前版本与更新状态")

    check = sub.add_parser("check", help="用本地 release JSON 比对版本")
    check.add_argument("--release", required=True, help="release JSON 文件（GitHub API 形状）")

    stage = sub.add_parser("stage", help="校验并暂存一个发行包")
    stage.add_argument("--kit", required=True, help="发行包 zip 路径")
    stage.add_argument("--sha256", default="", help="可选：期望的 sha256（不给则按 deploy.json 版本暂存）")

    sub.add_parser("apply", help="备份并切换到暂存版本")
    sub.add_parser("rollback", help="回滚上一版（含 data 备份）")

    args = parser.parse_args(argv)
    service = _service()

    try:
        if args.command == "status":
            print(json.dumps(service.status(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "check":
            release = json.loads(Path(args.release).read_text(encoding="utf-8"))
            result = service.check(release)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("update_available") else 1

        if args.command == "stage":
            kit_path = Path(args.kit)
            data = kit_path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if args.sha256 and args.sha256.lower() != digest:
                print(f"[update] sha256 不匹配：期望 {args.sha256}，实际 {digest}")
                return 2
            result = service.stage({"url": kit_path.as_uri(), "sha256": digest, "size": len(data)})
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if args.command == "apply":
            result = service.apply()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print("[update] 已切换；重启后生效。出问题跑 `python scripts/update.py rollback`。")
            return 0

        if args.command == "rollback":
            result = service.rollback()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print("[update] 已回滚；请重启。")
            return 0
    except UpdateError as e:
        print(f"[update] {e}（{e.code}）" + (f"\n  建议：{e.hint}" if e.hint else ""))
        return 2

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
