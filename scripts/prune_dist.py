"""【已废弃】清理 `dist/assets` 里的过期 chunk。

## 为什么废弃

它存在的唯一理由是 `vite.config.ts` 曾关掉 `emptyOutDir`（保留旧 chunk，避免
正在使用的页面 `import()` 404）。该策略的代价是 `dist/assets` 无限累积（实测
1077 个文件 / 15 MB），而本脚本按"index.html 引用 + 最近 N 天"删除，**会误删
Vue 懒加载 chunk**——它们不在 index.html 里，超过保留天数就被当成垃圾。

现在改为**干净构建**（`vite.config.ts` 的 `emptyOutDir: true`）：仓库里的 `dist`
就是一次完整构建的结果，可复现、可审查，不需要事后清理。

```bash
cd app/webui/dashboard && pnpm build   # 干净重建（推荐）
python scripts/prune_dist.py           # 仅在你仍有历史累积目录时兜底，先 dry-run
```

保留脚本只为兼容"手上还有旧 dist"的用户；新流程不再需要它。
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "app" / "webui" / "dashboard" / "dist"
ASSETS = DIST / "assets"

#: index.html 里引用的文件名（`/assets/xxx-hash.js`）
_ASSET_REF = re.compile(r"assets/([A-Za-z0-9_.\-]+\.(?:js|css))")


def referenced_assets() -> set[str]:
    """index.html 里显式引用的资源（入口 chunk + 它的直接依赖）。"""
    index = DIST / "index.html"
    if not index.is_file():
        return set()
    return set(_ASSET_REF.findall(index.read_text(encoding="utf-8")))


def plan(keep_days: float) -> tuple[list[Path], list[Path]]:
    """返回 (要删, 要留)。规则：被 index 引用 → 留；最近 keep_days 天内改过 → 留。"""
    refs = referenced_assets()
    cutoff = time.time() - keep_days * 86400
    delete: list[Path] = []
    keep: list[Path] = []
    for f in sorted(ASSETS.glob("*")):
        if not f.is_file():
            continue
        if f.name in refs or f.stat().st_mtime >= cutoff:
            keep.append(f)
        else:
            delete.append(f)
    return delete, keep


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理 dist 过期 chunk")
    parser.add_argument("--apply", action="store_true", help="真删（默认只预览）")
    parser.add_argument("--keep-days", type=float, default=7.0, help="保留最近几天的文件")
    args = parser.parse_args(argv)

    if not ASSETS.is_dir():
        print(f"[skip] 没有 {ASSETS}（先 pnpm build）")
        return 0

    delete, keep = plan(args.keep_days)
    total = sum(f.stat().st_size for f in delete)
    print(f"保留 {len(keep)} 个，待删 {len(delete)} 个（{total / 1024:.0f} KB）")
    for f in delete[:10]:
        print(f"  - {f.name}")
    if len(delete) > 10:
        print(f"  … 还有 {len(delete) - 10} 个")

    if not args.apply:
        print("\n[dry-run] 加 --apply 才会真的删除")
        return 0
    for f in delete:
        try:
            f.unlink()
        except OSError as e:
            print(f"[warn] 删除失败 {f.name}: {e}")
    print(f"[done] 已删除 {len(delete)} 个过期 chunk")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
