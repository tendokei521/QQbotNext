"""History tagging ablation: which per-line tagging lets a model tell WHO and WHEN.

Uses the project's REAL agent LLM configuration (system_prompt / temperature /
max_tokens from the stored "agent" module config) and the REAL provider chain
(provider preset + model from data/app.db) from the existing agent pipeline.

Test content is taken from REAL local chat history (module/data/*/message_db_*.json)
so it reflects actual usage. The only thing varied is how each message line is
tagged (time / speaker / content), which lets us compare tagging styles.

Call budget: 5 tagging formats x 2 questions = 10 real API calls.

Usage:
    python scripts/history_tagging_ablation.py --history module/data/notice_recall_back/message_db_3569937952.json
    python scripts/history_tagging_ablation.py --history ... --window 48 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
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

# Per-line tagging formats. Each maps to a renderer returning one text line.
FORMATS = ["plain", "time", "full", "json", "table"]

FORMAT_LABEL = {
    "plain": "纯文本（昵称: 内容，无时间）",
    "time": "仅时刻（HH:MM 昵称: 内容）",
    "full": "日期+时刻（MM-DD HH:MM 昵称: 内容）",
    "json": "JSON 数组（含 time/speaker/text）",
    "table": "Markdown 表格（时间|昵称|内容）",
}


# --------------------------------------------------------------------------- #
#  History loading
# --------------------------------------------------------------------------- #
def load_history(path: Path) -> list[dict]:
    """Load a message_db_*.json file into time-ordered transcript rows.

    Returns rows of {ts, text, speaker, is_self}; text is reconstructed from the
    CQ message segments (text joined, at/reply/image collapsed to placeholders).
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for key, v in data.items():
        if not isinstance(v, dict) or "message_id" not in v:
            continue
        text = extract_text(v.get("message") or [])
        ts = float(v.get("time") or 0)
        speaker = (v.get("user_card") or "").strip() or (v.get("user_nickname") or "").strip() or str(v.get("user_id", ""))
        rows.append({
            "ts": ts,
            "text": text,
            "speaker": speaker,
            "is_self": int(v.get("user_id") or 0) == int(v.get("self_id") or 0),
        })
    rows.sort(key=lambda r: r["ts"])
    return rows


def extract_text(segments) -> str:
    """Reconstruct readable text from a CQ message segment array."""
    if isinstance(segments, str):
        return segments
    parts: list[str] = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        kind = seg.get("type", "")
        d = seg.get("data", {}) or {}
        if kind == "text":
            parts.append(str(d.get("text", "")))
        elif kind == "at":
            parts.append(f"[at:{d.get('qq', '')}]")
        elif kind == "reply":
            parts.append("[回复]")
        elif kind in ("image", "face", "record", "video", "file"):
            parts.append(f"[{kind}]")
        else:
            parts.append(f"[{kind}]")
    return "".join(parts).strip()


def render_row(fmt: str, r: dict) -> str:
    """Render one transcript row into a tagged line (or dict for json)."""
    dt = datetime.fromtimestamp(r["ts"]) if r["ts"] else None
    tag = "自己" if r["is_self"] else r["speaker"]
    if fmt == "plain":
        return f"{tag}: {r['text']}"
    if fmt == "time":
        t = dt.strftime("%H:%M") if dt else "??:??"
        return f"{t} {tag}: {r['text']}"
    if fmt == "full":
        t = dt.strftime("%m-%d %H:%M") if dt else "??-?? ??:??"
        return f"{t} {tag}: {r['text']}"
    if fmt == "json":
        return json.dumps({
            "time": dt.strftime("%m-%d %H:%M") if dt else "",
            "speaker": tag,
            "text": r["text"],
        }, ensure_ascii=False)
    if fmt == "table":
        t = dt.strftime("%m-%d %H:%M") if dt else ""
        return f"| {t} | {tag} | {r['text']} |"
    raise ValueError(fmt)


def render_transcript(fmt: str, rows: list[dict]) -> str:
    """Render a whole transcript window in the given tagging format."""
    if fmt == "json":
        return "[\n" + ",\n".join("  " + render_row(fmt, r) for r in rows) + "\n]"
    if fmt == "table":
        head = "| 时间 | 昵称 | 内容 |\n|---|---|---|\n"
        return head + "\n".join(render_row(fmt, r) for r in rows)
    return "\n".join(render_row(fmt, r) for r in rows)


def pick_window(rows: list[dict], size: int) -> list[dict]:
    """Pick a contiguous window with >=3 senders and spanning >=10 minutes."""
    for start in range(len(rows) - size + 1):
        win = rows[start : start + size]
        senders = {r["speaker"] for r in win if not r["is_self"]}
        span = max(r["ts"] for r in win) - min(r["ts"] for r in win)
        if len(senders) >= 3 and span >= 600 and len(self_msgs := [r for r in win if r["is_self"]]) < size // 4:
            return win
    return rows[:size]


# --------------------------------------------------------------------------- #
#  Project real config (existing prompts / LLM settings)
# --------------------------------------------------------------------------- #
def resolve_agent_config(cfg_service) -> dict:
    """Merge stored 'agent' config on top of defaults, like the real AgentConfig."""
    stored = cfg_service.get_module_config(AGENT_MODULE, None) or {}
    merged = {**DEFAULT_LLM_CONFIG, **stored}
    return merged


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
#  Harness
# --------------------------------------------------------------------------- #
def strip_placeholders(text: str) -> str:
    """Remove CQ placeholder tokens ([image]/[at:..]/[回复]...) to judge real content."""
    import re
    return re.sub(r"\[[^\[\]]*\]", "", text or "").strip()


def build_questions(win: list[dict]) -> list[dict]:
    """Two natural questions about the window, with ground truth for scoring."""
    humans = [r for r in win if not r["is_self"]]
    # 提问目标：选一条有实际文本的句子，避免选中纯图片/at 类占位
    meaningful = [r for r in humans if len(strip_placeholders(r["text"])) >= 4]
    target = (meaningful[len(meaningful) // 2] if meaningful else humans[len(humans) // 2])
    quote = strip_placeholders(target["text"])[:24] or (target["text"] or "……")[:24]
    earliest = humans[0]
    latest = humans[-1]
    return [
        {
            "q": f"上面这段群聊记录里，「{quote}」这句话是谁说的？大概是什么时候说的？请直接说昵称和时间。",
            "expect": {
                "speaker": [target["speaker"]],
                "time": [datetime.fromtimestamp(target["ts"]).strftime("%H:%M")],
            },
        },
        {
            "q": "上面这段记录里，发言最早的人和发言最晚的人分别是谁？请分别说出昵称。",
            "expect": {"earliest": [earliest["speaker"]], "latest": [latest["speaker"]]},
        },
    ]


def build_messages(system_prompt: str, fmt: str, win: list[dict], question: str) -> list[dict]:
    body = render_transcript(fmt, win)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"以下是一段群聊记录：\n{body}\n\n按上面的记录回答：\n{question}"},
    ]


def norm(s: str) -> str:
    return (s or "").replace(" ", "").replace("\u3000", "").replace("\n", "")


def score(answer: str, expect: dict) -> dict:
    a = norm(answer)
    out: dict = {}
    for key, kws in expect.items():
        out[key] = any(norm(k) in a for k in kws)
    out["cautious"] = any(w in a for w in ["无法确定", "无法依据记录", "不确定"])
    return out


async def main(args) -> None:
    history = load_history(Path(args.history))
    if len(history) < args.window:
        raise SystemExit(f"历史记录只有 {len(history)} 条，不足以取 {args.window} 条窗口。")
    win = pick_window(history, args.window)
    questions = build_questions(win)

    if args.dry_run:
        print("=== DRY-RUN（不调用 API）===")
        print(f"窗口: {len(win)} 条 | 起 {datetime.fromtimestamp(win[0]['ts']):%m-%d %H:%M} "
              f"止 {datetime.fromtimestamp(win[-1]['ts']):%m-%d %H:%M} | 说话人 "
              f"{sorted({r['speaker'] for r in win if not r['is_self']})}")
        for fmt in args.formats:
            print(f"\n--- 打标[{fmt}] {FORMAT_LABEL[fmt]} ---")
            print(render_transcript(fmt, win)[:400].replace("\n", " | ") + " …")
            print(f"  问题1: {questions[0]['q'][:50]}…\n  问题2: {questions[1]['q'][:50]}…")
        print("\n(以上仅构建请求形状，未调用 API)")
        return

    cfg_service, runtime = await _init()
    agent_cfg = resolve_agent_config(cfg_service)
    system_prompt = agent_cfg.get("system_prompt") or "你是一个友好的助手。"
    temperature = float(agent_cfg.get("temperature", 0.7))
    max_tokens = int(agent_cfg.get("max_tokens", 1024))

    target = await resolve_target(args, cfg_service, runtime)
    key = (target.get("api_key") or target.get("key") or "").strip()
    print(f"Agent system_prompt: {system_prompt[:40]}… | temperature={temperature} max_tokens={max_tokens}")
    print(f"Provider: preset={target.get('provider_preset_id','?')} base={target.get('api_base')} "
          f"key={mask_key(key)} model={target.get('model')}")

    provider = get_provider(target)
    results: list[dict] = []
    seq = 0
    for fmt in args.formats:
        for qi, q in enumerate(questions):
            seq += 1
            messages = build_messages(system_prompt, fmt, win, q["q"])
            t0 = time.monotonic()
            try:
                resp = await provider.chat(
                    messages,
                    model=target.get("model"),
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=args.timeout,
                )
                answer = resp.text or ""
                usage = resp.usage or {}
            except Exception as e:  # noqa: BLE001
                answer = ""
                usage = {}
            latency = round(time.monotonic() - t0, 2)
            row = {
                "seq": seq,
                "format": fmt,
                "question_idx": qi + 1,
                "question": q["q"],
                "answer": answer,
                "latency_s": latency,
                "prompt_tokens": usage.get("prompt_tokens") if isinstance(usage, dict) else None,
                "completion_tokens": usage.get("completion_tokens") if isinstance(usage, dict) else None,
                "score": score(answer, q["expect"]) if answer else {},
                "error": "" if answer else "empty/failed",
            }
            results.append(row)
            print(f"[{seq:>2}] {fmt:<5} Q{qi+1} {latency:>5}s in={row['prompt_tokens']} out={row['completion_tokens']} "
                  f"{'OK' if row['score'] and all(v for k,v in row['score'].items() if k!='cautious') else '--'} "
                  f"| {(answer[:40] or row['error']).replace(chr(10),' ')}")

    report = {
        "target": {"provider": target.get("provider"), "api_base": target.get("api_base"),
                   "api_key": mask_key(key), "model": target.get("model")},
        "agent_config_used": {"system_prompt": system_prompt, "temperature": temperature, "max_tokens": max_tokens},
        "window": {"n": len(win),
                   "start": datetime.fromtimestamp(win[0]["ts"]).isoformat(),
                   "end": datetime.fromtimestamp(win[-1]["ts"]).isoformat(),
                   "senders": sorted({r["speaker"] for r in win if not r["is_self"]})},
        "formats": args.formats,
        "questions": [q["q"] for q in questions],
        "results": results,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = out_dir / f"tagging_{ts}.json"
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
    p = argparse.ArgumentParser(description="历史聊天记录打标方式消融测试（10 次真实 API）")
    p.add_argument("--history", required=True, help="message_db_*.json 历史文件")
    p.add_argument("--window", type=int, default=24, help="每次使用的连续消息条数")
    p.add_argument("--formats", default="plain,time,full,json,table", help="打标方式，逗号分隔")
    p.add_argument("--preset", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--out-dir", default=str(ROOT / "logs" / "prompt_ablation"))
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    ns = parse_args()
    ns.formats = [f.strip().lower() for f in ns.formats.split(",") if f.strip()]
    asyncio.run(main(ns))
