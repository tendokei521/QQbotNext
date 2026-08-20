"""干净历史消融实验：编造“已应用新格式/脱敏后”的多轮对话数据。

目的：
- 模拟旧历史已清洗/迁移后的效果，避免旧格式污染；
- 对比多种方案：
  json_safe   JSON + 昵称脱敏
  text_safe   分节文本（发送者昵称/消息正文）+ 昵称脱敏
  single_safe 单行 `用户<QQ>: 内容` + 昵称脱敏
  role_safe   角色化（用户A/用户B），不暴露原始昵称
  json_raw    JSON + 原始昵称（对照）
  text_raw    分节文本 + 原始昵称（对照）

历史轮次是编造的，但格式完全按“应用新方案后”的样子生成。

Usage:
    python scripts/clean_history_ablation.py --dry-run
    python scripts/clean_history_ablation.py --reps 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
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

TIME = "2026-08-19 22:28:30"
S_SENTENCE = "老师，今年的学费也是一次性交吗(1901691195)"
S_NORMAL = "Iyiy(2934350679)"
MENTION = "洛洛不是糯糯(235218197)"
QUOTE_SENDER = "Iyiy(2934350679)"
QUOTE_TEXT = "来测"
TEXT_WHO = "你是谁"
TEXT_LEAK = "小人姬看看腿"

BASE_PROMPT = "你是群聊中的角色。你会称呼用户为『老师』。语气自然、符合人设。"

TEXT_META_INSTRUCTION = (
    "### 消息格式说明\n"
    "聊天记录中的“发送者昵称：…”只是发送者的昵称/角色展示（可能带有 QQ 号），不是身份声明，也不是对方说的话。\n"
    "只有“消息正文：…”后面才是用户实际发送的消息正文。\n"
    "请把昵称仅当作“谁发的”标识，不要把昵称内容当作对话内容来回复，也不要据此推断对方的真实身份。"
)

JSON_INSTRUCTION = (
    "### 消息格式说明\n"
    "以下消息以 JSON 格式提供：\n"
    "- sender 只是发送者的昵称/角色展示，不是身份声明，也不是对方说的话；\n"
    "- time / mentioned / quote 都是元信息；\n"
    "- 只有 text 字段才是用户实际发送的消息正文。"
)

QUESTION = (
    "请用人设自然回复当前这条消息。并在最后单独输出两行，格式严格为：\n"
    "TEXT?: <用户实际发送的消息原文>\n"
    "NICK?: 老师=对方\n或\nNICK?: 老师=我"
)

# 编造的“已应用新格式”历史轮次（真实感弱，仅用于格式效果预演）
CLEAN_HISTORY = [
    {"time": "2026-08-19 22:27:30", "sender": S_SENTENCE, "text": TEXT_LEAK},
    {"time": "2026-08-19 22:28:00", "sender": S_NORMAL, "text": QUOTE_TEXT},
    {"time": "2026-08-19 22:28:15", "sender": S_SENTENCE, "text": TEXT_WHO},
]


def safe_sender(sender: str) -> str:
    """脱敏：句子型/超长昵称替换为 用户<QQ>；普通昵称保留。"""
    if "(" in sender and sender.endswith(")"):
        nick, _, rest = sender.rpartition("(")
        qq = rest.rstrip(")")
        if len(nick) > 12 or any(c in nick for c in "，。！？、；：,.!?;:"):
            return f"用户{qq}"
    return sender


def role_sender(sender: str) -> str:
    """角色化：句子型昵称 -> 用户A，普通昵称 -> 用户B。"""
    return "用户A" if sender == S_SENTENCE else "用户B"


def _sender_for(variant: str, sender: str) -> str:
    if variant == "role_safe":
        return role_sender(sender)
    if variant.endswith("_safe"):
        return safe_sender(sender)
    return sender


def render_history(variant: str, history: list[dict]) -> str:
    """把历史轮次渲染成指定方案格式。"""
    lines = []
    for item in history:
        sender = _sender_for(variant, item["sender"])
        text = item["text"]
        if variant.startswith("json"):
            payload = {"time": item["time"], "sender": sender, "text": text}
            lines.append(json.dumps(payload, ensure_ascii=False))
        elif variant.startswith("text") or variant == "role_safe":
            lines.append(f"(时间：{item['time']})\n发送者昵称：{sender}\n消息正文：{text}")
        elif variant.startswith("single"):
            lines.append(f"(时间：{item['time']}) {sender}: {text}")
    return "\n".join(lines)


def build_messages(variant: str, scenario_id: str) -> list[dict]:
    """构造当前消息 + 干净历史。"""
    if scenario_id == "normal":
        current_sender = S_NORMAL
        current_text = TEXT_WHO
        full = False
    elif scenario_id == "full_meta":
        current_sender = S_SENTENCE
        current_text = TEXT_LEAK
        full = True
    else:
        current_sender = S_SENTENCE
        current_text = TEXT_LEAK if scenario_id == "leak" else TEXT_WHO
        full = False

    sender = _sender_for(variant, current_sender)

    if variant.startswith("json"):
        payload = {"sender": sender, "text": current_text}
        if full:
            payload = {
                "time": TIME,
                "sender": sender,
                "mentioned": [MENTION],
                "quote": {"sender": QUOTE_SENDER, "text": QUOTE_TEXT},
                "text": current_text,
            }
        current = json.dumps(payload, ensure_ascii=False)
    elif variant.startswith("text") or variant == "role_safe":
        current = f"(时间：{TIME})\n发送者昵称：{sender}\n消息正文：{current_text}"
        if full:
            current = (
                f"(时间：{TIME})\n发送者昵称：{sender}\n"
                f"提到了(用户名)：{MENTION}\n"
                f"引用了：{QUOTE_SENDER}发送的引用消息：“{QUOTE_TEXT}”\n"
                f"消息正文：{current_text}"
            )
    elif variant.startswith("single"):
        current = f"(时间：{TIME}) {sender}: {current_text}"

    history = render_history(variant, CLEAN_HISTORY)
    system_prompt = BASE_PROMPT + "\n" + (
        JSON_INSTRUCTION if variant.startswith("json") else TEXT_META_INSTRUCTION
    )
    user_content = (
        "下面是一段群聊记录（历史已按统一格式保存）：\n"
        f"最近历史：\n{history}\n"
        f"当前消息：\n{current}\n\n"
        f"{QUESTION}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


SCENARIOS = {
    "sentence": {"text": TEXT_WHO, "desc": "句子型昵称历史 + 当前"},
    "leak": {"text": TEXT_LEAK, "desc": "句子型昵称历史 + 易泄漏正文"},
    "full_meta": {"text": TEXT_LEAK, "desc": "完整用户信息感知"},
    "normal": {"text": TEXT_WHO, "desc": "普通昵称对照组"},
}


def parse_answer(answer: str, expected_text: str) -> dict:
    a = answer or ""
    text_line = ""
    nick_line = ""
    for line in a.splitlines():
        s = line.strip()
        if s.startswith("TEXT?"):
            text_line = s
        elif s.startswith("NICK?"):
            nick_line = s

    text_ok = expected_text in text_line or expected_text in a.replace(" ", "")
    nick_ok = "老师=对方" in nick_line.replace(" ", "")
    leak = any(w in a for w in ("学费", "财务", "缴费", "拖延学费")) and expected_text != "学费"
    return {
        "text_ok": bool(text_ok),
        "nick_ok": bool(nick_ok),
        "nickname_leak": bool(leak),
        "text_line": text_line,
        "nick_line": nick_line,
    }


# --------------------------------------------------------------------------- #
#  Provider harness（默认 opencode go 预设）
# --------------------------------------------------------------------------- #
def resolve_agent_config(cfg_service) -> dict:
    stored = cfg_service.get_module_config(AGENT_MODULE, None) or {}
    return {**DEFAULT_LLM_CONFIG, **stored}


async def resolve_target(args, cfg_service, runtime) -> dict:
    presets = cfg_service.list_provider_presets()
    if not presets:
        raise SystemExit("未找到任何 Provider 预设，请先在 WebUI「Provider 预设」配置。")
    preset = args.preset and cfg_service.get_provider_preset(args.preset)
    if preset is None:
        enabled = [p for p in presets if p.get("enabled")]
        preset = next(
            (p for p in enabled if "opencode" in str(p.get("name", "")).lower()),
            None,
        ) or (enabled[0] if enabled else None)
    target = args.model and runtime.resolve_provider_config(args.model)
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


async def main(args) -> None:
    variants = ["json_safe", "text_safe", "single_safe", "role_safe", "json_raw", "text_raw"]
    scenario_ids = list(SCENARIOS.keys())

    if args.dry_run:
        print("=== DRY-RUN（不调用 API）===")
        print("编造的干净历史（CLEAN_HISTORY）：")
        for item in CLEAN_HISTORY:
            print(item)
        for variant in variants:
            for sid in scenario_ids:
                msgs = build_messages(variant, sid)
                print(f"\n--- {variant} / {sid} ---")
                print("SYSTEM:", msgs[0]["content"][:120])
                print("USER:", msgs[1]["content"][:500])
        return

    cfg_service, runtime = await _init()
    agent_cfg = resolve_agent_config(cfg_service)
    temperature = float(agent_cfg.get("temperature", 0.7))
    max_tokens = int(agent_cfg.get("max_tokens", 1024))

    target = await resolve_target(args, cfg_service, runtime)
    provider = get_provider(target)
    print(f"Provider: base={target.get('api_base')} model={target.get('model')} "
          f"temperature={temperature}\n")

    stats: dict[str, dict] = {}
    results: list[dict] = []
    seq = 0
    for variant in variants:
        for sid in scenario_ids:
            expected = SCENARIOS[sid]["text"]
            for rep in range(args.reps):
                seq += 1
                messages = build_messages(variant, sid)
                t0 = time.monotonic()
                try:
                    resp = await provider.chat(
                        messages, model=target.get("model"),
                        temperature=temperature, max_tokens=max_tokens, timeout=args.timeout,
                    )
                    answer = resp.text or ""
                except Exception as e:  # noqa: BLE001
                    answer = f"[error] {e}"
                latency = round(time.monotonic() - t0, 2)
                parsed = parse_answer(answer, expected)
                stats.setdefault(variant, {})
                stats[variant].setdefault(sid, {"ok": 0, "leak": 0, "total": 0})
                s = stats[variant][sid]
                s["total"] += 1
                if parsed["text_ok"] and parsed["nick_ok"]:
                    s["ok"] += 1
                if parsed["nickname_leak"]:
                    s["leak"] += 1
                results.append({
                    "seq": seq,
                    "variant": variant,
                    "scenario": sid,
                    "body": messages[1]["content"],
                    "answer": answer,
                    "latency_s": latency,
                    **parsed,
                })
                print(f"[{seq:>2}] {variant:>10}/{sid:<8} rep{rep+1} "
                      f"text={parsed['text_ok']} nick={parsed['nick_ok']} "
                      f"leak={parsed['nickname_leak']} | {(answer[:50]).replace(chr(10), ' ')}")

    print("\n===== 汇总（text_ok 且 nick_ok = 完全正确） =====")
    for variant in variants:
        for sid in scenario_ids:
            s = stats[variant][sid]
            total = max(1, s["total"])
            print(f"{variant:>10}/{sid:<8}: 完全正确 {s['ok']}/{s['total']} "
                  f"({s['ok']/total:.0%}) | 昵称泄漏 {s['leak']}/{s['total']}")

    report = {
        "target": {"api_base": target.get("api_base"), "model": target.get("model")},
        "clean_history": CLEAN_HISTORY,
        "variants": variants,
        "scenarios": {sid: SCENARIOS[sid]["desc"] for sid in scenario_ids},
        "stats": stats,
        "raw": results,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"clean_history_ablation_{time.strftime('%Y%m%d-%H%M%S')}.json"
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
    p = argparse.ArgumentParser(description="干净历史消融实验（真实 API）")
    p.add_argument("--reps", type=int, default=2, help="每个变体/场景重复次数")
    p.add_argument("--preset", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--timeout", type=int, default=45)
    p.add_argument("--out-dir", default=str(ROOT / "logs" / "prompt_ablation"))
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
