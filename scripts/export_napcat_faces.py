"""从本机 NapCat 包导出 QQ 系统表情表 → ``app/llm/qq_faces.py``。

## 为什么要"导出"而不是手抄

QQ 客户端下发的系统表情表（``face_config.sysface``）是表情 id 的唯一权威来源：

- OneBot ``face`` 段的 ``id`` 就是表里的 ``QSid``（NapCat 解析 face 段时按 ``QSid`` 反查）；
- ``set_msg_emoji_like`` 的 ``emoji_id`` 也被 NapCat 原样透传给 QQ，
  因此同一张表也是表情回应的 id 空间。

网上流传的"QQ 表情代码"列表编号互相矛盾（最常见的一种把 微笑 记成 1，
而 QQ 实际是 撇嘴=1、微笑=14），照抄会贴错且**不可撤回**。所以这里只做一件事：
把客户端自己的表导成仓库内的数据模块，并在客户端升级后可重新生成、比对。

## 用法

```bash
python scripts/export_napcat_faces.py            # 自动探测本机 NapCat，打印核对摘要
python scripts/export_napcat_faces.py --write    # 重新生成 app/llm/qq_faces.py
python scripts/export_napcat_faces.py --check    # 与仓库内的表比对（CI/升级后核对）
python scripts/export_napcat_faces.py --mjs <napcat.mjs 路径>
```
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

#: 自动探测用的候选路径（按修改时间取最新的一个）
_CANDIDATE_GLOBS = (
    r"C:\Users\*\Desktop\NapCatQQ\*\versions\*\resources\app\napcat\napcat.mjs",
    r"*\NapCat*\*\versions\*\resources\app\napcat\napcat.mjs",
    r"C:\Users\*\Documents\GitHub\NapCatQQ\packages\*\dist\napcat.mjs",
    r"/opt/NapCat*/**/napcat.mjs",
)

#: 表情表在 bundle 里的锚点
_ANCHOR = "const sysface"

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET = REPO_ROOT / "app" / "llm" / "qq_faces.py"


def find_mjs(explicit: str | None = None) -> Path:
    """定位 napcat.mjs：显式路径 > ``NAPCAT_MJS`` 环境变量 > 候选路径里最新的一个。"""
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f"找不到 napcat.mjs: {path}")
        return path
    from_env = os.environ.get("NAPCAT_MJS", "").strip()
    if from_env and Path(from_env).is_file():
        return Path(from_env)
    found: list[Path] = []
    for pattern in _CANDIDATE_GLOBS:
        found += [Path(p) for p in glob.glob(pattern)]
    if not found:
        raise SystemExit(
            "没找到 napcat.mjs，请用 --mjs <路径> 指定（或设置 NAPCAT_MJS）"
        )
    return max(found, key=lambda p: p.stat().st_mtime)


def _read_js_string_literal(text: str, start: int) -> str:
    """从 ``start``（指向开引号）起，按 JS 转义规则读出字符串字面量内容。"""
    if text[start] != '"':
        raise SystemExit("表情表锚点后不是字符串字面量，bundle 结构可能变了")
    buf: list[str] = []
    i = start + 1
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            buf.append(text[i:i + 2])
            i += 2
            continue
        if ch == '"':
            break
        buf.append(ch)
        i += 1
    return "".join(buf)


def extract_sysface(mjs: Path) -> list[dict]:
    """读出 bundle 内嵌的 ``face_config.sysface`` 数组。"""
    raw = mjs.read_text(encoding="utf-8", errors="replace")
    idx = raw.find(_ANCHOR)
    if idx < 0:
        raise SystemExit(f"{mjs} 里找不到 {_ANCHOR}，bundle 结构可能变了")
    literal_at = raw.find("JSON.parse(", idx)
    if literal_at < 0:
        raise SystemExit("表情表锚点后没有 JSON.parse(...)，bundle 结构可能变了")
    literal = _read_js_string_literal(raw, literal_at + len("JSON.parse("))
    # 先按 JS 字符串反转义，再解析 JSON
    data = json.loads(json.loads('"' + literal + '"'))
    if not isinstance(data, list) or not data:
        raise SystemExit("表情表解析结果不是非空数组")
    return data


def build_tables(rows: list[dict]) -> tuple[dict[str, str], frozenset[str], list[str]]:
    """``rows`` → ``(名字→QSid, 大表情集合, 重名清单)``。

    名字取 ``QDes`` 去掉前导 ``/``；同一名字出现多次时保留第一个（QQ 表里
    同名不同 id 的情况要人工确认，所以这里把重名报出来）。
    """
    ids: dict[str, str] = {}
    large: set[str] = set()
    dupes: list[str] = []
    for row in rows:
        name = str(row.get("QDes", "") or "").lstrip("/").strip()
        qsid = str(row.get("QSid", "") or "").strip()
        if not name or not qsid.isdigit():
            continue
        if name in ids:
            dupes.append(f"{name}(QSid {ids[name]} → {qsid})")
            continue
        ids[name] = qsid
        if int(qsid) >= 222 or str(row.get("AniStickerType", "") or "").strip():
            large.add(name)
    return ids, frozenset(large), dupes


def _render_dict(ids: dict[str, str]) -> str:
    items = sorted(ids.items(), key=lambda kv: int(kv[1]))
    lines: list[str] = []
    chunk: list[str] = []
    for name, value in items:
        chunk.append(f'"{name}": "{value}"')
        if len(chunk) == 4:
            lines.append("    " + ", ".join(chunk) + ",")
            chunk = []
    if chunk:
        lines.append("    " + ", ".join(chunk) + ",")
    return "\n".join(lines)


def render_module(ids: dict[str, str], large: frozenset[str], source: Path) -> str:
    large_items = sorted(large, key=lambda n: int(ids.get(n, "0")))
    large_lines: list[str] = []
    chunk: list[str] = []
    for name in large_items:
        chunk.append(f'"{name}"')
        if len(chunk) == 6:
            large_lines.append("    " + ", ".join(chunk) + ",")
            chunk = []
    if chunk:
        large_lines.append("    " + ", ".join(chunk) + ",")
    stamp = source.name
    return f'''"""QQ 系统表情表（生成物，勿手改）。

来源：QQ 客户端下发的 ``face_config.sysface``，经 ``scripts/export_napcat_faces.py``
从 NapCat 包（{stamp}）导出。名字是表情名（去掉 ``QDes`` 的前导 ``/``），
值是 QQ 的 ``QSid`` —— OneBot ``face`` 段的 ``id`` 和 ``set_msg_emoji_like``
的 ``emoji_id`` 都用这个数字（NapCat 解析 face 段时按 ``QSid`` 反查，
表情回应则把 ``emoji_id`` 原样透传给 QQ）。

``LARGE_FACES``：``QSid >= 222`` 或带 ``AniStickerType`` 的名字，也就是客户端里
走 faceType 2/3 的"大表情 / 动态表情"。这些 id 是否同样适用于**表情回应**
需要真机校准（见 ``emoji_lexicon`` 模块说明）。

重新生成 / 核对：``python scripts/export_napcat_faces.py --write|--check``
"""

from __future__ import annotations

#: 表情名 → QSid（按 QSid 升序，共 {len(ids)} 条）
SYSFACE_IDS: dict[str, str] = {{
{_render_dict(ids)}
}}

#: 大表情 / 动态表情（{len(large)} 条）：表情回应用的 id 形态待真机校准
LARGE_FACES: frozenset[str] = frozenset({{
{chr(10).join(large_lines)}
}})
'''


def _load_repo_module() -> tuple[dict[str, str], frozenset[str]]:
    namespace: dict = {}
    exec(TARGET.read_text(encoding="utf-8"), namespace)  # noqa: S102 - 本地生成物
    return namespace["SYSFACE_IDS"], namespace["LARGE_FACES"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出 QQ 系统表情表")
    parser.add_argument("--mjs", default=None, help="napcat.mjs 路径（缺省自动探测）")
    parser.add_argument("--write", action="store_true", help="写入 app/llm/qq_faces.py")
    parser.add_argument("--check", action="store_true", help="与仓库内的表比对")
    args = parser.parse_args(argv)

    mjs = find_mjs(args.mjs)
    rows = extract_sysface(mjs)
    ids, large, dupes = build_tables(rows)
    print(f"napcat.mjs: {mjs}")
    print(f"表情 {len(ids)} 条（大表情/动态 {len(large)} 条），原始记录 {len(rows)} 条")
    if dupes:
        print("重名（保留第一个）：" + "；".join(dupes))

    if args.check:
        if not TARGET.is_file():
            print(f"FAIL: 仓库里没有 {TARGET}")
            return 1
        old_ids, old_large = _load_repo_module()
        missing = {k: v for k, v in ids.items() if old_ids.get(k) != v}
        extra = {k: v for k, v in old_ids.items() if ids.get(k) != v}
        if missing or extra:
            print(f"FAIL: 与仓库不一致（新增/变更 {len(missing)} 条，消失/变更 {len(extra)} 条）")
            for name, value in list(missing.items())[:20]:
                print(f"  + {name}={value}（仓库 {old_ids.get(name, '无')}）")
            for name, value in list(extra.items())[:20]:
                print(f"  - {name}={value}（客户端 {ids.get(name, '无')}）")
            return 1
        print(f"OK: 仓库内的表与客户端一致（大表情差异 {len(large ^ old_large)} 条）")
        return 0

    if args.write:
        TARGET.write_text(render_module(ids, large, mjs), encoding="utf-8")
        print(f"已写入 {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
