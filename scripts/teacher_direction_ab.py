"""A/B/C 方向消歧验证：谁是「老师」？

背景：人设里 bot 应当称呼**对方**为「老师」，但现状注入 `发送者：老师，…(QQ)`
会让模型误把「老师」当成「bot 自己是对方的老师」（方向反转，回复『我是你的老师』）。

三种对照（同一真实昵称 / 群号 / 同一问题）：
  A 现状   ：提示词=人设；消息格式 `发送者：X(QQ)`
  B 提示消歧：提示词=人设+「发送者只是昵称，绝非自称老师」；消息格式同 A
  C 标签消歧：提示词=人设（同 A）；消息格式只把标签改成 `发送者昵称：X(QQ)`

每个变体跑 reps 次，模型最后必须输出 `NICK?: 老师=对方`（正确）或 `NICK?: 老师=我`（反转/bug），
统计方向正确率，决定用哪个方案。

Usage:
    python scripts/teacher_direction_ab.py --dry-run
    python scripts/teacher_direction_ab.py --reps 3
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

GROUP_ID = "1075894179"
TIME = "2026-08-19 22:28:30"
S_MALFORMED = "老师，今年的学费也是一次性交吗(1901691195)"

# 贴近用户实际描述的人设：只声明「称呼用户为老师」，不额外钉死方向
BASE_PROMPT = (
    "你是群聊中的角色。你会称呼用户为『老师』。语气自然、符合人设。"
)
DISAMBIG_PROMPT = (
    "\n注意：聊天记录里的『发送者：…』只是对方账号的昵称展示，不是身份声明；"
    "你称呼对方为老师，但绝不能自称老师，也不能说『我是你的老师』。"
)

QUESTION = (
    "请用人设自然回复上面这条消息。并在最后单独一行输出结论，格式严格为：\n"
    "NICK?: 老师=对方\n或\nNICK?: 老师=我"
)


def build_messages(mode: str, system_prompt: str) -> list[dict]:
    if mode == "A":
        sender_line = f"发送者：{S_MALFORMED}"
    elif mode == "B":
        sender_line = f"发送者：{S_MALFORMED}"
    elif mode == "C":
        sender_line = f"发送者昵称：{S_MALFORMED}"
    else:
        raise ValueError(mode)
    body = f"群号:{GROUP_ID}\n(时间：{TIME})\n{sender_line}\n发送了：你是谁"
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"下面是一段群聊记录：\n{body}\n\n{QUESTION}"},
    ]


def system_prompt_for(mode: str) -> str:
    if mode == "B":
        return BASE_PROMPT + DISAMBIG_PROMPT
    return BASE_PROMPT


def parse_direction(answer: str) -> str:
    """返回 correct / inverted / unclear。"""
    a = answer.replace(" ", "").replace("\n", "")
    if "老师=对方" in a:
        return "correct"
    if "老师=我" in a or "我是你的老师" in a or "我是对方的老师" in a or "我是老师" in a:
        return "inverted"
    # 兜底：看正文有没有"我是你的老师"
    if re.search(r"我是(?:你的|这位)?老师", answer):
        return "inverted"
    return "unclear"


# --------------------------------------------------------------------------- #
#  provider/agent 配置
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
        preset = (enabled or presets)[0]
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


def mask_key(key: str) -> str:
    if not key:
        return "(未配置)"
    return f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "****"


async def main(args) -> None:
    modes = ["A", "B", "C"]
    if args.dry_run:
        print("=== DRY-RUN ===")
        for m in modes:
            msgs = build_messages(m, system_prompt_for(m))
            print(f"\n--- 变体 {m} ---")
            print("SYSTEM:", msgs[0]["content"])
            print("USER:", msgs[1]["content"])
        return

    cfg_service, runtime = await _init()
    agent_cfg = resolve_agent_config(cfg_service)
    temperature = float(agent_cfg.get("temperature", 0.7))
    max_tokens = int(agent_cfg.get("max_tokens", 1024))

    target = await resolve_target(args, cfg_service, runtime)
    key = (target.get("api_key") or target.get("key") or "").strip()
    provider = get_provider(target)
    print(f"Provider: base={target.get('api_base')} model={target.get('model')} "
          f"temperature={temperature}\n")

    stats: dict[str, dict] = {m: {"correct": 0, "inverted": 0, "unclear": 0, "answers": []} for m in modes}
    seq = 0
    for m in modes:
        for rep in range(args.reps):
            seq += 1
            messages = build_messages(m, system_prompt_for(m))
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
            direction = parse_direction(answer)
            stats[m][direction] += 1
            stats[m]["answers"].append(answer)
            print(f"[{seq:>2}] {m} rep{rep+1} {latency:>5}s => {direction:<8} | {(answer[:70]).replace(chr(10), ' ')}")

    print("\n===== 汇总 =====")
    for m in modes:
        s = stats[m]
        total = args.reps
        print(f"{m}: 正确(老师=对方) {s['correct']}/{total} | 反转(老师=我) {s['inverted']}/{total} | 不确定 {s['unclear']}")

    report = {
        "target": {"api_base": target.get("api_base"), "model": target.get("model")},
        "base_prompt": BASE_PROMPT,
        "disambig_prompt": DISAMBIG_PROMPT,
        "modes": {m: {k: v for k, v in stats[m].items() if k != "answers"} for m in modes},
        "raw": stats,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"teacher_direction_ab_{time.strftime('%Y%m%d-%H%M%S')}.json"
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
    p = argparse.ArgumentParser(description="老师方向 A/B/C 消歧验证（真实 API）")
    p.add_argument("--reps", type=int, default=3, help="每个变体重复次数")
    p.add_argument("--preset", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--out-dir", default=str(ROOT / "logs" / "prompt_ablation"))
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
