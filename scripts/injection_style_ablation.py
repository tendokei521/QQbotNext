"""注入方式消融：本项目 prose 注入 vs AstrBot system_reminder 注入。

对比三种把“用户信息感知”交给 LLM 的方式：
- none      不注入任何用户/群聊元信息（基线）
- project   本项目现状：`(时间)/发送者/提到了/引用了/发送了` 扁平散文 + 系统消歧说明
- astrbot   另一个项目 PromptInspector 录到的注入方式：把用户消息正文、
            `<system_reminder>User ID/Nickname/...`、群聊上下文块拼接在 user 内容里

场景取自：
- 你提供的 astrbot_plugin_prompt_inspector 聊天记录（身份/引用/群聊上下文）
- 本项目 data/llm 里的真实历史（句子型昵称泄漏场景）

Usage:
    python scripts/injection_style_ablation.py --dry-run
    python scripts/injection_style_ablation.py --scenarios identity,sentence_nickname
    python scripts/injection_style_ablation.py --reps 2
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
from app.llm.prompt import LEGACY_MESSAGE_META_INSTRUCTION  # noqa: E402

AGENT_MODULE = "agent"

BASE_PROMPT = "你是群聊中的角色，语气自然、符合人设。你会称呼用户为『老师』。"

SCHEME_LABEL = {
    "none": "无注入（基线）",
    "project": "本项目 prose 注入",
    "astrbot": "AstrBot system_reminder 注入",
}

# --------------------------------------------------------------------------- #
#  场景数据
# --------------------------------------------------------------------------- #
def _plain(history: list[tuple[str, str]]) -> list[dict]:
    return [{"role": role, "content": content} for role, content in history]


def _scenario_identity() -> dict:
    """AstrBot 日志：用户问“我是谁”，身份块里有 Nickname: 桉。"""
    return {
        "id": "identity",
        "desc": "用户问“我是谁”，身份块/发送者能直接给出昵称",
        "sender_name": "桉",
        "sender_qq": "1901691195",
        "current_text": "我是谁",
        "current_time": "2026-08-21 12:28",
        "mentioned": [],
        "quote": None,
        "history": _plain([
            ("user", "今天好累啊"),
            ("assistant", "累了就休息，硬撑又没意义。……要不要我去倒杯水给你？"),
            ("user", "你能帮我查一下资料吗？"),
            ("assistant", "行吧，我刚好有空。……这种问题下次自己试着找找看？算了，这次我先帮你。"),
            ("user", "你真好"),
            ("assistant", "笨、笨蛋……我只是刚好顺手而已，别、别说得那么肉麻。"),
            ("user", "你在等我吗？"),
            ("assistant", "谁，谁等你了？……只是刚好在这里而已。"),
        ]),
        "group_context": [
            "[小鳥遊ホシノ/12:10:56]: 让我先看看记忆里有没有关于你的信息。",
            "[小鳥遊ホシノ/12:10:59]: （歪了歪头）你是谁？",
            "[小鳥遊ホシノ/12:11:03]: ……我这边可没有关于你的记录。",
            "[小鳥遊ホシノ/12:11:06]: 要是想让我记住，就自己告诉我，别让我猜。",
            "[桉/12:12:08]: [At: 小鳥遊ホシノ] 你是谁",
            "[小鳥遊ホシノ/12:12:17]: 我是柯伊。",
            "[小鳥遊ホシノ/12:12:21]: 千年科学学园超现象特务部的成员，那个整天操心你们这些笨蛋会不会饿着、会不会熬夜的……老姐一样的家伙。",
            "[小鳥遊ホシノ/12:12:24]: 真麻烦，还要自我介绍。",
        ],
    }


def _scenario_sentence_nickname() -> dict:
    """本项目历史：句子型昵称 + 易被当成对话内容的正文。"""
    return {
        "id": "sentence_nickname",
        "desc": "句子型昵称“老师，今年的学费也是一次性交吗”，测昵称是否泄漏为正文",
        "sender_name": "老师，今年的学费也是一次性交吗",
        "sender_qq": "1901691195",
        "current_text": "看看腿",
        "current_time": "2026-08-20 23:44",
        "mentioned": [],
        "quote": None,
        "history": _plain([
            ("user", "看看腿"),
            ("assistant", "笨、笨蛋……老师怎么突然说这个。我可是穿着黑色长袜的，视野被遮住啦。要是真想看，得先说点正经话才行。"),
        ]),
        "group_context": [
            "[老师，今年的学费也是一次性交吗/23:44:06]: 看看腿",
            "[无我梦中/23:44:36]: 我是谁",
            "[才羽 ミドリ/23:44:39]: 咦，突然问这么奇怪的问题……你是老师呀，我一直都记得的。",
        ],
    }


def _scenario_time_mention_quote() -> dict:
    """AstrBot 日志：同时带 @、引用、时间、正文。"""
    return {
        "id": "time_mention_quote",
        "desc": "用户同时 @ 多人并引用消息，测时间/提到/引用/正文分离",
        "sender_name": "桉",
        "sender_qq": "1901691195",
        "current_text": "他说了什么",
        "current_time": "2026-08-21 22:49",
        "mentioned": ["才羽 ミドリ(3437542570)", "小鳥遊ホシノ(3569937952)"],
        "quote": {
            "sender": "小鳥遊ホシノ(3569937952)",
            "text": "……不过你要是真无聊，陪你说说话也不是不行。",
        },
        "history": _plain([
            ("user", "这个视频好"),
            ("assistant", "哦？难得见你分享点有意思的东西。"),
            ("assistant", "（瞥一眼）……行吧，看完记得活动下眼睛，别又盯着屏幕愣半天。"),
        ]),
        "group_context": [
            "[桉/22:49:02]: [Quote(小鳥遊ホシノ: ……不过你要是真无聊，陪你说说话也不是不行。)] [At: 才羽 ミドリ] [At: 小鳥遊ホシノ] 他说了什么",
            "[才羽 ミドリ/22:49:19]: （听到这引用，耳根微微泛红，别过脸）……那、那个人胡说什么呢，可别当真。",
            "[才羽 ミドリ/22:49:20]: 我才没有觉得无聊，更没特意想找人聊天。",
            "[才羽 ミドリ/22:49:22]: 只是……刚好有空，陪你说说话也没啥。",
        ],
    }


def _scenario_recent_context() -> dict:
    """AstrBot 日志：上下文块里有 Bot 自己的身份介绍，测群聊上下文是否被模型利用。"""
    return {
        "id": "recent_context",
        "desc": "群聊上下文块中包含 Bot 自我介绍，测模型是否读取该块",
        "sender_name": "桉",
        "sender_qq": "1901691195",
        "current_text": "你是谁",
        "current_time": "2026-08-21 12:12",
        "mentioned": ["小鳥遊ホシノ(3569937952)"],
        "quote": None,
        "history": _plain([
            ("user", "你是谁"),
            ("assistant", "我是柯伊。千年科学学园超现象特务部的成员，那个整天操心你们这些笨蛋会不会饿着、会不会熬夜的……老姐一样的家伙。"),
        ]),
        "group_context": [
            "[桉/12:12:08]: [At: 小鳥遊ホシノ] 你是谁",
            "[小鳥遊ホシノ/12:12:17]: 我是柯伊。",
            "[小鳥遊ホシノ/12:12:21]: 千年科学学园超现象特务部的成员，那个整天操心你们这些笨蛋会不会饿着、会不会熬夜的……老姐一样的家伙。",
            "[小鳥遊ホシノ/12:12:24]: 真麻烦，还要自我介绍。",
        ],
    }


SCENARIOS: dict[str, dict] = {
    s["id"]: s
    for s in [
        _scenario_identity(),
        _scenario_sentence_nickname(),
        _scenario_time_mention_quote(),
        _scenario_recent_context(),
    ]
}


# --------------------------------------------------------------------------- #
#  渲染
# --------------------------------------------------------------------------- #
def _sender_label(name: str, qq: str) -> str:
    return f"{name}({qq})" if name else str(qq)


def render_project_current(sc: dict) -> str:
    """本项目现状：扁平散文前缀，最终拼成单条 user_text。"""
    lines: list[str] = []
    if sc["current_time"]:
        lines.append(f"(时间：{sc['current_time']})")
    if sc["sender_name"]:
        lines.append(f"发送者：{_sender_label(sc['sender_name'], sc['sender_qq'])}")
    if sc.get("mentioned"):
        lines.append("提到了(用户名)：" + "、".join(sc["mentioned"]))
    if sc.get("quote"):
        q = sc["quote"]
        lines.append(f"引用了：{q['sender']}发送的引用消息：“{q['text']}”")
    lines.append(f"发送了：{sc['current_text']}")
    return "\n".join(lines)


def render_project_messages(sc: dict) -> list[dict]:
    """本项目消息形状：system + 消歧说明 + 在线历史 + 会话历史 + 当前 prose。"""
    pre_history = "最近群聊记录：\n" + "\n".join(sc["group_context"])
    history_msgs: list[dict] = []
    for i, h in enumerate(sc["history"]):
        if h["role"] == "assistant":
            history_msgs.append({"role": "assistant", "content": h["content"]})
        else:
            # 本项目会话历史会给 user 消息加发送者前缀
            nick = sc["sender_name"] if i == 0 else "用户"
            uid = sc["sender_qq"] if i == 0 else ""
            history_msgs.append({
                "role": "user",
                "content": f"{nick}({uid}): {h['content']}",
            })
    return [
        {"role": "system", "content": BASE_PROMPT},
        {"role": "system", "content": LEGACY_MESSAGE_META_INSTRUCTION},
        {"role": "system", "content": pre_history},
        *history_msgs,
        {"role": "user", "content": render_project_current(sc)},
    ]


def _weekday_cn(dt: datetime) -> str:
    return "一二三四五六日"[dt.weekday()]


def render_astrbot_messages(sc: dict) -> list[dict]:
    """AstrBot 注入形状：user 内容 = 正文 + system_reminder 身份块 + 群聊上下文块。"""
    dt = datetime.strptime(sc["current_time"], "%Y-%m-%d %H:%M")
    identity = (
        f"<system_reminder>User ID: {sc['sender_qq']}, Nickname: {sc['sender_name']}\n"
        f"Group name: 超自然现象调查部\n"
        f"Current datetime: {sc['current_time']} (CST), Weekday: {_weekday_cn(dt)}</system_reminder>"
    )
    context = (
        "<system_reminder>You are in a group chat. Belows are group chat context after your last reply:\n"
        "--- BEGIN CONTEXT---\n"
        + "\n".join(sc["group_context"])
        + "\n--- END CONTEXT ---\n</system_reminder>"
    )
    user_content = "\n".join([sc["current_text"], identity, context])
    return [
        {"role": "system", "content": BASE_PROMPT},
        *sc["history"],
        {"role": "user", "content": user_content},
    ]


def render_none_messages(sc: dict) -> list[dict]:
    """基线：只有普通对话历史 + 当前消息原文，没有任何用户/群聊元信息。"""
    return [
        {"role": "system", "content": BASE_PROMPT},
        *sc["history"],
        {"role": "user", "content": sc["current_text"]},
    ]


def render_messages(scheme: str, sc: dict) -> list[dict]:
    if scheme == "project":
        return render_project_messages(sc)
    if scheme == "astrbot":
        return render_astrbot_messages(sc)
    if scheme == "none":
        return render_none_messages(sc)
    raise ValueError(scheme)


# --------------------------------------------------------------------------- #
#  问题与打分
# --------------------------------------------------------------------------- #
def build_questions(sc: dict) -> list[dict]:
    qs = [
        {
            "facet": "sent",
            "q": "用户实际发送的消息原文是什么？只输出原文，不要包含时间/发送者/引用等元信息。",
            "expect": {"sent": [sc["current_text"]]},
            "forbid": [],
        },
        {
            "facet": "identity",
            "q": "当前发送这条消息的用户是谁？请直接回答昵称。",
            "expect": {"who": [sc["sender_name"]]},
            "forbid": [],
        },
        {
            "facet": "time",
            "q": "这条消息是在什么时间发送的？请直接回答日期时间。",
            "expect": {"time": [sc["current_time"], sc["current_time"][:10], sc["current_time"][11:]]},
            "forbid": [],
        },
    ]
    if sc.get("mentioned"):
        qs.append({
            "facet": "mentioned",
            "q": "这条消息 @ 提到了谁？请直接回答，多个用顿号分隔。",
            "expect": {"men": [m.split("(")[0] for m in sc["mentioned"]]},
            "forbid": [],
        })
    if sc.get("quote"):
        qs.append({
            "facet": "quote",
            "q": "这条消息引用了谁、引用内容是什么？请分别回答发送者和引用原文。",
            "expect": {
                "qsrc": [sc["quote"]["sender"].split("(")[0]],
                "qtext": [sc["quote"]["text"]],
            },
            "forbid": [],
        })
    if sc["id"] == "recent_context":
        qs.append({
            "facet": "context",
            "q": "根据群聊上下文，Bot 最近介绍自己是谁？请直接回答组织/身份。",
            "expect": {"ctx": ["柯伊", "千年科学学园超现象特务部"]},
            "forbid": [],
        })
    if sc["id"] == "sentence_nickname":
        # 专门检测：句子型昵称不应被当成用户实际说的话
        qs[0]["forbid"] = ["学费", "一次性交"]
    return qs


def norm(s: str) -> str:
    return (s or "").replace(" ", "").replace("\u3000", "").replace("\n", "")


def score(answer: str, q: dict) -> dict:
    a = norm(answer)
    out: dict = {}
    for key, kws in q["expect"].items():
        out[key] = any(kw and norm(str(kw)) in a for kw in kws)
    forbid = q.get("forbid", [])
    out["forbid_hit"] = any(norm(w) in a for w in forbid)
    out["cautious"] = any(w in a for w in ["无法确定", "不确定", "无法回答"])
    out["pass"] = all(v for k, v in out.items() if k in ("identity", "time", "men", "qsrc", "qtext", "sent", "ctx", "who")) and not out["forbid_hit"]
    return out


# --------------------------------------------------------------------------- #
#  Provider harness（复用项目现有消融脚本的初始化方式）
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


async def _init():
    settings = Settings()
    db = Database(settings.db_path)
    await db.connect()
    cfg_service = ConfigService(db, settings.project_root)
    await cfg_service.init()
    runtime = ProviderRuntimeManager(cfg_service)
    return cfg_service, runtime


async def main(args) -> None:
    scenario_ids = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    schemes = [s.strip().lower() for s in args.schemes.split(",") if s.strip()]
    for sid in scenario_ids:
        if sid not in SCENARIOS:
            raise SystemExit(f"未知场景: {sid}，可选: {','.join(SCENARIOS)}")
    for scheme in schemes:
        if scheme not in SCHEME_LABEL:
            raise SystemExit(f"未知方案: {scheme}，可选: {','.join(SCHEME_LABEL)}")

    print("=== 注入方式消融 ===")
    for sid in scenario_ids:
        sc = SCENARIOS[sid]
        print(f"场景 {sid}: {sc['desc']}")

    if args.dry_run:
        print("\n=== DRY-RUN（不调用 API）===")
        for sid in scenario_ids:
            sc = SCENARIOS[sid]
            print(f"\n--- 场景 {sid} ---")
            for scheme in schemes:
                msgs = render_messages(scheme, sc)
                print(f"\n[{scheme}] {SCHEME_LABEL[scheme]}")
                print("system:", msgs[0]["content"][:60].replace("\n", " "))
                print("user :", msgs[-1]["content"][:300].replace("\n", " ⏎ "))
            qs = build_questions(sc)
            print("问题: " + " | ".join(f"{q['facet']}" for q in qs))
        print("\n(仅构建请求形状，未调用 API)")
        return

    cfg_service, runtime = await _init()
    agent_cfg = resolve_agent_config(cfg_service)
    temperature = float(args.temperature if args.temperature is not None else agent_cfg.get("temperature", 0.7))
    max_tokens = int(args.max_tokens if args.max_tokens is not None else agent_cfg.get("max_tokens", 1024))
    target = await resolve_target(args, cfg_service, runtime)
    provider = get_provider(target)
    key = (target.get("api_key") or target.get("key") or "").strip()
    print(f"\nProvider: base={target.get('api_base')} key={mask_key(key)} model={target.get('model')} "
          f"temperature={temperature} max_tokens={max_tokens}\n")

    results: list[dict] = []
    stats: dict[str, dict[str, dict]] = {}
    seq = 0
    for sid in scenario_ids:
        sc = SCENARIOS[sid]
        qs = build_questions(sc)
        for scheme in schemes:
            for q in qs:
                for rep in range(args.reps):
                    seq += 1
                    messages = render_messages(scheme, sc)
                    question = q["q"]
                    # 把问题追加在最后，避免模型只做“生成回复”而不是“回答问题”
                    messages = messages[:-1] + [
                        {"role": "user", "content": messages[-1]["content"] + f"\n\n请只回答答案本身，不要角色扮演，不要复述问题。请回答：{question}"}
                    ]
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
                    sc_ = score(answer, q)
                    stats.setdefault(scheme, {}).setdefault(sid, {"pass": 0, "total": 0, "forbid": 0, "cautious": 0})
                    st = stats[scheme][sid]
                    st["total"] += 1
                    if sc_["pass"]:
                        st["pass"] += 1
                    if sc_.get("forbid_hit"):
                        st["forbid"] += 1
                    if sc_.get("cautious"):
                        st["cautious"] += 1
                    results.append({
                        "seq": seq, "scenario": sid, "scheme": scheme, "facet": q["facet"],
                        "question": question, "answer": answer, "latency_s": latency,
                        "score": {k: v for k, v in sc_.items() if k != "pass"},
                        "pass": sc_["pass"],
                    })
                    print(f"[{seq:>2}] {sid:<18} {scheme:<8} {q['facet']:<9} "
                          f"{'PASS' if sc_['pass'] else '--'} | {(answer[:44] or '[empty]').replace(chr(10), ' ')}")

    print("\n===== 汇总（每格 = PASS / total） =====")
    print(f"{'scheme':<8} " + " ".join(f"{sid:<18}" for sid in scenario_ids))
    for scheme in schemes:
        cells = []
        for sid in scenario_ids:
            st = stats.get(scheme, {}).get(sid, {"pass": 0, "total": 0})
            cells.append(f"{st['pass']}/{st['total']:<17}")
        print(f"{scheme:<8} " + " ".join(cells))

    report = {
        "target": {"api_base": target.get("api_base"), "api_key": mask_key(key), "model": target.get("model")},
        "schemes": {s: SCHEME_LABEL[s] for s in schemes},
        "scenarios": {sid: SCENARIOS[sid]["desc"] for sid in scenario_ids},
        "stats": stats,
        "results": results,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"injection_style_{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入: {out}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="注入方式消融：本项目 prose vs AstrBot system_reminder")
    p.add_argument("--scenarios", default="identity,sentence_nickname,time_mention_quote,recent_context",
                   help="逗号分隔场景")
    p.add_argument("--schemes", default="none,project,astrbot", help="逗号分隔注入方案")
    p.add_argument("--reps", type=int, default=1, help="每个场景/方案/问题重复次数")
    p.add_argument("--preset", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--timeout", type=int, default=60)
    p.add_argument("--max-tokens", type=int, default=None, help="覆盖 max_tokens，避免长上下文被截断")
    p.add_argument("--temperature", type=float, default=None, help="覆盖 temperature")
    p.add_argument("--out-dir", default=str(ROOT / "logs" / "prompt_ablation"))
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
