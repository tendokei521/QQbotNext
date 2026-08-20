"""特殊环境对话诊断：昵称被误当身份 / 群号被误当称呼。

现象（来自真实历史 history_30201c70170f.json）：
- 某用户昵称是「老师，今年的学费也是一次性交吗(1901691195)」，
  真实回复把对方当成了「老师/你的老师」；
- 另一昵称「群耄耋，时不时乱哈(2016494636)」；
- 群号是被注入的上下文（如 群号：1075894179），但群里没有群名。

本脚本用真实昵称/群号构造若干特殊对话环境，让模型回答
「你会怎么称呼发送者 / 你怎么判断 / 群号是什么身份？」，观察
昵称→身份 的误判与 群号→称呼 的误判，输出分析报告 JSON。

Usage:
    python scripts/identity_confusion_test.py --dry-run
    python scripts/identity_confusion_test.py
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

# —— 真实样本（取自 history_30201c70170f.json，bot 3437542570 / group_1075894179）——
GROUP_ID = "1075894179"
TIME = "2026-08-19 22:28:30"
S_MALFORMED = "老师，今年的学费也是一次性交吗(1901691195)"
S_GROUPY = "群耄耋，时不时乱哈(2016494636)"
S_NORMAL = "Iyiy(2934350679)"


def scenario_messages(system_prompt: str, scenario: dict) -> list[dict]:
    """构造一段『群聊记录 + 发送者 + 问题』的请求。"""
    body = scenario["body"]
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"下面是一段群聊记录：\n{body}\n\n{scenario['question']}"},
    ]


# 预置场景（body 用真实昵称/群号，question 为诊断提问）
def build_scenarios() -> list[dict]:
    base = f"群号:{GROUP_ID}\n(时间：{TIME})\n"
    return [
        {
            "id": "S1_nick_as_identity_repro",
            "title": "昵称误当身份重现：发送者是「老师，今年的学费也是一次性交吗」",
            "body": base + f"发送者：{S_MALFORMED}\n发送了：你是谁",
            "question": (
                "请先回答：你会怎么称呼这位发送者？他是什么身份？"
                "然后请用人设自然地把这句话当作它问的「你是谁」来回复。"
            ),
            "focus": "发消息的人被当成什么身份",
        },
        {
            "id": "S2_baseline_normal_nick",
            "title": "对照：发送者是普通昵称「Iyiy」",
            "body": base + f"发送者：{S_NORMAL}\n发送了：你是谁",
            "question": "请先回答：你会怎么称呼这位发送者？他是什么身份？然后用人设自然回复这句「你是谁」。",
            "focus": "普通昵称是否被当作身份",
        },
        {
            "id": "S3_nickname_vs_identity_judge",
            "title": "直接判断题：昵称 vs 身份",
            "body": base + f"发送者：{S_MALFORMED}\n发送了：现在几点了",
            "question": (
                "请严格区分以下两者并回答：\n"
                f"1) 发送条目里写的「{S_MALFORMED}」中的『老师』二字，是对方的【昵称】，"
                "还是真实身份/称谓？\n"
                f"2) 如果只是昵称，你还会称他为『老师』吗？请解释。"
            ),
            "focus": "能否识别昵称≠身份",
        },
        {
            "id": "S4_group_id_is_not_identity",
            "title": "群号误当称呼：1075894179 是什么",
            "body": base + f"发送者：{S_GROUPY}\n发送了：这是谁的群",
            "question": (
                f"请回答：记录开头的『群号:{GROUP_ID}』是一个【真实的群名称/称呼/身份】吗？"
                "还是一个纯数字的群 ID？你能否根据它说出这个群叫什么名字？说明判断依据。"
            ),
            "focus": "群号是否被当成名字/身份",
        },
        {
            "id": "S5_sender_groupy_judge",
            "title": "「群耄耋，时不时乱哈」是昵称还是群实体",
            "body": base + f"发送者：{S_GROUPY}\n发送了：你是谁",
            "question": (
                "请判断：『群耄耋，时不时乱哈(2016494636)』里的『群』字，"
                "是他昵称的一部分，还是代表『群』这个实体？请严格区分昵称与实体，并说明。"
            ),
            "focus": "昵称以『群』开头是否被误当群实体",
        },
    ]


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


# --------------------------------------------------------------------------- #
#  Harness
# --------------------------------------------------------------------------- #
async def main(args) -> None:
    scenarios = build_scenarios()

    if args.dry_run:
        print("=== DRY-RUN（不调用 API）===")
        for s in scenarios:
            print(f"\n--- {s['id']} {s['focus']} ---")
            print(s["body"].replace("\n", " | "))
            print(f"  问: {s['question']}")
        return

    cfg_service, runtime = await _init()
    agent_cfg = resolve_agent_config(cfg_service)
    system_prompt = agent_cfg.get("system_prompt") or "你是一个友好的助手。"
    temperature = float(agent_cfg.get("temperature", 0.7))
    max_tokens = int(agent_cfg.get("max_tokens", 1024))

    target = await resolve_target(args, cfg_service, runtime)
    key = (target.get("api_key") or target.get("key") or "").strip()
    print(f"Agent system_prompt: {system_prompt[:60]}…")
    print(f"Provider: base={target.get('api_base')} key={mask_key(key)} model={target.get('model')}")

    provider = get_provider(target)
    results: list[dict] = []
    seq = 0
    for s in scenarios:
        seq += 1
        messages = scenario_messages(system_prompt, s)
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
        # 启发式标记
        flags = {
            "nickname_as_identity": any(w in answer for w in ["老师就是", "他是老师", "你的老师", "我是老师", "他是我的老师", "你老师"]),
            "calls_malformed_teacher": ("老师" in answer),
            "group_id_as_name": any(w in answer for w in [f"群{GROUP_ID}", "名字是" + GROUP_ID, GROUP_ID + "群"]),
        }
        results.append({"seq": seq, "id": s["id"], "focus": s["focus"], "body": s["body"],
                        "question": s["question"], "answer": answer, "latency_s": latency, "flags": flags})
        print(f"\n[{seq}] {s['id']}  ({s['focus']})  {latency}s")
        print(f"  回答: {answer[:400]}")

    report = {
        "target": {"api_base": target.get("api_base"), "model": target.get("model")},
        "scenarios": results,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"identity_confusion_{time.strftime('%Y%m%d-%H%M%S')}.json"
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
    p = argparse.ArgumentParser(description="特殊环境对话诊断（真实 API）")
    p.add_argument("--preset", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--out-dir", default=str(ROOT / "logs" / "prompt_ablation"))
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
