"""Prompt / context presentation ablation harness.

Ablation harness that reuses the project's REAL OpenAI-compatible provider chain
(Provider preset + Provider model read from data/app.db) to send real chat
requests, then compares how different prompt styles and context window sizes
help a language model attribute WHO said WHAT and WHEN in a multi-speaker,
multi-day group-chat transcript.

Key questions this tries to answer:
- Does adding explicit timestamps/dates help a model reason about time order
  (including crossing midnight)?
- Does an explicit analysis-instruction system prompt reduce identity
  confusion (nicknames that look alike, same text by different speakers)?
- How does the amount of prior context (round size) affect consistency and cost?

Usage:
    python scripts/prompt_context_ablation.py --dry-run     # build requests, no API calls
    python scripts/prompt_context_ablation.py               # run against the real configured API
    python scripts/prompt_context_ablation.py --variants A,D --rounds 2   # subset
    python scripts/prompt_context_ablation.py --transcript path/to.json --questions path/2.json

The real API key is read from provider presets but never printed (only masked).
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
from app.llm.providers import get_provider  # noqa: E402
from app.llm.providers.runtime_manager import ProviderRuntimeManager  # noqa: E402

# --------------------------------------------------------------------------- #
#  Sample multi-speaker, multi-day transcript (synthetic, editable)
# --------------------------------------------------------------------------- #
DEFAULT_TRANSCRIPT: list[dict] = [
    {"day": "03-15", "time": "09:10", "speaker": "阿伟", "text": "你们到了吗？我快到地铁站了"},
    {"day": "03-15", "time": "09:12", "speaker": "小红", "text": "我还堵在路上，估计还要20分钟"},
    {"day": "03-15", "time": "09:13", "speaker": "李总", "text": "给大家介绍一下，这位是合作方的伟哥，今天一起开会"},
    {"day": "03-15", "time": "09:14", "speaker": "伟哥", "text": "大家好，我是伟哥，请多关照"},
    {"day": "03-15", "time": "09:20", "speaker": "张三", "text": "我先去开个短会，回来说方案"},
    {"day": "03-15", "time": "09:25", "speaker": "阿伟", "text": "好"},
    {"day": "03-15", "time": "09:31", "speaker": "阿伟", "text": "好的"},
    {"day": "03-15", "time": "09:32", "speaker": "小红", "text": "好的"},
    {"day": "03-15", "time": "09:40", "speaker": "张三", "text": "好好好，都安静，听我说"},
    {"day": "03-15", "time": "09:41", "speaker": "张三", "text": "我觉得方案A更好，成本低一半"},
    {"day": "03-15", "time": "09:45", "speaker": "伟哥", "text": "但我这边实测方案A会超时，还是B更稳"},
    {"day": "03-15", "time": "09:50", "speaker": "张三", "text": "行，那按B走，李总同意吗？"},
    {"day": "03-15", "time": "09:51", "speaker": "李总", "text": "同意，按B"},
    {"day": "03-15", "time": "10:05", "speaker": "阿伟", "text": "我到了，在楼下"},
    {"day": "03-15", "time": "10:06", "speaker": "小红", "text": "我电梯上来了"},
    {"day": "03-15", "time": "10:07", "speaker": "李总", "text": "会议室已经订好，10:30 见"},
    {"day": "03-15", "time": "10:20", "speaker": "张三", "text": "那我先去拉个B的报价"},
    {"day": "03-15", "time": "22:50", "speaker": "阿伟", "text": "报价需求我发群里了"},
    {"day": "03-15", "time": "23:58", "speaker": "张三", "text": "我再盯一会，你们先睡"},
    {"day": "03-16", "time": "00:02", "speaker": "阿伟", "text": "起来干活了，报价我改好了"},
    {"day": "03-16", "time": "00:03", "speaker": "小红", "text": "凌晨了还干活？真的假的"},
    {"day": "03-16", "time": "14:00", "speaker": "张三", "text": "昨天的方案定了，按B，感谢伟哥的实测"},
]

# --------------------------------------------------------------------------- #
#  Presentation variants (prompt styles)
# --------------------------------------------------------------------------- #
SYSTEM_PROMPTS = {
    "A": "",  # raw JSON, no explicit instruction
    "B": "",  # plain "speaker: text" lines, NO timestamps (negative control)
    "C": "",  # stamped lines "D H:M speaker: text", no meta instruction
    "D": (
        "你是一个群聊记录分析助手。每条记录格式为「日期 时间 昵称: 内容」。"
        "回答务必以记录为准：区分不同昵称（昵称不同并不代表同一个人，除非记录有证据）；"
        "比较时间时先比日期、再比时分。"
    ),
    "E": (
        "你是一个严格的对话记录仲裁员。规则：\n"
        "1) 只依据给定记录作答，不要编造记录里没有的信息；\n"
        "2) 发言人身份以记录中每一行的昵称为准，两行昵称不同就不是同一人，除非记录明确指出；\n"
        "3) 时间先后先比较日期，再比较时刻（注意跨天，例如 23:58 在前、次日 00:02 在后）；\n"
        "4) 无法从记录判断时，明确说明「无法依据记录确定」，不要臆测。"
    ),
}

# Context progress: show the first N lines for each round.
ROUND_SIZES = [9, 14, None]  # None = full transcript

QUESTIONS = [
    {
        "q": "记录里一共有几个人发言？其中「伟哥」和「阿伟」是同一个人吗？请给出你的依据。",
        "expect": {
            "人数含张三": ["5", "五个", "5 人", "5人"],
            "伟哥≠阿伟": ["不是同一个人", "不是同一人", "不同的人", "两个人", "不是"],
            "给出依据": ["介绍", "李总", "合作方", "09:13"],
        },
    },
    {
        "q": "「方案B」最后是几点、由谁拍板同意的？09:45 那句话是谁说的？",
        "expect": {
            "拍板时间09:51": ["09:51", "9:51"],
            "拍板人李总": ["李总"],
            "09:45是伟哥": ["伟哥"],
        },
    },
    {
        "q": "按时间先后（注意跨天）：23:58 的「我再盯一会」和 00:02 的「起来干活了」哪个在前？分别是哪天几点？",
        "expect": {
            "23:58在前": ["23:58", "3-15", "03-15", "15号"],
            "00:02在后(次日)": ["00:02", "3-16", "03-16", "16号"],
            "明确指出跨天": ["后一天", "次日", "第二天", "跨天", "凌晨"],
        },
    },
]

# --------------------------------------------------------------------------- #
#  Rendering
# --------------------------------------------------------------------------- #
def format_line(variant: str, line: dict) -> str:
    """Render a single transcript line for a given presentation variant."""
    if variant == "B":
        return f'{line["speaker"]}: {line["text"]}'
    return f'{line["day"]} {line["time"]} {line["speaker"]}: {line["text"]}'


def build_messages(variant: str, items: list[dict], question: str) -> list[dict]:
    """Build the real OpenAI-style messages payload for a variant + slice."""
    messages: list[dict] = []
    sys_prompt = SYSTEM_PROMPTS[variant]
    if sys_prompt:
        messages.append({"role": "system", "content": sys_prompt})

    if variant == "A":
        body = (
            "以下是群聊记录（JSON 数组，字段为 day/time/speaker/text，已按接收顺序排列）：\n"
            + json.dumps(items, ensure_ascii=False)
            + f"\n\n请回答：{question}"
        )
        messages.append({"role": "user", "content": body})
        return messages

    lines = "\n".join(format_line(variant, item) for item in items)
    messages.append({"role": "user", "content": f"以下为群聊记录：\n{lines}\n\n请回答：\n{question}"})
    return messages


# --------------------------------------------------------------------------- #
#  Scoring
# --------------------------------------------------------------------------- #
def score_answer(variant: str, round_idx: int, answer: str, expect: dict) -> dict:
    """Lightweight rubric scoring via substring matching (for quick triage)."""
    answer = (answer or "").replace(" ", "").replace("\n", "")
    result: dict[str, bool | str] = {"variant": variant, "round": round_idx}
    for label, keywords in (expect or {}).items():
        hit = any(k.replace(" ", "") in answer for k in keywords)
        result[label] = hit
    result["cautious"] = any(w in answer for w in ["无法依据记录", "无法确定", "不确定"])
    return result


def mask_key(key: str) -> str:
    if not key:
        return "(未配置)"
    if len(key) <= 8:
        return "****(已打码)"
    return f"{key[:4]}...{key[-4:]}"


# --------------------------------------------------------------------------- #
#  Target resolution (project's real request source)
# --------------------------------------------------------------------------- #
async def resolve_target(args) -> tuple[dict, dict]:
    """Load the real DB-backed provider target like the running app does."""
    settings = Settings()
    db = Database(settings.db_path)
    await db.connect()
    cfg_service = ConfigService(db, settings.project_root)
    await cfg_service.init()
    runtime = ProviderRuntimeManager(cfg_service)
    return cfg_service, runtime


async def pick_target(args, cfg_service, runtime) -> dict:
    """Pick the provider config + model from the real presets/models."""
    presets = cfg_service.list_provider_presets()
    if not presets:
        raise SystemExit("未找到任何 Provider 预设，请先在 WebUI「Provider 预设」配置真实 api_base/api_key。")

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
        raise SystemExit("无法从预设/模型解析出可用的 Provider 配置。")

    target.setdefault("model", target.get("model") or "deepseek-chat")
    return target


# --------------------------------------------------------------------------- #
#  Harness
# --------------------------------------------------------------------------- #
async def run_case(
    provider,
    target: dict,
    variant: str,
    transcript: list[dict],
    questions: list[dict],
    args,
) -> dict:
    """Run all rounds (context windows) for one variant against the real API."""
    sizes = [s for s in ROUND_SIZES[: args.rounds]]
    rows: list[dict] = []
    for r, size in enumerate(sizes):
        items = transcript if size is None else transcript[:size]
        question = questions[r]["q"]
        messages = build_messages(variant, items, question)
        t0 = time.monotonic()
        error = ""
        answer = ""
        usage: dict = {}
        try:
            resp = await provider.chat(
                messages,
                model=target.get("model"),
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                timeout=args.timeout,
            )
            answer = resp.text or ""
            usage = resp.usage or {}
        except Exception as e:  # noqa: BLE001 - record and continue
            error = f"{type(e).__name__}: {e}"
        latency = round(time.monotonic() - t0, 2)

        row = {
            "variant": variant,
            "round": r + 1,
            "context_lines": len(items),
            "prompt_chars": sum(len(m.get("content", "")) for m in messages),
            "system_prompt": SYSTEM_PROMPTS[variant][:40],
            "question": question,
            "answer": answer,
            "latency_s": latency,
            "prompt_tokens": (usage.get("prompt_tokens") if isinstance(usage, dict) else None),
            "completion_tokens": (usage.get("completion_tokens") if isinstance(usage, dict) else None),
            "error": error,
        }
        if not args.dry_run:
            row["score"] = score_answer(variant, r + 1, answer, questions[r]["expect"])
        rows.append(row)
    return {"variant": variant, "rows": rows}


async def main(args) -> None:
    if args.dry_run:
        transcript = args.transcript or DEFAULT_TRANSCRIPT
        print("=== DRY-RUN: 构建请求但不发送（不消耗 API） ===\n")
        sizes = [s for s in ROUND_SIZES[: args.rounds]]
        for variant in args.variants:
            print(f"--- 变体 {variant}: {SYSTEM_PROMPTS[variant][:30] or '(无 system 提示)'} ---")
            for r, size in enumerate(sizes):
                items = transcript if size is None else transcript[:size]
                messages = build_messages(variant, items, QUESTIONS[r]["q"])
                total_chars = sum(len(m.get("content", "")) for m in messages)
                print(
                    f"  轮次{r + 1}: 上下文 {len(items)} 行 | 请求 {len(messages)} 条消息 | "
                    f"约 {total_chars} 字符 | Q: {QUESTIONS[r]['q'][:24]}…"
                )
        print("\n（以上仅展示请求形状，未调用真实 API）")
        return

    cfg_service, runtime = await resolve_target(args)
    target = await pick_target(args, cfg_service, runtime)
    key = (target.get("api_key") or target.get("key") or "").strip()
    print(f"Provider preset: {target.get('provider_preset_id', '?')} | "
          f"api_base: {target.get('api_base')} | "
          f"api_key: {mask_key(key)} | model: {target.get('model')}")
    if not key:
        print("警告：预设中未解析到 api_key，请求很可能失败。")

    transcript = args.transcript or DEFAULT_TRANSCRIPT
    questions = args.questions or QUESTIONS
    provider = get_provider(target)

    sem = asyncio.Semaphore(args.concurrency)
    results: list[dict] = []

    async def guarded(variant: str):
        async with sem:
            return await run_case(provider, target, variant, transcript, questions, args)

    for variant in args.variants:
        results.append(await guarded(variant))

    report = {
        "target": {
            "provider": target.get("provider"),
            "api_base": target.get("api_base"),
            "api_key": mask_key(key),
            "model": target.get("model"),
        },
        "variants_order": args.variants,
        "round_sizes": ROUND_SIZES[: args.rounds],
        "results": results,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    (out_dir / f"raw_answers_{ts}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n原始结果已写入: {out_dir / f'raw_answers_{ts}.json'}")

    _print_summary(report)


def _print_summary(report: dict) -> None:
    print("\n=== 汇总（按变体 × 轮次） ===")
    header = f"{'变体':<4} {'轮次':<4} {'行数':<5} {'延迟s':<7} {'入token':<10} {'出token':<10} {'得分':<8} 答"
    print(header)
    print("-" * 90)
    for v in report["results"]:
        for row in v["rows"]:
            score = row.get("score") or {}
            ok = sum(1 for k in ("人数含张三", "伟哥≠阿伟", "拍板时间09:51", "拍板人李总", "09:45是伟哥", "23:58在前", "00:02在后(次日)", "明确指出跨天") if score.get(k))
            total = sum(1 for k in score if k.startswith(("人数", "伟哥", "拍板", "09", "23", "00")))
            cautious = "C" if score.get("cautious") else " "
            ans = (row.get("answer") or row.get("error") or "")[:36].replace("\n", " ")
            err = "ERR" if row.get("error") else ""
            print(
                f"{row['variant']:<4} {row['round']:<4} {row['context_lines']:<5} "
                f"{str(row['latency_s']):<7} {str(row.get('prompt_tokens')):<10} "
                f"{str(row.get('completion_tokens')):<10} {f'{ok}/{total}':<8}{cautious} {err} {ans}"
            )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="提示词/上下文呈现方式消融测试（调用项目真实 API）")
    p.add_argument("--preset", default=None, help="指定 Provider 预设 id（默认第一个启用的预设）")
    p.add_argument("--model", default=None, help="指定 Provider 模型 id")
    p.add_argument(
        "--variants",
        default="A,B,C,D,E",
        help="要测试的呈现变体，逗号分隔（A=原始JSON B=无时间戳 C=带时间戳 D=结构化引导 E=仲裁员规则），默认全部",
    )
    p.add_argument("--rounds", type=int, default=3, choices=[1, 2, 3], help="上下文轮次（窗口大小）数量")
    p.add_argument("--temperature", type=float, default=0.2, help="采样温度")
    p.add_argument("--max-tokens", type=int, default=512, help="单次回复最大 token")
    p.add_argument("--timeout", type=int, default=60, help="单次请求超时（秒）")
    p.add_argument("--concurrency", type=int, default=1, help="并发数（默认 1，避免限流）")
    p.add_argument("--transcript", default=None, help="外部聊天记录 JSON 文件（数组，字段 day/time/speaker/text）")
    p.add_argument("--questions", default=None, help="外部问题 JSON 文件（可选）")
    p.add_argument("--out-dir", default=str(ROOT / "logs" / "prompt_ablation"), help="报告输出目录")
    p.add_argument("--dry-run", action="store_true", help="只构建请求并打印形状，不调用真实 API")
    return p.parse_args()


if __name__ == "__main__":
    ns = parse_args()
    ns.variants = [v.strip().upper() for v in ns.variants.split(",") if v.strip()]
    if ns.transcript:
        ns.transcript = json.loads(Path(ns.transcript).read_text(encoding="utf-8"))
    if ns.questions:
        ns.questions = json.loads(Path(ns.questions).read_text(encoding="utf-8"))
    asyncio.run(main(ns))
