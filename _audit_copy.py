"""扫描所有会出现在界面上的文案，按"口语化 / 过长 / 内部黑话"分类。"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(".")

# 口语化 / 内部黑话 / 不专业标记
INFORMAL = (
    "吧", "呢", "啦", "哦", "呀", "嘛", "啊",
    "别再", "纯粹是坑", "白白", "烧钱", "别让", "免得", "省得", "顺手",
    "黑话", "接管", "槽", "迁移", "模块化", "历史", "此前", "曾经", "以前",
    "一句话", "说白了", "其实", "就是", "咱们", "你自己", "别", "坑",
    "了。", "的。", "。",  # 占位
)
INFORMAL = tuple(x for x in INFORMAL if x != "。")

def chinese_len(s: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", s))


def scan_schema() -> list[tuple[str, str, str, str, int]]:
    """后端 schema 的 label / description（前端直接渲染成标题与说明）。"""
    p = ROOT / "app/agent/config_schema.py"
    tree = ast.parse(p.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                continue
            if not isinstance(value, ast.Dict):
                continue
            fields = {}
            for k, v in zip(value.keys, value.values):
                if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                    fields[k.value] = v.value
            label = str(fields.get("label", "") or "")
            desc = str(fields.get("description", "") or "")
            if desc:
                out.append(("schema", key.value, label, desc, chinese_len(desc)))
    return out


def scan_frontend() -> list[tuple[str, str, str, str, int]]:
    """前端 .vue 里的中文文案（模板文本 + 常见 props）。"""
    out = []
    for p in (ROOT / "app/webui/dashboard/src").rglob("*.vue"):
        text = p.read_text(encoding="utf-8", errors="ignore")
        rel = p.relative_to(ROOT).as_posix()
        # 模板里的中文句子（>=6 个汉字）
        for m in re.finditer(r">([^<>{}\n]{6,})<", text):
            t = m.group(1).strip()
            if chinese_len(t) >= 6:
                out.append(("frontend", rel, "", t, chinese_len(t)))
        # 注释里的设计说明（不渲染，但也要看）
    return out


rows = scan_schema() + scan_frontend()

print("=" * 96)
print(f"共扫描到 {len(rows)} 条界面文案；其中中文 > 30 字的：")
print("=" * 96)
long_rows = sorted([r for r in rows if r[4] > 30], key=lambda r: -r[4])
for kind, key, label, desc, n in long_rows[:22]:
    print(f"[{n:3d}字] {kind}:{key} {('| ' + label) if label else ''}")
    print(f"        {desc}")

print()
print("=" * 96)
print("含口语化/内部黑话的文案（按命中词分组）：")
print("=" * 96)
hits: dict[str, list[tuple]] = {}
for r in rows:
    desc = r[3]
    found = [w for w in INFORMAL if w in desc]
    for w in found:
        hits.setdefault(w, []).append(r)
for word, items in sorted(hits.items(), key=lambda kv: -len(kv[1]))[:14]:
    print(f"\n「{word}」× {len(items)}")
    for kind, key, label, desc, n in items[:3]:
        print(f"   {kind}:{key}  {desc[:70]}")
