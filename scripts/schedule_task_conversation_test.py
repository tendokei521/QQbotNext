"""定时任务对话回归测试：复现日志中的真实私聊对话，观察 schedule_task 调用。

背景（来自日志）：
- bot #3569937952，会话 private_1901691195
- 用户先问「睡不着」「在吗」
- 然后说「每天早上六点提醒我吃药吧」
- 紧接着说「另外的，晚上十点提醒我睡觉」
- 旧版问题：工具循环重复 create，且 create 返回截断 id 导致 delete 失败，
  最终留下重复/错误任务。

本脚本直接复用现有 Provider API（get_provider / OpenAICompatible chat + tools）
与真实 TaskScheduler / handle_schedule_tool，按上述对话逐步请求，输出：
- 每轮用户输入、模型回复、工具调用
- 最终任务列表与重复检测结果

Usage:
    python scripts/schedule_task_conversation_test.py --dry-run
    python scripts/schedule_task_conversation_test.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.settings import Settings  # noqa: E402
from app.infrastructure.config.config_service import ConfigService  # noqa: E402
from app.infrastructure.persistence.database import Database  # noqa: E402
from app.llm.config import DEFAULT_LLM_CONFIG  # noqa: E402
from app.llm.prompt import LEGACY_MESSAGE_META_INSTRUCTION, build_messages  # noqa: E402
from app.llm.providers import get_provider  # noqa: E402
from app.llm.providers.openai_compat import OpenAICompatProvider  # noqa: E402
from app.llm.providers.runtime_manager import ProviderRuntimeManager  # noqa: E402
from app.llm.scheduler import (  # noqa: E402
    TaskScheduler,
    build_schedule_tool,
    handle_schedule_tool,
)
from app.llm.tool import make_executor  # noqa: E402
from app.llm.time_parser import parse_schedule  # noqa: E402

AGENT_MODULE = "agent"
BOT_ID = "3569937952"
SESSION_ID = "private_1901691195"
USER_ID = "1901691195"

# 对话内容参考日志
CONVERSATION = [
    "睡不着",
    "在吗",
    "每天早上六点提醒我吃药吧",
    "另外的，晚上十点提醒我睡觉",
]


# --------------------------------------------------------------------------- #
#  最小 TaskScheduler 运行环境
# --------------------------------------------------------------------------- #
class _FakeBot:
    pass


class _FakeServices:
    def __init__(self):
        from app.core.task_manager import get_task_manager

        self.task_manager = get_task_manager()


class _FakeCtx:
    def __init__(self):
        self.bot = _FakeBot()
        self.services = _FakeServices()


class _FakeConfig:
    def get(self, key, default=None):
        return default


class _FakeModule:
    def __init__(self):
        self.bot_id = BOT_ID
        self.module_name = "llm_chat_v2"
        self.ctx = _FakeCtx()
        self.config = _FakeConfig()
        self.scheduler = None


def _task_signature(task: dict) -> tuple | None:
    """按重复方式 + 触发时刻生成签名，用于检测重复任务。"""
    parsed = parse_schedule(task.get("trigger_expr") or "")
    if parsed is None:
        return None
    repeat = parsed["repeat"]
    t = parsed["next_at"]
    if repeat == "interval":
        return (repeat, parsed.get("interval_seconds"))
    if repeat == "once":
        return (repeat, t.date().toordinal(), t.hour, t.minute, t.second)
    if repeat == "weekly":
        return (repeat, parsed.get("weekday"), t.hour, t.minute, t.second)
    if repeat == "monthly":
        return (repeat, parsed.get("dom"), t.hour, t.minute, t.second)
    return (repeat, t.hour, t.minute, t.second)


def _find_duplicates(tasks: list[dict]) -> list[dict]:
    seen: dict[tuple, list[dict]] = {}
    for t in tasks:
        sig = _task_signature(t)
        if sig is None:
            continue
        seen.setdefault(sig, []).append(t)
    return [group for group in seen.values() if len(group) > 1]


def _mock_response(content=None, tool_calls=None) -> dict:
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
        finish = "tool_calls"
    else:
        finish = "stop"
    return {
        "choices": [{"message": msg, "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _tc(call_id: str, args: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "schedule_task",
            "arguments": json.dumps(args, ensure_ascii=False),
        },
    }


async def _run_mock(args, agent_cfg: dict, schedule_spec, tool_executor, module, scheduler) -> None:
    """用 OpenAICompatProvider + 假 _request 复现日志中的多轮工具调用场景。

    不会访问真实网络；工具仍走真实 TaskScheduler / handle_schedule_tool，
    用于验证修复后的 create 去重与 8 位前缀 delete。
    """
    print("=== MOCK（不访问网络，仅验证调度逻辑）===\n")
    provider = OpenAICompatProvider({"api_key": "mock"})
    request_seq = 0

    async def fake_request(payload, timeout):
        nonlocal request_seq
        idx = request_seq
        request_seq += 1
        if idx == 0:
            return _mock_response("这么晚还睡不着？去喝杯温牛奶，别硬撑。")
        if idx == 1:
            return _mock_response("嗯，我在。说吧，是睡不着，还是……单纯想找人说说话？")
        if idx == 2:
            return _mock_response(None, [
                _tc("call_a", {"action": "create", "trigger": "每天早上6点", "note": "老师，该吃药了，记得按时。"}),
            ])
        if idx == 3:
            return _mock_response("行了，每天早上六点我会提醒你。")
        if idx == 4:
            # 旧问题：模型一次给出两个错误 create
            return _mock_response(None, [
                _tc("call_b", {"action": "create", "trigger": "每天早上8点", "note": "该吃药了老师，身体要紧。"}),
                _tc("call_c", {"action": "create", "trigger": "今晚10点", "note": "到点该睡觉了老师，别再熬夜。"}),
            ])
        if idx == 5:
            # 旧工具结果只给 8 位 id；当前实现对前缀兼容，应能删除
            bad_rows = [t for t in scheduler.status() if t["session_id"] == SESSION_ID]
            bad_rows = [t for t in bad_rows if t["repeat"] != "daily" or "6点" not in t["trigger_expr"]]
            ids = [t["task_id"] for t in bad_rows[:2]]
            return _mock_response(None, [
                _tc("call_d", {"action": "delete", "job_id": ids[0][:8]}),
                _tc("call_e", {"action": "delete", "job_id": ids[1][:8]}),
            ])
        if idx == 6:
            # 模型再次尝试创建 6 点和 10 点日常任务：6 点应被去重，10 点应正常创建
            return _mock_response(None, [
                _tc("call_f", {"action": "create", "trigger": "每天早上6点", "note": "该吃药了老师，药别忘吃。"}),
                _tc("call_g", {"action": "create", "trigger": "每天晚上10点", "note": "晚上十点到了，老师该睡觉了。"}),
            ])
        if idx == 7:
            return _mock_response("好了，都安排上了。")
        raise AssertionError(f"mock request_seq 溢出: {idx}")

    provider._request = fake_request

    history: list[dict] = []
    all_reports: list[dict] = []
    for idx, user_text in enumerate(CONVERSATION, 1):
        messages = build_messages(
            system_prompt=agent_cfg.get("system_prompt") or "你是一个友好的助手。",
            history=history,
            user_text=user_text,
            with_schedule_instruction=True,
            message_meta_instruction=LEGACY_MESSAGE_META_INSTRUCTION,
        )
        t0 = time.monotonic()
        resp = await provider.chat(
            messages,
            model="mock-model",
            temperature=0.7,
            max_tokens=1024,
            tools=[schedule_spec.to_openai()],
            tool_executor=tool_executor,
            max_tool_rounds=5,
        )
        answer = resp.text or ""
        latency = round(time.monotonic() - t0, 2)

        print(f"--- Turn {idx} ---")
        print(f"用户: {user_text}")
        print(f"模型: {answer[:300]}")
        for tr in (resp.tool_results or []):
            print(f"工具: {tr['name']}({json.dumps(tr['args'], ensure_ascii=False)}) -> {tr['result'][:120]}")
        print(f"耗时: {latency}s\n")

        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": answer})
        all_reports.append({
            "turn": idx,
            "user": user_text,
            "assistant": answer,
            "tool_results": resp.tool_results or [],
            "tasks_after": scheduler.status(),
            "latency_s": latency,
        })

    tasks = scheduler.status()
    duplicates = _find_duplicates(tasks)
    print("=== 最终任务 ===")
    for t in tasks:
        print(json.dumps(t, ensure_ascii=False))
    if duplicates:
        print("\n!!! 检测到重复任务 !!!")
        for group in duplicates:
            print("重复组:", [t["task_id"] for t in group],
                  [t["trigger_expr"] for t in group], [t["repeat"] for t in group])
    else:
        print("\nOK: 未检测到重复任务")

    report = {
        "target": {"mode": "mock", "model": "mock-model"},
        "bot_id": BOT_ID,
        "session_id": SESSION_ID,
        "conversation": all_reports,
        "final_tasks": tasks,
        "duplicates": duplicates,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"schedule_task_conversation_mock_{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入: {out}")


# --------------------------------------------------------------------------- #
#  配置解析
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


async def _init():
    settings = Settings()
    db = Database(settings.db_path)
    await db.connect()
    cfg_service = ConfigService(db, settings.project_root)
    await cfg_service.init()
    runtime = ProviderRuntimeManager(cfg_service)
    return cfg_service, runtime


# --------------------------------------------------------------------------- #
#  主流程
# --------------------------------------------------------------------------- #
async def main(args) -> None:
    if args.dry_run:
        print("=== DRY-RUN（不调用 API）===")
        print(f"bot_id={BOT_ID} session_id={SESSION_ID}")
        for i, text in enumerate(CONVERSATION, 1):
            print(f"\n[{i}] 用户: {text}")
        print("\n将使用真实 Provider API 逐轮请求，schedule_task 工具绑定真实 TaskScheduler。")
        return

    if args.mock:
        cfg_service, _ = await _init()
        agent_cfg = resolve_agent_config(cfg_service)
        module = _FakeModule()
        scheduler = TaskScheduler(module, data_dir=tempfile.mkdtemp(prefix="sched-conv-mock-"))
        module.scheduler = scheduler
        schedule_spec = build_schedule_tool(module, SESSION_ID, is_private=True)
        tool_executor = make_executor([schedule_spec])
        try:
            await _run_mock(args, agent_cfg, schedule_spec, tool_executor, module, scheduler)
        finally:
            scheduler.stop()
        return

    cfg_service, runtime = await _init()
    agent_cfg = resolve_agent_config(cfg_service)
    system_prompt = agent_cfg.get("system_prompt") or "你是一个友好的助手。"
    temperature = float(agent_cfg.get("temperature", 0.7))
    max_tokens = int(agent_cfg.get("max_tokens", 1024))

    target = await resolve_target(args, cfg_service, runtime)
    key = (target.get("api_key") or target.get("key") or "").strip()
    print(f"Provider: base={target.get('api_base')} key={mask_key(key)} model={target.get('model')}")
    print(f"session={SESSION_ID} bot={BOT_ID}\n")

    # 真实 TaskScheduler，但数据写到临时目录，避免污染线上任务数据
    module = _FakeModule()
    scheduler = TaskScheduler(module, data_dir=tempfile.mkdtemp(prefix="sched-conv-"))
    module.scheduler = scheduler
    schedule_spec = build_schedule_tool(module, SESSION_ID, is_private=True)
    tool_executor = make_executor([schedule_spec])

    history: list[dict] = []
    all_reports: list[dict] = []

    try:
        for idx, user_text in enumerate(CONVERSATION, 1):
            messages = build_messages(
                system_prompt=system_prompt,
                history=history,
                user_text=user_text,
                with_schedule_instruction=True,
                message_meta_instruction=LEGACY_MESSAGE_META_INSTRUCTION,
            )
            t0 = time.monotonic()
            try:
                resp = await get_provider(target).chat(
                    messages,
                    model=target.get("model"),
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=args.timeout,
                    tools=[schedule_spec.to_openai()],
                    tool_executor=tool_executor,
                    max_tool_rounds=5,
                )
                answer = resp.text or ""
            except Exception as e:  # noqa: BLE001
                answer = f"[error] {e}"
            latency = round(time.monotonic() - t0, 2)

            print(f"--- Turn {idx} ---")
            print(f"用户: {user_text}")
            print(f"模型: {answer[:300]}")
            tool_rows = list(getattr(resp, "tool_results", []) or [])
            for tr in tool_rows:
                print(f"工具: {tr['name']}({json.dumps(tr['args'], ensure_ascii=False)}) -> {tr['result'][:120]}")
            print(f"耗时: {latency}s\n")

            # 与真实 Agent 一样，只会把最终 assistant 文本写入长期历史；工具消息不持久化
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": answer})

            all_reports.append({
                "turn": idx,
                "user": user_text,
                "assistant": answer,
                "tool_results": tool_rows,
                "tasks_after": scheduler.status(),
                "latency_s": latency,
            })

        tasks = scheduler.status()
        duplicates = _find_duplicates(tasks)
        print("=== 最终任务 ===")
        for t in tasks:
            print(json.dumps(t, ensure_ascii=False))
        if duplicates:
            print("\n!!! 检测到重复任务 !!!")
            for group in duplicates:
                print("重复组:", [t["task_id"] for t in group],
                      [t["trigger_expr"] for t in group], [t["repeat"] for t in group])
        else:
            print("\nOK: 未检测到重复任务")

        report = {
            "target": {"api_base": target.get("api_base"), "model": target.get("model")},
            "bot_id": BOT_ID,
            "session_id": SESSION_ID,
            "conversation": all_reports,
            "final_tasks": tasks,
            "duplicates": duplicates,
        }
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"schedule_task_conversation_{time.strftime('%Y%m%d-%H%M%S')}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入: {out}")
    finally:
        scheduler.stop()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="定时任务对话回归测试（真实 API）")
    p.add_argument("--preset", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--out-dir", default=str(ROOT / "logs" / "schedule_test"))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--mock", action="store_true", help="不访问网络，用假 _request 复现日志中的工具调用")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
