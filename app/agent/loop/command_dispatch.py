"""`#llm` 指令分发：把「用户敲了一条指令」路由到对应处理器并回话。

## 本模块只负责

`handle_commands`（解析 `#llm <action>` 并执行：task / list / switch / new / load /
export / exit / stop / schedule / proactive / memory …）、`_send_help`（合并转发形式
的能力清单）、`_availability`（各分组是否可用）、`_handle_command_event`（指令事件入口）。

## 为什么单独成文件

指令是一套**自成体系的交互面**：它有自己的语法、帮助文案、可用性判断，却和
「这一轮 LLM 该说什么」共用一个文件。搬出来之后，改一条指令不用在 1,600 行的主循环里
翻找；也让 `chat.py` 只剩真正的对话主流程。

返回 `bool` 的语义保持不变：`True` = 这条消息已被指令消费，不再走 LLM。
"""

from __future__ import annotations

from app.core.logger import account_label

import time
from typing import Any

from app.agent import logger
from app.agent.commands import (
    build_help_nodes,
    build_help_text,
    chunk_text,
    is_help_action,
    parse_action,
)


async def handle_commands(module, session_mgr, session_id, group_id, user_id,
                          raw_text, is_admin, is_private, event=None) -> bool:
    cmd = raw_text.strip()
    action = parse_action(cmd)
    if action is None:
        return False

    history_mgr = session_mgr.history
    bot = event.bot

    async def send(msg):
        text = f"# {msg}"
        if is_private:
            await bot.send_private_msg(user_id=int(user_id), message=text)
        else:
            await bot.send_group_msg(group_id=int(group_id), message=text)

    if is_help_action(action):
        await _send_help(
            module, event, bot, is_private=is_private,
            group_id=group_id, user_id=user_id, send=send,
        )
        return True

    # 本群开关 / 静音 / 状态：与 WebUI「权限与回复」写同一份 channel_overrides
    if await _maybe_handle_policy_command(
        module, action,
        is_private=is_private, group_id=group_id,
        event=event, send=send, is_admin=is_admin,
    ):
        return True

    session = session_mgr.get_session(session_id)

    if action == "task":
        if session:
            await send(f"当前任务ID: {session.task_id}（对话: {session.active.title if session.active else '?'}）")
        else:
            await send("当前没有活跃会话")
        return True

    elif action == "list":
        if not session:
            await send("当前没有活跃会话")
            return True
        convs = session.list_conversations()
        if not convs:
            await send("当前会话暂无对话记录")
            return True
        lines = [f"当前会话 {len(convs)} 个对话（#llm switch <id> 切换）:"]
        for c in convs[:10]:
            mark = " *" if c["id"] == session.active_id else ""
            lines.append(f"  {c['id'][:8]} | {c['title']} | {c['count']}条{mark}")
        await send("\n".join(lines))
        return True

    elif action == "switch" or action.startswith("switch "):
        target = action[len("switch"):].strip()
        if not target:
            await send("用法：#llm switch <conv_id|task_id>（可用 #llm list 查看）")
            return True
        if not session:
            await send("当前没有活跃会话")
            return True
        # 支持按 conv_id 或 task_id 切换
        hit = None
        for c in session.conversations.values():
            if c.id == target or c.task_id == target:
                hit = c
                break
        if hit and session.switch_conversation(hit.id):
            session_mgr.history.save_session(session)
            await send(f"已切换到对话「{hit.title}」({len(hit.data.history)} 条)")
        else:
            await send(f"未找到对话: {target}")
        return True

    elif action == "new" or action.startswith("new "):
        title = action[4:].strip()
        if session:
            session_mgr.history.save_session(session)
        created = session_mgr.new_conversation(session_id, title)
        if created:
            memory = getattr(module, "memory", None)
            if memory is not None:
                try:
                    memory.on_session_reset(session_id, user_id)
                except Exception as e:
                    logger.add_info(account_label(getattr(module, 'bot_id', '?'))).debug(
                        f"[Memory] 会话重置钩子失败（已忽略）: {e}"
                    )
            await send(f"已开启新对话「{created['title']}」(task: {created['task_id']})")
        else:
            await send("创建失败")
        return True

    elif action == "load" or action.startswith("load "):
        load_task_id = action[len("load"):].strip()
        if not load_task_id:
            await send("用法：#llm load <task_id>")
            return True
        data = history_mgr.load_history(load_task_id)
        if not data:
            await send(f"未找到任务: {load_task_id}")
            return True
        session = session or session_mgr.create_session(session_id, "private" if is_private else "group", 60)
        # 新建会话自带 1 个空对话 → 复用而非再建，避免残留空对话
        fresh = len(session.conversations) == 1 and not any(c.data.history for c in session.conversations.values())
        if fresh:
            conv = next(iter(session.conversations.values()))
            session.active_id = conv.id
        else:
            conv = session.new_conversation(title=data.get("title", "导入"))
        conv.title = data.get("title", "导入")
        conv.task_id = data.get("task_id", conv.task_id)
        conv.data.history = data.get("messages", []) or []
        session.touch()
        history_mgr.save_session(session)
        await send(f"已加载历史: {load_task_id} -> 新对话「{conv.title}」({len(conv.data.history)} 条)")
        return True

    elif action == "export" or action.startswith("export "):
        sub = action[len("export"):].strip()
        export_task_id = sub if sub and " " not in sub else (session.task_id if session else "")
        if not export_task_id:
            await send("当前没有活跃会话可导出，请指定任务ID: #llm export <task_id>")
            return True
        text = history_mgr.export_text(export_task_id)
        if text:
            lines = text.split("\n")
            for i in range(0, len(lines), 10):
                await send(f"导出 ({export_task_id}):\n{chr(10).join(lines[i:i+10])}")
        else:
            await send(f"未找到任务: {export_task_id}")
        return True

    elif action == "proactive" or action.startswith("proactive "):
        pm = getattr(module, "proactive", None)
        if pm is None:
            await send("本模块未启用主动消息")
            return True
        target = action[len("proactive "):].strip() if action.startswith("proactive ") else ""
        if target:
            ok = await pm.manual_trigger(target)
            await send(f"已触发 {target} 主动发言" if ok else f"会话 {target} 未启用或不在主动列表")
        else:
            rows = pm.status()
            if not rows:
                await send("暂无已配置的主动会话")
                return True
            lines = ["主动消息状态:"]
            for s in rows:
                mark = "🟢" if s["enabled"] else "⚪"
                next_s = f"，下次 {time.strftime('%m-%d %H:%M', time.localtime(s['next_trigger_time']))}" if s["next_trigger_time"] else ""
                lines.append(f"  {mark} {s['session_id']} | 未回复{s['unanswered']} | {s['timer'] or '未计时'}{next_s}")
            await send("\n".join(lines))
        return True

    elif action == "schedule" or action.startswith("schedule "):
        scheduler = getattr(module, "scheduler", None)
        if scheduler is None:
            await send("本模块未启用定时任务")
            return True
        sub = action[len("schedule "):].strip() if action.startswith("schedule ") else ""
        if sub.startswith("cancel "):
            tid = sub[len("cancel "):].strip()
            ok = scheduler.cancel(tid)
            await send(f"已取消定时任务 {tid}" if ok else f"未找到定时任务 {tid}")
            return True
        rows = scheduler.status()
        if not rows:
            await send("暂无定时任务（可在对话中直接提出定时请求，或用 WebUI 的「定时任务」页面添加）")
            return True
        lines = ["定时任务:"]
        for r in rows:
            next_s = time.strftime("%m-%d %H:%M", time.localtime(r["next_trigger_time"]))
            lines.append(
                f"  {r['task_id'][:8]} | {r['session_id']} | {r['repeat']} | "
                f"下次 {next_s} | {r['content'][:20]}"
            )
        lines.append("#llm schedule cancel <id> 取消；页面也可管理")
        await send("\n".join(lines))
        return True

    elif action.startswith("memory"):
        # v3：长期记忆是 `llm_memory` 模块的业务，指令由它自己用 @command 声明。
        # 走到这里说明该模块未安装或被禁用。
        await send("长期记忆未启用（安装并启用「长期记忆」模块后可用）")
        return True

    elif action == "exit":
        # 会话重置通知走钩子（记忆模块自己订阅），框架不再直呼业务对象
        hooks = getattr(module, "llm_hooks", None)
        if hooks is not None:
            from app.agent.hooks import run_stage

            try:
                await run_stage(module, "session_reset", None)
            except Exception as e:  # noqa: BLE001 —— 重置通知失败不该阻断退出
                logger.add_info(account_label(getattr(module, 'bot_id', '?'))).debug(
                    f"[LLM] 会话重置通知失败（已忽略）: {e}"
                )
        if session:
            session_mgr.add_message(session_id, "assistant", "#llm exit")
            history_mgr.save_session(session)
        session_mgr.destroy_session(session_id)
        await send("已退出会话")
        return True

    elif action == "stop":
        if not is_admin:
            await send("权限不足，无法执行此命令")
            return True
        memory = getattr(module, "memory", None)
        if memory is not None:
            try:
                memory.on_session_reset(session_id, user_id)
            except Exception as e:
                logger.add_info(account_label(getattr(module, 'bot_id', '?'))).debug(
                    f"[Memory] 会话重置钩子失败（已忽略）: {e}"
                )
        if session:
            session_mgr.add_message(session_id, "assistant", "#llm stop")
            history_mgr.save_session(session)
        session_mgr.destroy_session(session_id)
        await send("会话已强制结束")
        return True

    # 未知动作：明确回一句（此前静默返回，用户会以为机器人坏了）
    await send(f"未知指令：{action}\n发送 #llm help 查看全部指令")
    return True


async def _send_help(module, event, bot, *, is_private, group_id, user_id, send) -> None:
    """发送 `#llm help`：优先合并转发，失败降级为分段纯文本。

    合并转发把"说明 + 每个分组"拆成独立节点，长指令表在 QQ 里可展开阅读，
    不会像单条长文本那样被折叠成"[合并转发]"或被截断。
    """
    log = logger.add_info(account_label(getattr(module, 'bot_id', '?')))
    available = _availability(module)
    self_id = getattr(event, "self_id", None) or getattr(module, "bot_id", None)
    nodes = build_help_nodes(uin=self_id, available=available)

    resp: Any = None
    try:
        if is_private:
            resp = await bot.send_forward_msg(user_id=int(user_id), msgdata=nodes)
        else:
            resp = await bot.send_forward_msg(group_id=int(group_id), msgdata=nodes)
        if isinstance(resp, dict) and resp.get("status") == "ok":
            return
        reason = resp.get("message") if isinstance(resp, dict) else resp
        log.debug(f"[#llm help] 合并转发未成功（{reason}），降级为纯文本")
    except Exception as e:  # noqa: BLE001 —— 合并转发不被支持时必须降级而不是静默失败
        log.debug(f"[#llm help] 合并转发异常（{e}），降级为纯文本")

    for chunk in chunk_text(build_help_text(available=available)):
        await send(chunk)


def _availability(module) -> dict:
    """当前 bot 实际启用的能力（帮助里标注「本 bot 未启用」，避免照着表试却没反应）。"""
    memory = getattr(module, "memory", None)
    memory_on = False
    if memory is not None:
        try:
            memory_on = bool(memory.enabled())
        except Exception as e:  # noqa: BLE001 —— 帮助渲染不能因为探测失败而中断
            logger.add_info(account_label(getattr(module, 'bot_id', '?'))).debug(
                f"[#llm help] 记忆启用状态探测失败（按未启用展示）: {e}"
            )
    return {
        "schedule": getattr(module, "scheduler", None) is not None,
        "proactive": getattr(module, "proactive", None) is not None,
        "memory": memory_on,
    }




# ==================== 本群开关 / 静音 / 状态（R3） ====================
# 与 WebUI「权限与回复」写同一份 channel_overrides：面板能做的，群里也能做。

_POLICY_ACTIONS = ("off", "on", "mute", "unmute", "status")


def _parse_duration_minutes(token: str) -> int | None:
    """``30m`` / ``2h`` / ``1d`` / ``45`` → 分钟数；非法返回 None。"""
    text = str(token or "").strip().lower()
    if not text:
        return 60
    unit = text[-1]
    if unit in ("m", "h", "d"):
        number_text = text[:-1]
    else:
        unit, number_text = "m", text
    try:
        value = int(number_text)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value * {"m": 1, "h": 60, "d": 1440}[unit]


def _trigger_text(resolved) -> str:
    at = bool(resolved.config.get("trigger_at", False))
    keywords = [str(item) for item in (resolved.config.get("trigger_keyword") or []) if str(item).strip()]
    if at and keywords:
        return "@ 或关键词"
    if at:
        return "只回 @"
    if keywords:
        return "只回关键词：" + "、".join(keywords[:5])
    return "未开启触发（群消息不会引起回复）"


async def _maybe_handle_policy_command(
    module, action, *, is_private, group_id, event, send, is_admin,
) -> bool:
    """处理 ``#llm off/on/mute/unmute/status``；返回 True 表示已消费。"""
    from app.application.dialogue_policy_service import DialoguePolicyService, PolicyError

    parts = str(action or "").split()
    head = parts[0].lower() if parts else ""
    if head not in _POLICY_ACTIONS:
        return False

    # 权限先于"场景"判断：管理员指令在私聊里也必须先回答"权限不足"，
    # 否则"先把人拒掉"的语义会随场景漂移（帮助表守卫测试也依赖这个口径）。
    role = str(getattr(event, "permission_role", "") or "")
    allowed = bool(is_admin) or role in ("owner", "manager")
    if head != "status" and not allowed:
        await send("权限不足：需要管理者或群管理员")
        return True
    if is_private:
        await send("本群开关请在群里发送；私聊开关在「Agent → 基础配置」里")
        return True

    services = getattr(getattr(module, "ctx", None), "services", None)
    config_service = getattr(services, "config_service", None)
    account_id = str(getattr(module, "bot_id", "") or "")
    if config_service is None or not account_id:
        await send("配置服务未就绪，稍后再试")
        return True

    service = DialoguePolicyService(config_service)
    channel = str(group_id or "")

    try:
        if head == "status":
            resolved = service.resolve(account_id, "agent", channel)
            state = "静音中" if resolved.enabled_source == "muted" else ("允许回复" if resolved.enabled else "已关闭")
            await send(f"本群状态：{state}（来源 {resolved.enabled_source}）\n触发：{_trigger_text(resolved)}")
        elif head in ("off", "on"):
            await service.update(account_id, "agent", channel, enabled=(head == "on"))
            await send("本群已开启回复" if head == "on" else "本群已关闭回复（#llm on 恢复）")
        elif head == "mute":
            minutes = _parse_duration_minutes(parts[1] if len(parts) > 1 else "")
            if minutes is None:
                await send("用法：#llm mute 30m（也支持 2h / 1d）")
                return True
            await service.set_mute(account_id, "agent", channel, minutes)
            await send(f"已静音 {minutes} 分钟，到点自动恢复")
        else:  # unmute
            await service.set_mute(account_id, "agent", channel, 0)
            await send("已取消静音")
    except PolicyError as e:
        await send(str(e))
    return True
