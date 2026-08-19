"""Verify the NEW LLM history tagging end-to-end against the real API.

Builds prompts using the ACTUAL app formatters (group_context.format_online_history
/ format_history_for_llm) with real local history content, then fires real chat
requests to confirm the model can naturally tell WHO said WHAT and WHEN:

- group chat:  ``MM-DD HH:MM 昵称(QQ): 内容``  (+ bot self = "我")
- private chat: ``MM-DD HH:MM 我/对方: 内容``   (no nickname)

Run: python scripts/verify_new_tagging.py --history module/data/notice_recall_back/message_db_3569937952.json
Total real API calls: 10.
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
from app.llm.group_context import (  # noqa: E402
    SELF_TAG,
    PRIVATE_OTHER_TAG,
    format_history_for_llm,
    format_online_history,
)
from app.llm.providers import get_provider  # noqa: E402
from app.llm.providers.runtime_manager import ProviderRuntimeManager  # noqa: E402


# --------------------------------------------------------------------------- #
#  Real history -> OneBot-like messages
# --------------------------------------------------------------------------- #
def load_messages(path: Path) -> list[dict]:
    """Load message_db_*.json into OneBot-style dicts (sender/time/message)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    msgs: list[dict] = []
    for _k, v in data.items():
        if not isinstance(v, dict) or "message_id" not in v:
            continue
        msgs.append({
            "message_id": v.get("message_id"),
            "time": v.get("time"),
            "sender": {
                "user_id": v.get("user_id"),
                "card": v.get("user_card") or "",
                "nickname": v.get("user_nickname") or "",
            },
            "message": v.get("message") or [],
        })
    msgs.sort(key=lambda m: float(m.get("time") or 0))
    return msgs


def pick_window(msgs: list[dict], size: int) -> list[dict]:
    """Pick a contiguous window with >=3 distinct senders spanning >=10 minutes."""
    for start in range(len(msgs) - size + 1):
        win = msgs[start : start + size]
        senders = {m["sender"]["user_id"] for m in win}
        span = float(win[-1]["time"]) - float(win[0]["time"])
        if len(senders) >= 3 and span >= 600:
            return win
    return msgs[:size]


def human_text(msg: dict) -> str:
    from app.llm.group_context import extract_msg_text

    return extract_msg_text(msg.get("message") or [])


def label_of(msg: dict, self_ids: set[str], is_private: bool) -> str:
    uid = msg["sender"].get("user_id")
    if is_private:
        return SELF_TAG if str(uid) in self_ids else PRIVATE_OTHER_TAG
    if str(uid) in self_ids:
        return SELF_TAG
    nickname = msg["sender"].get("card") or msg["sender"].get("nickname") or str(uid) or "未知"
    parts = [nickname]
    if uid not in (None, ""):
        parts.append(f"({uid})")
    return "".join(parts)


def meaningful_quote(win, self_ids: set[str], is_private: bool) -> tuple[dict, str]:
    for m in win:
        text = human_text(m)
        if len(text) >= 4 and not text.startswith("["):
            return m, label_of(m, self_ids, is_private)
    return win[len(win) // 2], label_of(win[len(win) // 2], self_ids, is_private)


def fmt_ts(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%m-%d %H:%M")
    except Exception:
        return "??"


def group_variants(msg: dict, self_ids: set[str]) -> list[str]:
    """群聊发送者可接受的答案：昵称本体 + 昵称(QQ) 两种都算对。"""
    uid = msg["sender"].get("user_id")
    if str(uid) in self_ids:
        return [SELF_TAG]
    nickname = msg["sender"].get("card") or msg["sender"].get("nickname") or str(uid) or "未知"
    full = "".join([nickname] + ([f"({uid})"] if uid not in (None, "") else []))
    return [nickname, full]


# --------------------------------------------------------------------------- #
#  Config plumbing (same as the running app)
# --------------------------------------------------------------------------- #
async def _init():
    settings = Settings()
    db = Database(settings.db_path)
    await db.connect()
    cfg_service = ConfigService(db, settings.project_root)
    await cfg_service.init()
    runtime = ProviderRuntimeManager(cfg_service)
    return cfg_service, runtime


async def resolve_target(args, cfg_service, runtime) -> dict:
    presets = cfg_service.list_provider_presets()
    if not presets:
        raise SystemExit("未找到 Provider 预设，请先配置。")
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
        raise SystemExit("无法解析可用 Provider 配置")
    target.setdefault("model", target.get("model") or "deepseek-chat")
    return target


def mask_key(key: str) -> str:
    if not key:
        return "(未配置)"
    if len(key) <= 8:
        return "****(已打码)"
    return f"{key[:4]}...{key[-4:]}"


def norm(s: str) -> str:
    return (s or "").replace(" ", "").replace("\u3000", "").replace("\n", "")


def score(answer: str, expect: dict) -> dict:
    a = norm(answer)
    return {k: any(norm(x) in a for x in kws) for k, kws in expect.items()}


# --------------------------------------------------------------------------- #
#  Build the 10 cases
# --------------------------------------------------------------------------- #
def build_cases(win, bot_self_id) -> list[dict]:
    self_ids = {str(bot_self_id)}
    # 私聊：把窗口里最活跃的真人当作“我”，其余是“对方”（仅用真实内容验证私聊打标）
    uid_count: dict[str, int] = {}
    for m in win:
        uid = str(m["sender"].get("user_id"))
        uid_count[uid] = uid_count.get(uid, 0) + 1
    private_self = max(uid_count, key=lambda k: uid_count[k])
    pself_ids = {private_self}

    cases: list[dict] = []

    # ---- 群聊 ----
    g_online = format_online_history(win, self_ids=self_ids, is_private=False)
    q, qlabel = meaningful_quote(win, self_ids, is_private=False)
    q_ts = fmt_ts(q["time"])
    cases.append({
        "name": "G-online·谁+何时", "system": "",
        "user": f"以下是一段群聊记录：\n{g_online}\n\n请回答：{human_text(q)} 这句是谁说的、大概什么时间说的？请给出昵称和时间。",
        "expect": {"who": group_variants(q, self_ids), "when": [q_ts]},
    })
    first, last = win[0], win[-1]
    cases.append({
        "name": "G-online·最早/最晚", "system": "",
        "user": f"以下是一段群聊记录：\n{g_online}\n\n请回答：这条记录里发言最早和最晚的分别是谁？请给出昵称。",
        "expect": {"earliest": group_variants(first, self_ids), "latest": group_variants(last, self_ids)},
    })
    span = max(0, int(float(win[-1]["time"]) - float(win[0]["time"])) // 60)
    cases.append({
        "name": "G-online·时间跨度", "system": "",
        "user": f"以下是一段群聊记录：\n{g_online}\n\n请回答：这条记录从最早到最后大约跨越了多少分钟？",
        "expect": {"span": [str(span), f"{span} 分钟", f"{span}分钟"]},
    })

    g_session = format_history_for_llm(
        [{"role": "user" if str(m["sender"].get("user_id")) != bot_self_id else "assistant",
          "content": human_text(m) or "[非文本消息]", "user_id": m["sender"].get("user_id"),
          "nickname": m["sender"].get("card") or m["sender"].get("nickname"), "time": m["time"]} for m in win],
        is_private=False,
    )
    g_session_text = "\n".join(x["content"] for x in g_session)
    cases.append({
        "name": "G-session·谁+何时", "system": "",
        "user": f"以下是一段群聊历史：\n{g_session_text}\n\n请回答：{human_text(q)} 这句是谁说的、大概什么时间说的？请给出昵称和时间。",
        "expect": {"who": group_variants(q, self_ids), "when": [q_ts]},
    })
    cases.append({
        "name": "G-session·最早/最晚", "system": "",
        "user": f"以下是一段群聊历史：\n{g_session_text}\n\n请回答：发言最早和最晚的分别是谁？请给出昵称。",
        "expect": {"earliest": group_variants(first, self_ids), "latest": group_variants(last, self_ids)},
    })

    # ---- 私聊（我/对方，无昵称） ----
    p_online = format_online_history(win, self_ids=pself_ids, is_private=True)
    pq, pqlabel = meaningful_quote(win, pself_ids, is_private=True)
    pq_ts = fmt_ts(pq["time"])
    cases.append({
        "name": "P-online·谁(我/对方)+何时", "system": "",
        "user": f"以下是一段私聊记录：\n{p_online}\n\n请回答：{human_text(pq)} 这句是我说的还是对方说的？大概什么时间说的？请用「我」或「对方」回答。",
        "expect": {"who": [SELF_TAG if pqlabel == SELF_TAG else PRIVATE_OTHER_TAG], "when": [pq_ts]},
    })
    n_self = sum(1 for m in win if str(m["sender"].get("user_id")) in pself_ids)
    n_other = len(win) - n_self
    cases.append({
        "name": "P-online·各说几条", "system": "",
        "user": f"以下是一段私聊记录：\n{p_online}\n\n请回答：这段里「我」一共说了几条、「对方」一共说了几条？",
        "expect": {"my": [str(n_self)], "other": [str(n_other)]},
    })
    cases.append({
        "name": "P-online·最后一条", "system": "",
        "user": f"以下是一段私聊记录：\n{p_online}\n\n请回答：最后一条是我还是对方说的？什么时间？",
        "expect": {"who": [SELF_TAG if str(last["sender"].get("user_id")) in pself_ids else PRIVATE_OTHER_TAG], "when": [fmt_ts(last["time"])]},
    })

    p_session = format_history_for_llm(
        [{"role": "user" if str(m["sender"].get("user_id")) not in pself_ids else "assistant",
          "content": human_text(m) or "[非文本消息]", "user_id": m["sender"].get("user_id"),
          "nickname": m["sender"].get("card") or m["sender"].get("nickname"), "time": m["time"]} for m in win],
        is_private=True,
    )
    p_session_text = "\n".join(x["content"] for x in p_session)
    cases.append({
        "name": "P-session·谁(我/对方)+何时", "system": "",
        "user": f"以下是一段私聊历史：\n{p_session_text}\n\n请回答：{human_text(pq)} 这句是我说的还是对方说的？大概什么时间？请用「我」或「对方」回答。",
        "expect": {"who": [SELF_TAG if pqlabel == SELF_TAG else PRIVATE_OTHER_TAG], "when": [pq_ts]},
    })
    cases.append({
        "name": "P-session·各说几条", "system": "",
        "user": f"以下是一段私聊历史：\n{p_session_text}\n\n请回答：这段里「我」和「对方」各说了几条？",
        "expect": {"my": [str(n_self)], "other": [str(n_other)]},
    })

    return cases


# --------------------------------------------------------------------------- #
async def main(args) -> None:
    messages = load_messages(Path(args.history))
    if len(messages) < args.window:
        raise SystemExit(f"历史只有 {len(messages)} 条，不足以取 {args.window} 条窗口。")
    win = pick_window(messages, args.window)
    cases = build_cases(win, args.bot_self_id or 3569937952)

    print(f"窗口 {len(win)} 条 | {fmt_ts(win[0]['time'])} → {fmt_ts(win[-1]['time'])} | 群成员 "
          f"{sorted({str(m['sender'].get('user_id')) for m in win})}")

    cfg_service, runtime = await _init()
    target = await resolve_target(args, cfg_service, runtime)
    key = (target.get("api_key") or target.get("key") or "").strip()
    print(f"Provider: preset={target.get('provider_preset_id','?')} base={target.get('api_base')} "
          f"key={mask_key(key)} model={target.get('model')}")

    provider = get_provider(target)
    results: list[dict] = []
    for i, c in enumerate(cases, 1):
        messages_l = [
            {"role": "system", "content": "你是一个友好的助手。"},
            {"role": "user", "content": c["user"]},
        ]
        t0 = time.monotonic()
        try:
            resp = await provider.chat(messages_l, model=target.get("model"),
                                       temperature=0.2, max_tokens=512, timeout=60)
            answer = resp.text or ""
            usage = resp.usage or {}
        except Exception as e:  # noqa: BLE001
            answer = ""
            usage = {}
        latency = round(time.monotonic() - t0, 2)
        sc = score(answer, c["expect"]) if answer else {}
        ok = bool(sc) and all(sc.values()) if sc else False
        results.append({
            "seq": i, "name": c["name"], "answer": answer,
            "latency_s": latency, "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"), "score": sc,
        })
        print(f"[{i:>2}] {c['name']:<22} {'OK ' if ok else '--  '} {latency:>4}s "
              f"{(answer or '').replace(chr(10), ' ')[:56]}")

    report = {
        "target": {"base": target.get("api_base"), "api_key": mask_key(key), "model": target.get("model")},
        "window": fmt_ts(win[0]["time"]) + " ~ " + fmt_ts(win[-1]["time"]),
        "cases": results,
    }
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    f = out / f"verify_tagging_{time.strftime('%Y%m%d-%H%M%S')}.json"
    f.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入: {f}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="验证新版 LLM 历史打标（10 次真实 API）")
    p.add_argument("--history", required=True)
    p.add_argument("--window", type=int, default=24)
    p.add_argument("--bot-self-id", type=int, default=None, help="bot 自己 QQ（默认 3569937952 或自动）")
    p.add_argument("--preset", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--out-dir", default=str(ROOT / "logs" / "prompt_ablation"))
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
