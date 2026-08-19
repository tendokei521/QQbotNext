"""脏信息消融：历史内容里“时间/发送者”重复标记，到底去掉哪个更合适。

背景：
  真实会话历史里 user 消息的 content 自带一层内嵌标记：
      (时间：2026-08-19 21:49:23)
      发送者：桉(1901691195)
      发送了：今天晚上十点能提醒我睡觉吗
  而 format_history_for_llm 又在外层包了一层“MM-DD HH:MM 昵称(QQ): 内容”，
  于是同一句话出现两份“时间”和两份“发送者”，属于喂给模型的脏信息。

本脚本对同一条真实消息渲染成 3 个变体，让模型分别回答“什么时候”“谁发的”，
比较谁答得准，从而决策重复的“发送者”部分去掉外层的还是内层的：
  - outer      外层时间 + 外层发送人（去掉内嵌发送者）
  - inner      只有内层“发送者：xxx”（去掉外层时间与发送人）
  - time_inner 外层时间 + 内层“发送者：xxx”

内容从现有 data/llm 历史里真实抽取（含上述内嵌标记的消息）。

Usage:
    python scripts/dirty_info_ablation.py --limit 2 --dry-run
    python scripts/dirty_info_ablation.py --limit 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
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

FORMATS = ["outer", "inner", "time_inner"]
FORMAT_LABEL = {
    "outer": "外层时间+外层发送人（去掉内嵌发送者）",
    "inner": "只有内层「发送者：xxx」",
    "time_inner": "外层时间 + 内层「发送者：xxx」",
}

# 内嵌脏标记：(时间：...)\n发送者：xxx\n发送了：正文
_DIRTY_RE = re.compile(
    r"^\(时间：(?P<t>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\)\n"
    r"发送者：(?P<sender>[^\n]+)\n发送了：(?P<text>.*)$",
    re.S,
)
_SENDER_SPLIT = re.compile(r"^(?P<name>.*?)\((?P<qq>\d+)\)$")


# --------------------------------------------------------------------------- #
#  从现有历史抽取“脏消息”
# --------------------------------------------------------------------------- #
def _split_sender(s: str) -> tuple[str, str]:
    m = _SENDER_SPLIT.match(s.strip())
    if m:
        return m["name"].strip(), m["qq"]
    return s.strip(), ""


def _dirty_field(content: str) -> dict | None:
    m = _DIRTY_RE.match(content.strip())
    if not m:
        return None
    return {
        "inner_time": m["t"],
        "inner_sender": m["sender"].strip(),
        "text": m["text"].strip(),
    }


def load_dirty_messages(base_dir: Path, limit: int) -> list[dict]:
    """从 LLM 数据目录递归抽取含内嵌“时间/发送者”的用户消息。

    按 bot 隔离后的布局为 data/llm/<bot_id>/history/history_*.json，
    兼容旧平铺 data/llm/history/history_*.json，用 `**/history/` 一并覆盖。
    """
    seen: set[str] = set()
    rows: list[dict] = []
    files = sorted(base_dir.glob("**/history/history_*.json"))
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        messages = data.get("messages", []) or []
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = str(msg.get("content") or "")
            if content in seen:
                continue
            field = _dirty_field(content)
            if not field:
                continue
            seen.add(content)

            raw_ts = msg.get("time") or 0
            outer_time = datetime.fromtimestamp(int(raw_ts)).strftime("%m-%d %H:%M") if raw_ts else ""
            nickname = (msg.get("nickname") or "").strip()
            user_id = str(msg.get("user_id") or "")
            if not nickname:
                nickname, _qq = _split_sender(field["inner_sender"])
            if not user_id:
                _, user_id = _split_sender(field["inner_sender"])

            outer_sender = f"{nickname}({user_id})" if user_id else nickname
            rows.append({
                "source": f.as_posix(),
                "inner_time": field["inner_time"],
                "inner_sender": field["inner_sender"],
                "text": field["text"],
                "nickname": nickname,
                "user_id": user_id,
                "outer_time": outer_time,
                "outer_sender": outer_sender,
            })
            if len(rows) >= limit:
                return rows
    return rows


def render_line(fmt: str, r: dict) -> str:
    """把单条消息渲染成给定变体。"""
    body = f"{fmt} {r['user_id']}"  # 占位，避免误用
    if fmt == "outer":
        return f"{r['outer_time']} {r['outer_sender']}: {r['text']}"
    if fmt == "inner":
        return f"发送者：{r['inner_sender']}: {r['text']}"
    if fmt == "time_inner":
        return f"{r['outer_time']} 发送者：{r['inner_sender']}: {r['text']}"
    raise ValueError(fmt)


# --------------------------------------------------------------------------- #
#  真实 provider/agent 配置（与 history_tagging_ablation 一致）
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
def build_questions(r: dict) -> list[dict]:
    """两个问题：时间 / 谁发的，含判分基准。"""
    return [
        {
            "q": "这条消息是在什么时间发送的？请直接回答时间（例如 2026-08-19 21:49）。",
            "expect": {"time": [r["inner_time"][:16], r["inner_time"][11:16]]},
        },
        {
            "q": "这条消息是谁发送的？请直接回答发送者的昵称。",
            "expect": {"who": [r["nickname"], r["user_id"]]},
        },
    ]


def build_messages(system_prompt: str, fmt: str, r: dict, question: str) -> list[dict]:
    line = render_line(fmt, r)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"下面是一条聊天消息：\n{line}\n\n请回答：{question}"},
    ]


def norm(s: str) -> str:
    return (s or "").replace(" ", "").replace("\u3000", "").replace("\n", "")


def score(answer: str, expect: dict) -> dict:
    a = norm(answer)
    out: dict = {}
    for key, kws in expect.items():
        out[key] = any(keyword and norm(keyword) in a for keyword in kws)
    out["cautious"] = any(w in a for w in ["无法确定", "不确定"])
    return out


# --------------------------------------------------------------------------- #
#  Harness
# --------------------------------------------------------------------------- #
async def main(args) -> None:
    history_dir = Path(args.history_dir) if args.history_dir else ROOT / "data" / "llm"
    if not history_dir.is_dir():
        raise SystemExit(f"历史目录不存在: {history_dir}")

    rows = load_dirty_messages(history_dir, args.limit)
    if not rows:
        raise SystemExit(f"在 {history_dir} 没找到含内嵌「(时间：…)/发送者/发送了」标记的用户消息。")
    print(f"抽取 {len(rows)} 条真实脏消息：")
    for i, r in enumerate(rows, 1):
        print(f"  #{i} [{r['inner_time']}] {r['inner_sender']}: {r['text'][:20]}… (source={Path(r['source']).name})")

    if args.dry_run:
        print("\n=== DRY-RUN（不调用 API）===")
        for i, r in enumerate(rows, 1):
            print(f"\n--- 消息 #{i} ---")
            for fmt in args.formats:
                print(f"  [{fmt}] {FORMAT_LABEL[fmt]}:\n      {render_line(fmt, r)}")
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
    for i, r in enumerate(rows, 1):
        questions = build_questions(r)
        for fmt in args.formats:
            for qi, q in enumerate(questions):
                seq += 1
                messages = build_messages(system_prompt, fmt, r, q["q"])
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
                sc = score(answer, q["expect"]) if answer else {}
                row = {
                    "seq": seq,
                    "msg_idx": i,
                    "format": fmt,
                    "question_idx": qi + 1,
                    "question": q["q"],
                    "answer": answer,
                    "latency_s": latency,
                    "prompt_tokens": usage.get("prompt_tokens") if isinstance(usage, dict) else None,
                    "completion_tokens": usage.get("completion_tokens") if isinstance(usage, dict) else None,
                    "score": sc,
                    "error": "" if answer else "empty/failed",
                }
                results.append(row)
                ok = sc and all(v for k, v in sc.items() if k != "cautious")
                print(f"[{seq:>2}] M{i} {fmt:<10} Q{qi+1} {latency:>5}s "
                      f"{'OK' if ok else '--'} | {(answer[:36] or row['error']).replace(chr(10), ' ')}")

    report = {
        "target": {"api_base": target.get("api_base"), "api_key": mask_key(key), "model": target.get("model")},
        "agent_config_used": {"system_prompt": system_prompt, "temperature": temperature, "max_tokens": max_tokens},
        "formats": args.formats,
        "messages": [{"source": r["source"], "inner_time": r["inner_time"], "inner_sender": r["inner_sender"],
                      "text": r["text"]} for r in rows],
        "results": results,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = out_dir / f"dirty_info_{ts}.json"
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
    p = argparse.ArgumentParser(description="重复“时间/发送者”脏信息消融（真实 API）")
    p.add_argument("--history-dir", default=None,
                   help="LLM 数据目录（默认 data/llm，递归扫各 bot 的 history）")
    p.add_argument("--limit", type=int, default=2, help="抽几条真实脏消息（每条 × 3 变体 × 2 问 = 6 次调用）")
    p.add_argument("--formats", default="outer,inner,time_inner", help="渲染变体，逗号分隔")
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
