"""消息格式消融实验：昵称 vs 正文 vs 身份。

背景：
- 用户昵称长得像一句话（如「老师，今年的学费也是一次性交吗(1901691195)」）；
- 旧格式 `发送者：X\n发送了：Y` 会把昵称误当对话内容/身份；
- 新格式 `发送者昵称：X\n消息正文：Y` + system 消歧 已落地，但日志显示历史旧格式仍会污染。

本脚本对比多种网络常见格式：
  old      旧格式：发送者：… / 发送了：…
  new      新格式：发送者昵称：… / 消息正文：…（当前实现）
  single   单行冒号：昵称(QQ): 内容（AstrBot group_context 历史格式）
  bracket  方括号分节：[发送者昵称] … / [消息正文] …
  angle    尖括号/IRC：<昵称(QQ)> 内容
  json     JSON 文本：{"sender": "...", "text": "..."}

每个格式让模型回答「消息正文是什么 + 人设中的老师指向谁」，
统计正文识别正确率、昵称泄漏率、身份方向正确率。

Usage:
    python scripts/message_format_ablation.py --dry-run
    python scripts/message_format_ablation.py --reps 3
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

GROUP_ID = "1075894179"
TIME = "2026-08-19 22:28:30"
S_MALFORMED = "老师，今年的学费也是一次性交吗(1901691195)"
NORMAL_TEXT = "你是谁"
LEAK_TEXT = "小人姬看看腿"  # 正文不含“学费”，可检测昵称是否泄漏

# 人设：只声明「称呼用户为老师」，不额外钉死方向
BASE_PROMPT = "你是群聊中的角色。你会称呼用户为『老师』。语气自然、符合人设。"

# 与 app/llm/prompt.py MESSAGE_META_INSTRUCTION 对齐的消歧说明
META_INSTRUCTION = (
    "### 消息格式说明\n"
    "聊天记录中的“发送者：…”或“发送者昵称：…”只是对方账号的昵称展示（可能带有 QQ 号），"
    "不是身份声明，也不是对方说的话。\n"
    "只有“发送了：…”或“消息正文：…”后面才是用户实际发送的消息正文。\n"
    "请把昵称仅当作“谁发的”标识，不要把昵称内容当作对话内容来回复，也不要据此推断对方的真实身份。"
)

QUESTION = (
    "请用人设自然回复上面这条消息。并在最后单独输出两行，格式严格为：\n"
    "TEXT?: <用户实际发送的消息原文>\n"
    "NICK?: 老师=对方\n或\nNICK?: 老师=我"
)

# 每个模式使用什么 system prompt
SYSTEM_PROMPT_FOR = {
    "old": BASE_PROMPT,
    "new": BASE_PROMPT + "\n" + META_INSTRUCTION,
    "single": BASE_PROMPT + "\n" + META_INSTRUCTION,
    "bracket": BASE_PROMPT + "\n" + META_INSTRUCTION,
    "angle": BASE_PROMPT + "\n" + META_INSTRUCTION,
    "json": BASE_PROMPT + "\n" + META_INSTRUCTION,
}


def build_body(mode: str, sender: str, text: str) -> str:
    """按指定格式构造群聊记录正文。"""
    if mode == "old":
        return f"群号:{GROUP_ID}\n(时间：{TIME})\n发送者：{sender}\n发送了：{text}"
    if mode == "new":
        return f"群号:{GROUP_ID}\n(时间：{TIME})\n发送者昵称：{sender}\n消息正文：{text}"
    if mode == "single":
        return f"群号:{GROUP_ID}\n(时间：{TIME})\n{sender}: {text}"
    if mode == "bracket":
        return (
            f"群号:{GROUP_ID}\n(时间：{TIME})\n"
            f"[发送者昵称] {sender}\n[消息正文] {text}"
        )
    if mode == "angle":
        return f"群号:{GROUP_ID}\n(时间：{TIME})\n<{sender}> {text}"
    if mode == "json":
        payload = json.dumps({"sender": sender, "text": text}, ensure_ascii=False)
        return f"群号:{GROUP_ID}\n(时间：{TIME})\n{payload}"
    raise ValueError(mode)


def build_messages(mode: str, text: str) -> list[dict]:
    body = build_body(mode, S_MALFORMED, text)
    return [
        {"role": "system", "content": SYSTEM_PROMPT_FOR[mode]},
        {"role": "user", "content": f"下面是一段群聊记录：\n{body}\n\n{QUESTION}"},
    ]


def parse_answer(answer: str, expected_text: str) -> dict:
    """从模型回答中解析 TEXT? / NICK? 并做启发式泄漏检测。"""
    a = answer or ""
    text_line = ""
    nick_line = ""
    for line in a.splitlines():
        s = line.strip()
        if s.startswith("TEXT?"):
            text_line = s
        elif s.startswith("NICK?"):
            nick_line = s

    # 正文识别：期望正文应出现在 TEXT? 行中
    text_ok = expected_text in text_line or expected_text in a.replace(" ", "")

    # 身份方向：NICK? 行应为 老师=对方
    nick_ok = "老师=对方" in nick_line.replace(" ", "")

    # 昵称泄漏：正文本身不含“学费”，若回答正文里出现“学费/财务/缴费”，视为把昵称当内容
    leak = any(w in a for w in ("学费", "财务", "缴费", "拖延学费")) and expected_text != "学费"
    return {
        "text_ok": bool(text_ok),
        "nick_ok": bool(nick_ok),
        "nickname_leak": bool(leak),
        "text_line": text_line,
        "nick_line": nick_line,
    }


# --------------------------------------------------------------------------- #
#  provider/agent 配置（复用 teacher_direction_ab 的 harness）
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
        # 默认优先使用 opencode go 预设（zen 预设容易触发上游 429 限流）
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
    modes = list(SYSTEM_PROMPT_FOR.keys())
    scenarios = [
        {"id": "simple", "text": NORMAL_TEXT},
        {"id": "leak", "text": LEAK_TEXT},
    ]

    if args.dry_run:
        print("=== DRY-RUN（不调用 API）===")
        for mode in modes:
            for sc in scenarios:
                msgs = build_messages(mode, sc["text"])
                print(f"\n--- {mode} / {sc['id']} ---")
                print("SYSTEM:", msgs[0]["content"][:120])
                print("USER:", msgs[1]["content"][:300])
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
    for mode in modes:
        for sc in scenarios:
            for rep in range(args.reps):
                seq += 1
                messages = build_messages(mode, sc["text"])
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
                parsed = parse_answer(answer, sc["text"])
                stats.setdefault(mode, {"simple": {"ok": 0, "leak": 0, "total": 0},
                                        "leak": {"ok": 0, "leak": 0, "total": 0}})
                s = stats[mode][sc["id"]]
                s["total"] += 1
                if parsed["text_ok"] and parsed["nick_ok"]:
                    s["ok"] += 1
                if parsed["nickname_leak"]:
                    s["leak"] += 1
                results.append({
                    "seq": seq,
                    "mode": mode,
                    "scenario": sc["id"],
                    "body": messages[1]["content"],
                    "answer": answer,
                    "latency_s": latency,
                    **parsed,
                })
                print(f"[{seq:>2}] {mode:>7}/{sc['id']:<6} rep{rep+1} "
                      f"text={parsed['text_ok']} nick={parsed['nick_ok']} "
                      f"leak={parsed['nickname_leak']} | {(answer[:60]).replace(chr(10), ' ')}")

    print("\n===== 汇总（text_ok 且 nick_ok = 完全正确） =====")
    for mode in modes:
        for sc in scenarios:
            s = stats[mode][sc["id"]]
            total = max(1, s["total"])
            print(f"{mode:>7}/{sc['id']:<6}: 完全正确 {s['ok']}/{s['total']} "
                  f"({s['ok']/total:.0%}) | 昵称泄漏 {s['leak']}/{s['total']}")

    report = {
        "target": {"api_base": target.get("api_base"), "model": target.get("model")},
        "modes": modes,
        "scenarios": [s["id"] for s in scenarios],
        "meta_instruction": META_INSTRUCTION,
        "stats": stats,
        "raw": results,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"message_format_ablation_{time.strftime('%Y%m%d-%H%M%S')}.json"
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
    p = argparse.ArgumentParser(description="消息格式消融实验（真实 API）")
    p.add_argument("--reps", type=int, default=2, help="每个格式/场景重复次数")
    p.add_argument("--preset", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--out-dir", default=str(ROOT / "logs" / "prompt_ablation"))
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
