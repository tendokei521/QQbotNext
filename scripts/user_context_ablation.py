"""用户信息感知注入方案消融：时间 / 发送者 / 提到 / 引用 / 正文 五维。

背景：
  LLM增强模块（module/modules/llm_enhance）会把消息加工成一条带元信息的 user_text：
      (时间：2026-08-19 22:49:03)
      发送者：桉(1901691195)
      提到了(用户名)：小鳥遊ホシノ(3569937952)
      引用了：小鳥遊ホシノ(3569937952)发送的引用消息：“……”
      发送了：他说了什么
  这些元信息（时间/发送者/提到/引用）都以扁平散文混在正文里。

本脚本把这些信息按 3 种“注入方案”重新组织，对每条真实消息分别问
  正文原文 / 时间 / 谁发的 / 提到谁 / 引用了什么，
比较哪种方案能让模型最干净地分离“元信息”与“真实正文”：
  - prose       现状：全字段扁平散文前缀
  - structured  结构化键值（[发送者] [提到] [引用] [用户消息] 分节，正文显式标注）
  - separated   元信息与正文分离（【元信息】……【用户正文】……，正文保持干净）

Usage:
    python scripts/user_context_ablation.py --dry-run
    python scripts/user_context_ablation.py --limit 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.settings import Settings  # noqa: E402
from app.infrastructure.config.config_service import ConfigService  # noqa: E402
from app.infrastructure.persistence.database import Database  # noqa: E402
from app.llm.config import DEFAULT_LLM_CONFIG  # noqa: E402
from app.llm.providers import get_provider  # noqa: E402
from app.llm.providers.runtime_manager import ProviderRuntimeManager  # noqa: E402

AGENT_MODULE = "agent"

SCHEMES = ["prose", "structured", "separated"]
SCHEME_LABEL = {
    "prose": "扁平散文（现状）",
    "structured": "结构化键值分节",
    "separated": "元信息/正文分离",
}

_TIME_RE = re.compile(r"\(时间：(?P<t>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\)")
_SENDER_RE = re.compile(r"发送者：(?P<s>[^\n]+)")
_MEN_RE = re.compile(r"提到了\(用户名\)：(?P<m>[^\n]+)")
_QUOTE_RE = re.compile(r"引用了：(?P<q>[^\n]+)")
_QUOTE_SPLIT = re.compile(r"^(?P<qs>.*?)发送的引用消息：“(?P<qt>.*)”$")
_SENT_RE = re.compile(r"发送了：(?P<st>.*)$")
_SENDER_SPLIT = re.compile(r"^(?P<name>.*?)\((?P<qq>\d+)\)$")


# --------------------------------------------------------------------------- #
#  解析 + 渲染
# --------------------------------------------------------------------------- #
def parse_content(content: str) -> dict:
    p = {
        "time": "",
        "sender": "",
        "mentioned": [],
        "quote": "",
        "quote_sender": "",
        "sent": "",
    }
    m = _TIME_RE.search(content)
    if m:
        p["time"] = m["t"]
    m = _SENDER_RE.search(content)
    if m:
        p["sender"] = m["s"].strip()
    m = _MEN_RE.search(content)
    if m:
        p["mentioned"] = [x.strip() for x in m["m"].split("、") if x.strip()]
    m = _QUOTE_RE.search(content)
    if m:
        q = m["q"].strip()
        qm = _QUOTE_SPLIT.match(q)
        if qm:
            p["quote_sender"] = qm["qs"].strip()
            p["quote"] = qm["qt"].strip()
        else:
            p["quote"] = q
    m = _SENT_RE.search(content)
    if m:
        p["sent"] = m["st"].strip()
    return p


def render(parsed: dict, scheme: str) -> str:
    if scheme == "prose":
        lines: list[str] = []
        if parsed["time"]:
            lines.append(f"(时间：{parsed['time']})")
        if parsed["sender"]:
            lines.append(f"发送者：{parsed['sender']}")
        if parsed["mentioned"]:
            lines.append("提到了(用户名)：" + "、".join(parsed["mentioned"]))
        if parsed["quote_sender"] and parsed["quote"]:
            lines.append(f"引用了：{parsed['quote_sender']}发送的引用消息：“{parsed['quote']}”")
        elif parsed["quote"]:
            lines.append(f"引用了：{parsed['quote']}")
        if parsed["sent"]:
            lines.append(f"发送了：{parsed['sent']}")
        return "\n".join(lines)

    if scheme == "structured":
        lines = []
        if parsed["time"]:
            lines.append(f"[发送时间] {parsed['time']}")
        if parsed["sender"]:
            lines.append(f"[发送者] {parsed['sender']}")
        if parsed["mentioned"]:
            lines.append("[提到] " + "、".join(parsed["mentioned"]))
        if parsed["quote_sender"] and parsed["quote"]:
            lines.append(f"[引用] {parsed['quote_sender']}：“{parsed['quote']}”")
        elif parsed["quote"]:
            lines.append(f"[引用] {parsed['quote']}")
        if parsed["sent"]:
            lines.append(f"[用户消息] {parsed['sent']}")
        return "\n".join(lines)

    if scheme == "separated":
        lines = ["【元信息】"]
        meta = []
        if parsed["time"]:
            meta.append(f"时间：{parsed['time']}")
        if parsed["sender"]:
            meta.append(f"发送者：{parsed['sender']}")
        if parsed["mentioned"]:
            meta.append("@：" + "、".join(parsed["mentioned"]))
        lines.append(" | ".join(meta))
        if parsed["quote_sender"] and parsed["quote"]:
            lines.append(f"引用：{parsed['quote_sender']}：“{parsed['quote']}”")
        elif parsed["quote"]:
            lines.append(f"引用：{parsed['quote']}")
        lines.append("")
        lines.append("【用户正文】")
        lines.append(parsed["sent"])
        return "\n".join(lines)

    raise ValueError(scheme)


def _split_sender(s: str) -> tuple[str, str]:
    m = _SENDER_SPLIT.match(s)
    if m:
        return m["name"].strip(), m["qq"]
    return s, ""


# --------------------------------------------------------------------------- #
#  从现有历史取真实样例（优先带引用的）
# --------------------------------------------------------------------------- #
def load_samples(base_dir: Path, limit: int) -> list[dict]:
    seen: set[str] = set()
    rows: list[dict] = []
    for f in sorted(base_dir.glob("**/history/history_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for msg in (data.get("messages", []) or []):
            if msg.get("role") != "user":
                continue
            content = str(msg.get("content") or "")
            if not (_SENDER_RE.search(content) and _SENT_RE.search(content)):
                continue
            if content in seen:
                continue
            seen.add(content)
            rows.append({
                "source": Path(f).name,
                "raw": content,
                "parsed": parse_content(content),
            })
    # 优先带引用的（维度最全）
    rows.sort(key=lambda r: (not r["parsed"]["quote"], not bool(r["parsed"]["mentioned"])))
    return rows[:limit]


# --------------------------------------------------------------------------- #
#  真实 provider/agent 配置
# --------------------------------------------------------------------------- #
def resolve_agent_config(cfg_service) -> dict:
    stored = cfg_service.get_module_config(AGENT_MODULE, None) or {}
    return {**DEFAULT_LLM_CONFIG, **stored}


async def resolve_target(args, cfg_service, runtime) -> dict:
    presets = cfg_service.list_provider_presets()
    if not presets:
        raise SystemExit("未找到任何 Provider 预设，请先在 WebUI「Provider 预设」配置。")
    preset = None
    if args.preset:
        preset = cfg_service.get_provider_preset(args.preset)
    if preset is None:
        enabled = [p for p in presets if p.get("enabled")]
        preset = (enabled or presets)[0]
    target = None
    if args.model:
        target = runtime.resolve_provider_config(args.model)
    if target is None:
        models = cfg_service.list_provider_models(preset.get("id", ""))
        if models:
            target = runtime.resolve_provider_config(models[0].get("id", ""))
    if target is None:
        target = runtime.resolve_preset_config(preset.get("id", ""))
    if not target:
        raise SystemExit("无法解析可用的 Provider 配置。")
    target.setdefault("model", target.get("model") or "deepseek-chat")
    return target


def mask_key(key: str) -> str:
    if not key:
        return "(未配置)"
    if len(key) <= 8:
        return "****(已打码)"
    return f"{key[:4]}...{key[-4:]}"


# --------------------------------------------------------------------------- #
#  问题与打分
# --------------------------------------------------------------------------- #
def build_questions(p: dict) -> list[dict]:
    qs = [
        {
            "facet": "sent",
            "q": "用户实际发送的消息原文是什么？只输出原文，不要包含时间/发送者等元信息。",
            "expect": {"sent": [p["sent"]]},
        },
    ]
    if p["time"]:
        qs.append({
            "facet": "time",
            "q": "这条消息是在什么时间发送的？请直接回答。",
            "expect": {"time": [p["time"][:16], p["time"][11:16]]},
        })
    if p["sender"]:
        name, qq = _split_sender(p["sender"])
        qs.append({
            "facet": "sender",
            "q": "这条消息是谁发送的？请直接回答昵称。",
            "expect": {"who": [name, qq]},
        })
    if p["mentioned"]:
        qs.append({
            "facet": "mentioned",
            "q": "这条消息 @ 提到了谁？请直接回答。",
            "expect": {"men": p["mentioned"]},
        })
    if p["quote"]:
        qs.append({
            "facet": "quote",
            "q": "这条消息引用了谁、引用内容是什么？请分别回答。",
            "expect": {"qsrc": [p["quote_sender"]], "qtext": [p["quote"]]},
        })
    return qs


def build_messages(system_prompt: str, scheme: str, p: dict, question: str) -> list[dict]:
    body = render(p, scheme)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"下面是一条聊天消息：\n{body}\n\n请回答：{question}"},
    ]


def norm(s: str) -> str:
    return (s or "").replace(" ", "").replace("\u3000", "").replace("\n", "")


def score(answer: str, expect: dict) -> dict:
    a = norm(answer)
    out: dict = {}
    for key, kws in expect.items():
        out[key] = any(kw and norm(str(kw)) in a for kw in kws)
    out["cautious"] = any(w in a for w in ["无法确定", "不确定"])
    return out


# --------------------------------------------------------------------------- #
#  Harness
# --------------------------------------------------------------------------- #
async def main(args) -> None:
    base = Path(args.history_dir) if args.history_dir else ROOT / "data" / "llm"
    if not base.is_dir():
        raise SystemExit(f"目录不存在: {base}")

    samples = load_samples(base, args.limit)
    if not samples:
        raise SystemExit(f"在 {base} 未找到含「发送者…发送了…」增强标记的真实消息。")

    print(f"抽取 {len(samples)} 条真实增强消息：")
    for i, s in enumerate(samples, 1):
        p = s["parsed"]
        print(f"  #{i} 时间={p['time']} 发送者={p['sender']} 提到={p['mentioned']} "
              f"引用={bool(p['quote'])} 正文={p['sent'][:16]}… ({s['source']})")

    if args.dry_run:
        print("\n=== DRY-RUN（不调用 API）===")
        for i, s in enumerate(samples, 1):
            print(f"\n--- 消息 #{i} {s['source']} ---")
            for scheme in args.schemes:
                print(f"  [{scheme}] {SCHEME_LABEL[scheme]}:\n{render(s['parsed'], scheme)}")
            qs = build_questions(s["parsed"])
            print("  问题：" + " | ".join(f"{q['facet']}:{q['q'][:20]}…" for q in qs))
        print("\n(仅构建请求形状，未调用 API)")
        return

    cfg_service, runtime = await _init()
    agent_cfg = resolve_agent_config(cfg_service)
    system_prompt = agent_cfg.get("system_prompt") or "你是一个友好的助手。"
    temperature = float(agent_cfg.get("temperature", 0.7))
    max_tokens = int(agent_cfg.get("max_tokens", 1024))

    target = await resolve_target(args, cfg_service, runtime)
    key = (target.get("api_key") or target.get("key") or "").strip()
    print(f"\nAgent system_prompt: {system_prompt[:40]}… | temperature={temperature} max_tokens={max_tokens}")
    print(f"Provider: base={target.get('api_base')} key={mask_key(key)} model={target.get('model')}")

    provider = get_provider(target)
    results: list[dict] = []
    seq = 0
    for i, s in enumerate(samples, 1):
        qs = build_questions(s["parsed"])
        for scheme in args.schemes:
            for qi, q in enumerate(qs):
                seq += 1
                messages = build_messages(system_prompt, scheme, s["parsed"], q["q"])
                t0 = time.monotonic()
                try:
                    resp = await provider.chat(
                        messages, model=target.get("model"),
                        temperature=temperature, max_tokens=max_tokens, timeout=args.timeout,
                    )
                    answer = resp.text or ""
                    usage = resp.usage or {}
                except Exception as e:  # noqa: BLE001
                    answer = ""
                    usage = {}
                latency = round(time.monotonic() - t0, 2)
                sc = score(answer, q["expect"]) if answer else {}
                row = {
                    "seq": seq, "msg_idx": i, "scheme": scheme, "facet": q["facet"],
                    "question": q["q"], "answer": answer, "latency_s": latency,
                    "prompt_tokens": usage.get("prompt_tokens") if isinstance(usage, dict) else None,
                    "completion_tokens": usage.get("completion_tokens") if isinstance(usage, dict) else None,
                    "score": sc, "error": "" if answer else "empty/failed",
                }
                results.append(row)
                ok = sc and all(v for k, v in sc.items() if k != "cautious")
                print(f"[{seq:>2}] M{i} {scheme:<11} {q['facet']:<9} {latency:>5}s "
                      f"{'OK' if ok else '--'} | {(answer[:34] or row['error']).replace(chr(10), ' ')}")

    report = {
        "target": {"api_base": target.get("api_base"), "api_key": mask_key(key), "model": target.get("model")},
        "schemes": args.schemes,
        "samples": [{"source": s["source"], "raw": s["raw"]} for s in samples],
        "results": results,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = out_dir / f"user_context_{ts}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入: {out}")


async def _init():
    settings = Settings()
    db = Database(settings.db_path)
    await db.connect()
    cfg_service = ConfigService(db, settings.project_root)
    await cfg_service.init()
    runtime = ProviderRuntimeManager(cfg_service)
    return cfg_service, runtime


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="用户信息注入方案消融（真实 API）")
    p.add_argument("--history-dir", default=None, help="LLM 数据目录（默认 data/llm）")
    p.add_argument("--limit", type=int, default=2, help="取几条真实样例")
    p.add_argument("--schemes", default="prose,structured,separated", help="注入方案，逗号分隔")
    p.add_argument("--preset", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--out-dir", default=str(ROOT / "logs" / "prompt_ablation"))
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    ns = parse_args()
    ns.schemes = [x.strip().lower() for x in ns.schemes.split(",") if x.strip()]
    asyncio.run(main(ns))
