# 流水线接口审计笔记（工作稿）

> 目的：把「高级功能默认暴露在主页面」这个问题的根因，落到**流水线上每一个该暴露而未暴露的接口点**。
> 判定标准（2026-02 与用户对齐后的口径）：
>
> - **业务逻辑**（主动回复 / 定时问候 / 记忆策略 / 知识库策略）→ 模块自己写，框架**不给**接口，只给原料。
> - **基础接口**（收消息 / 发消息 / 事件分发 / 后台任务 / 定时 / 工具调用 / 状态）→ 必须完备。
> - **高级接口**（可替代的 LLM 非流式/流式请求、可替代的组装、可替代的 provider 调用）→ 必须可替代。
>
> 本文件是过程稿，看到什么记什么；最终结论见文末「汇总」。

---

## 0. 参考基准：AstrBot 暴露给插件的接口面

来源：`1/AstrBot-master/astrbot/core/star/context.py`（`Context`）、
`1/AstrBot-master/astrbot/core/platform/astr_message_event.py`（`AstrMessageEvent`）。

### 0.1 `Context`（框架级，全局单例，插件从 `self.context` 拿）

| 方法 | 作用 | QQbotNext 对应 | 状态 |
|---|---|---|---|
| `llm_generate(chat_provider_id, prompt, image_urls, tools, system_prompt, contexts)` | **单次 LLM 生成，不执行工具** | `self.llm_chat(messages, system=...)` | ✅ 有 |
| `tool_loop_agent(event, chat_provider_id, prompt, tools, contexts, max_steps, tool_call_timeout)` | **带工具循环的完整 agent 运行** | 无（`llm_chat(tools=, tool_executor=)` 要自己造 executor） | ❌ 缺 |
| `send_message(session: str \| MessageSesion, message_chain) -> bool` | **按会话字符串主动发消息** | 无（要 `bot.send_group_msg(int(...))` 自己解析） | ❌ 缺 |
| `add_llm_tools(*tools)` | 运行时增/替工具 | `@tool` 只覆盖"自己注册" | ⚠️ 部分 |
| `get_llm_tool_manager()` | **拿到全量工具表** | 无公开入口 | ❌ 缺 |
| `activate_llm_tool` / `deactivate_llm_tool` | 单工具启停 | `tools_enabled` 只是配置 map | ⚠️ 部分 |
| `get_using_provider(umo)` / `get_provider_by_id` / `get_all_providers` | provider 解析与列举 | `ctx.services.llm.resolve()/models()` | ✅ 有 |
| `register_provider(provider)` | 运行时注册 provider 实例 | `register_provider(name, cls, aliases)` 注册**类** | ⚠️ 语义略不同 |
| `get_config(umo)` | 会话级配置 | `module.config`（模块级）+ `config_profiles` | ⚠️ 部分 |
| `conversation_manager` / `message_history_manager` | 会话与历史 | `SessionManager` 内部类 | ❌ 缺 |
| `persona_manager` | 人格 | `ctx.services.role_presets` | ✅ 有 |
| `kb_manager` | 知识库 | `runtime.knowledge` 内部 | ❌ 缺 |
| `cron_manager`（`add_basic_job`/`add_active_job`/`list_jobs`/`run_job_now`） | **定时任务管理器（可增删查立即跑）** | `ctx.services.scheduler`（`register`/`unload`/`register_module`）+ `SCHEDULES` | ⚠️ 功能更少 |
| `register_web_api(route, handler, methods, desc)` | **插件注册 HTTP 路由** | 无（只有 `pages/index.html` 静态页 + `list`/`dynamic` 数据源） | ❌ 缺 |
| `get_platform_inst(platform_id)` | 平台实例 | `ctx.bot`（仅本账号） | ⚠️ 部分 |
| `get_db()` | 数据库 | `ctx.services.config_service` / 直接 aiosqlite | ⚠️ 部分 |
| `get_all_stars()` / `get_registered_star(name)` | 插件清单 | `get_modules()` | ✅ 有 |

### 0.2 `AstrMessageEvent`（事件级，每个事件一个）

| 方法 | 作用 | QQbotNext 对应 | 状态 |
|---|---|---|---|
| `event.request_llm(prompt, tool_set, image_urls, contexts, system_prompt, conversation)` | **构造一次「交给框架跑」的 LLM 请求**（人格/历史/工具/记录都由框架接管） | 无。`generate_response(runtime, event)` 是内部函数，且不接受"我要改变请求"的入参 | ❌ 缺（**核心缺口**） |
| `event.send(chain)` | 事件级发送 | `event.reply(...)` / `event.bot.send_*` | ✅ 有 |
| `event.send_streaming(...)` | 流式发送 | `stream_response` 内部，非事件 API | ⚠️ 部分 |
| `event.set_extra/get_extra/clear_extra` | **跨插件共享事件级状态** | `event.raw` / `ctx.state`（仅 llm 钩子内可见） | ⚠️ 部分 |
| `event.stop_event()` / `continue_event()` / `is_stopped()` | 链路控制 | `event.stop()` / `event.stopped` | ✅ 有 |
| `event.should_call_llm(bool)` | 跳过 LLM | `event.llm.stop()` | ✅ 有 |
| `event.react(emoji)` | 表情回应 | 走 OneBot API | ⚠️ 无封装 |
| `event.get_group()` | 群资料 | `event.group`（仅本群，无按 id 查） | ⚠️ 部分 |
| `event.track_temporary_local_file(path)` / `cleanup_temporary_local_files()` | **临时文件生命周期托管** | 无 | ❌ 缺 |
| `event.process_buffer(buffer, pattern)` | 流式切句 | `domain/splitter.split_sentences` | ✅ 有 |

### 0.3 AstrBot 的对照结论（第一版判断）

1. **AstrBot 把"发起一次 LLM 请求"做成了事件级 API（`request_llm`）+ 框架级 API（`llm_generate` / `tool_loop_agent`）**，
   而 QQbotNext 的同类能力只存在于 `loop/chat.py` / `loop/generate.py` 的内部函数里 —— 这是"业务模块想复用框架回复能力"时唯一的入口缺失。
2. AstrBot 把**"按 session 发消息"**做成 `Context.send_message`，把**"定时任务"**做成可增删查的 `cron_manager`；
   QQbotNext 只有 `bot.send_*_msg`（要自己解析 session 前缀）和只能"注册/注销"的 `SchedulerService`。
3. AstrBot 允许插件 `register_web_api` —— 模块自带后台页时不必再让框架替它建接口。

---

## 1. 组装层（`app/agent/assembly/`）

### 1.1 已有的「标准输入」—— `PromptRequest`（`assembler.py:71`）

这是全库最接近"标准输入输出"的既有设计，字段分组清晰：

| 组 | 字段 |
|---|---|
| 身份与载体 | `runtime` / `event` / `ctx` / `config` / `session_id` / `is_private` / `user_id` / `group_id` / `kind`(`chat`/`proactive`/`schedule`/`summary`) |
| 文本 | `user_text` / `raw_user_text` / `memory_source_text` |
| 已备好的块原料 | `history` / `pre_history_text` / `referent_text` / `skill_blocks` / `memory_text` / `group_log_text` |
| 能力与模态 | `available_tools` / `modalities` / `schedule_enable` |
| 多模态 | `user_images` / `with_images` / `extra_user_content_parts` |

**判断：输入侧已经标准化了，问题全在"谁来构造它"。** 目前构造者只有两处：
`prompt_prep.prepare_prompt`（有事件）和 `proactivity/{manager,scheduler}`（无事件，各自手搓一遍）。
→ 这就是缺 `run_turn()` 的直接证据：**同一个 dataclass，三份构造代码**。

### 1.2 块表 `BLOCKS`（`assembler.py:478-493`）

12 个命名块，顺序固定：persona → behavior → emoji_hint → schedule → proactive →
message_meta → skills → memory → background → group_log → history → user。

- ✅ 块是**纯函数** `build_xxx(req) -> list[dict]`，只读 req，可单测；
- ✅ `describe(req)` 能给出 `[{block, role, chars}]`，是"这一轮发了什么"的机器可读答案；
- ❌ **块表不可扩展**：`PromptAssembler.__init__(blocks: tuple[Block, ...] = BLOCKS)`
  意味着模块想加一块，只能 `runtime.assembler = PromptAssembler(BLOCKS + (my_block,))`
  —— 需要先拿到 `BLOCKS` 这个内部常量。**没有 `register_block()`**。

> 但注意：这不一定要补。模块要"加内容"用 `post_assemble` 就够（`ctx.messages` 可增删改）。
> 真正补不了的是"**替换某一块但保留其余块**"——目前只能整体接管 `assembly`。

### 1.3 装配器接管（`assembler.py:687` `build_for`）

```python
assembler = getattr(runtime, "assembler", None)
if not callable(getattr(assembler, "build", None)):  # 不合格 → 回退默认 + 留痕
    assembler = PromptAssembler()
return assembler.build(req)
```
✅ 契约清晰：`build(req) -> list[dict]`；不合格对象不会装进去（`controllers.py:230 _usable`）。

### 1.4 `apply_post_assemble`（`assembler.py:736`）—— 最好的既有范式

一个函数同时做了四件事，而且**四条路径统一收口**：
1. 把 messages 挂到 `ctx.messages` 并跑 `post_assemble` 钩子（可增删/重排/整体替换）；
2. 置空或 `job.skip` → 返回 `None` = 本轮不请求 LLM；
3. `apply_request_policy`：token 预算裁剪（唯一一处收口）；
4. `dump.record`：请求落盘对账。

**这是"标准输入 → 接口 → 输出"的成品样板，`run_turn` 应该照它写。**

### 1.5 框架自己也在用钩子（`enhance.py:308 install_framework_hooks`）

```python
registry.register(stage="pre_request", event_type="*", order=-100, handler=collect_user_context)
registry.register(stage="pre_request", event_type="*", order=20,  handler=format_user_context)
registry.register(stage="pre_request", event_type="*", order=25,  handler=interrupt_config_hook)
```

✅ 好消息：框架行为与插件行为走**同一个注册表**，顺序号即契约（order<0 看到原始文本，>25 看到包装后的）。
⚠️ 风险：`unregister_module(module)` 用的是 `h.module is not module`；框架钩子的 `module=None`，
若哪天有代码调 `unregister_module(None)` 会**把框架钩子一起清掉**。

⚠️ 另一个发现：`ctx.user_text` 在 `pre_request` 被 `format_user_context`（order=20）**改写**，
而 `normalize_event`（`turn_input.py:92`）用的是 `ctx.user_text if ctx.user_text else raw`。
→ 模块在 order 0~19 之间改 `ctx.user_text` **会**进入本轮请求；改 `event.text` **不会**（已被取走）。

---

## 2. 请求层（`app/agent/providers/`）

### 2.1 `ProviderInvoker`（`invoker.py`）—— 已有，且契约明确

```python
class ProviderInvoker:
    async def chat(*, runtime, chain, messages, params) -> LLMResponse
    async def stream(*, runtime, chain, messages, params) -> AsyncIterator
```

- `CHAT_PARAMS` / `STREAM_PARAMS` 明确列出了**会被透传的参数名**（含 `budget` / `input_tokens_estimate`
  这类框架内部账本）——这是"接管方要认得哪些键"的书面契约，写得很好；
- `resolve_invoker(runtime)` 带不合格回退 + 留痕。

⚠️ 文档里明确写了边界（`invoker.py:16-17`）：
> `stream()` 替换的是**单轮流式请求**；流式的多轮工具循环仍在 `chat.stream_response` 里
> （要连整条主循环一起接管，用 `supersedes = ("agent",)`）。

**这句话正好暴露了缺口的形状**："整条主循环"今天没有槽，只能靠 `agent`（整体启停）这种核弹级开关。
`run_turn` 就是把"整条主循环"变成**可调用**（而非只能关闭）。

### 2.2 工具协议（`tools/spec.py`）

| 概念 | 说明 | 对外暴露？ |
|---|---|---|
| `ToolSpec` | 工具定义（name/description/parameters/handler/permission/scopes/source/category/max_result） | ❌ 未从 `app.agent` 导出 |
| `ToolContext` | 调用上下文（module/bot/session_id/event/runtime/user_id/group_id/extra） | ✅ |
| `build_tools(specs)` | → OpenAI `tools` 参数 | ❌ |
| `make_executor(specs, ctx)` | → `async (name, args) -> str`，含 20s 超时/权限/截断/遥测/`tool_call_hook` | ❌ |
| `@tool(...)` | 声明式注册 | ✅ |
| `ModuleToolRegistry` | `enabled_specs()`（模块工具的启停过滤） | ❌ |

**结论：模块能"加工具"，不能"用工具"**。要在自己的 agent loop 里跑框架工具
（`expand_message`、`get_chat_history`、`onebot` 系列），必须 `from app.agent.tools.spec import make_executor`
（内部模块，`app/agent/__init__.py` 明确说"内部实现不承诺稳定"）。

### 2.3 工具循环原语（`loop/tool_loop.py:98`）

```python
async def normalize_and_execute_tool_calls(tool_calls, tool_executor, tool_results)
    -> tuple[list[dict], list[dict]]
```
负责 id 自洽、arguments 归一、**并行执行**、错误文本回传、回应要求注入。
同样未导出。`stream_response` 与 `proactivity/stream.py` 都在用它 —— 三处重复的循环骨架就靠它兜着。

---

## 3. 会话层（`app/agent/session/`）

### 3.1 `SessionManager` 其实是**可用的**公开东西（更正我上一版的说法）

`manager.py:259-272`：

```python
class SessionManager:
    _instances: dict = {}
    def __new__(cls, bot_id: str):      # 按 bot_id 的进程级单例
```

方法面已经相当完整：

| 方法 | 用途 |
|---|---|
| `add_message(session_id, role, content, ...)` | 写历史（支持结构化 `segments` + `meta_lines`） |
| `get_history(session_id, limit)` | 读历史（渲染后的 dict 列表） |
| `history_snapshot(...)` | **未投影的原始条目**（自带渲染器组装用） |
| `import_history(...)` | 批量导入外部历史 |
| `upsert_streaming_assistant` / `end_streaming_assistant` | 流式边发边写 |
| `mark_last_assistant_message_id` | 回填发送句柄 |
| `create_session` / `get_session` / `destroy_session` / `forget_session` | 会话 CRUD |
| `new_conversation` / `switch_conversation` / `list_conversations` / `delete_conversation` | 多对话 |
| `get_stats` / `restore_session_from_archive` | 统计 / 归档恢复 |

**结论修正：模块今天其实能读写历史** —— `SessionManager(str(module.bot_id))` 拿到的
就是 `AgentRuntime` 用的**同一个实例**。缺的不是能力，是**（a）文档没写，（b）有一个脚踏陷阱**：

> ⚠️ 单例 key 是原始参数：`AgentRuntime` 传入 `str(bot_id)`，而 `module.bot_id` 是 **int**。
> 模块写 `SessionManager(module.bot_id)` 会拿到**另一个实例**（`123` vs `"123"`），
> 各自一份 `_streaming_entries`，历史写入看起来"成功了"但框架读不到。
> 修法二选一：`__new__` 里 `bot_id = str(bot_id)`，或提供 `ctx.services.sessions(bot_id)` 门面。

### 3.2 `HistoryManager` / `SQLiteHistoryStore`

`create_history_store(bot_id)` 决定落点；两者接口一致（`save_session` / `load_history` /
`list_tasks` / `export_text` / `update_history` / `import_conversation` / `delete_history`）。
→ 「导出会话」这类**业务**功能模块可以自己写，不需要框架接口。

### 3.3 `session/enrich.py`（补全记账本）

`ExpansionLedger` + `commit()` + `rendering_for()`：把"工具取回过的内容"记在
`data/llm/<bot_id>/` 下，下一轮渲染成 `【已展开:…】`。这是**记忆的一种**，纯业务，
不构成框架接口缺口。

---

## 4. 主动/定时（`app/agent/proactivity/`）—— 业务侧，只列事实

### 4.1 三条路径各自复制了「生成 → 发送 → 落历史」

| 位置 | 生成 | 发送 | 落历史 | 触发 pre_send/post_send？ | 解析 `[reply]`/`[@qq]`？ | `mark_replied()`？ |
|---|---|---|---|---|---|---|
| `loop/pipeline.py:_run` + `_send` | `generate_response` | `bot.send_*` | generate 内部 | ✅ | ✅ | ✅ |
| `loop/pipeline.py:_run_stream` | `stream_response` | 同左 | 同左 | ✅ | ✅ | ✅ |
| `proactivity/manager.py:_check_and_chat` | `resolve_invoker().chat()` 或 `stream_send_initiative` | `bot.send_*` | `add_message(strip_all_tags)` | ❌ / ❌ | ❌ | ❌ |
| `proactivity/scheduler.py:_fire` | `_generate_reply()` 或 `stream_send_initiative` | `bot.send_*` | 同上 | ❌ | ❌ | ❌ |
| `proactivity/stream.py:stream_send_initiative` | `resolve_invoker().stream()` | `StreamSendPool(send_message=…)` | 调用方写 | ❌（**没传 pre_send/post_send**） | ❌（`Message.from_text`） | — |

> 这张表就是"输出侧不一致"的全部证据。**同一个 LLM 回复，走 chat 路径与走定时路径，
> 落进历史的文本、所触发的钩子、是否重置群冷却，全都不一样。**

### 4.2 定时任务引擎（`scheduler.py`）

- `TaskEntry` 是纯数据（`enter.py`），`_run` 循环按 `next_at` 睡眠 → `_fire`；
- 用 `task_manager.create_task(owner=f"agent:{bot_id}")`，随 runtime 停止而取消；
- 有 `find_similar`（去重）、`trigger_now`、`status`、`cancel`、`list`；
- ⚠️ 与 AstrBot 的 `cron_manager`（`add_basic_job` / `add_active_job` / `update_job` / `list_jobs` / `run_job_now`）
  相比：**没有 update、没有按 job_type 分类、没有"由插件直接增删"的稳定入口**
  （`build_schedule_tool` 是给 LLM 用的工具，`ScheduleFeatureController` 只能整体关掉）。

### 4.3 `delivery/speech.py` 的发言通道门槛（三条件）

```python
def speech_via_tool(config, *, stream_output=None) -> bool:
    return is_qq_agent(config) and config.get("speech_via_tool") and stream_output
```

⚠️ `stream_output` 默认从**全局配置**读 → 意味着"发言通道是否生效"由配置耦合决定，
模块无法声明"我这一轮要发言通道"。这正是用户说的"高级功能强绑在配置页"的典型。

---

## 5. 模块加载时的钩子绑定（`app/plugin/loader.py`）

| 函数 | 绑定到 |
|---|---|
| `_register_service_hooks`（:153） | `services.{send_hooks, before_send_hooks, api_hooks, lifecycle_hooks, event_completed_hooks}` |
| `_register_llm_hooks`（:171） | `runtime.llm_hooks`（**必须先有 runtime**，`bot_service.on_bot_login` 保证） |
| `_register_runtime_capabilities`（:196） | `runtime.llm_tools` / `runtime.skills` / `runtime.llm_tool_call_hooks` |
| `register_data_sources`（:229） | `services.providers`（list/dynamic）+ `services.scheduler`（`SCHEDULES`）+ `requires` 自检 |
| `activate`（:256） | `on_load()` + `features.acquire_module()`（`supersedes`） |

**这张表说明：模块能挂载的位置只有"5 个服务钩子 + llm_hooks + 3 个 runtime 表 + 数据源 + 调度器"。**
新增任何"模块可挂载点"，都必须在这张表里加一行——**这是判断"接口有没有暴露"的最快口径。**

---

## 6. 顺带查实的既有缺陷（与本需求直接相关）

| # | 缺陷 | 证据 |
|---|---|---|
| 6.1 | **出站链扩展接口是死的** | `bootstrap.py:324` `OutboundPipeline([SendNode()])` 是字面量；`NodeRegistry.register_outbound()` 在 `nodes/registry.py:29` 存在但**无人读取**；`tests/plugin/test_nodes.py:84-111` 只测手工构造的 node list，测不到接线 |
| 6.2 | **`NodeRegistry` 不在 `ServiceAccess`** | `base.py:209-235` 无该字段；`bootstrap.py:321` 只进了容器 |
| 6.3 | `time_core` 事件已废弃但仍在文档里 | `application/cron_service.py:1` 明确"替代旧 time_core 广播"；`domain/events.py:280` 类还在但**全库无人构造**；`README.md:474` 仍列在 subscribe 表 |
| 6.4 | 主动/定时流式路径不触发 `pre_send`/`post_send` | `proactivity/stream.py:98` 构造 `StreamSendPool(config, send_message=…)` 未传钩子 |
| 6.5 | 主动/定时路径不解析 `[reply]` / `[@qq]` 输出指令 | `proactivity/stream.py:134` `Message.from_text(clean)`；`scheduler.py:397` 直接发字符串 |
| 6.6 | 主动/定时路径不重置群冷却 | `scheduler.py:403-405` 只 `add_message`，无 `session.mark_replied()`；chat 路径有（`generate.py:216-217`） |
| 6.7 | 落历史清洗口径不一致 | chat 用 `_clean_output_for_history`（含括号清洗），主动/定时只用 `strip_all_tags` |
| 6.8 | `SessionManager` 单例 key 类型不一致 | `manager.py:265` `__new__(cls, bot_id: str)` 直接用原值做 key；`AgentRuntime` 传 `str(bot_id)`，`module.bot_id` 是 int |
| 6.9 | `spawn` 语义靠自觉 | `task_manager.py:43` `owner="system"` 默认；`registry.py:241/275` 只清 `module:<名>:<bot_id>` |
| 6.10 | 账号漂移时模块实例不回收 | `registry.py:198-223` `load_all` 只加不卸；`AgentManager` 有 `bind_account`/`_evict_runtime`（`runtime.py:385-422`），模块侧无对应机制 |
| 6.11 | `SCHEDULES` 未进模块开发文档 | 只在 `module/CONFIG_SCHEMA.md:395` 与 `MAINTENANCE.md:175`；`docs/MODULE_DEV.md` 没有 |
| 6.12 | 框架钩子 `module=None`，可能被误清 | `enhance.py:313-315` 注册时 `module` 缺省 None；`hooks.py:49-50` `unregister_module(None)` 会全删 |

---

## 7. 汇总：要补的接口（按"业务不给接口 / 基础必补 / 高级可替代"三档）

### 7.1 明确**不**做接口（业务逻辑，模块自己写）

| 项 | 理由 | 模块用什么写 |
|---|---|---|
| 主动回复 / 定时问候 | 触发时机、话术、冷却、免打扰全是业务 | 订阅消息 + `ctx.services.scheduler`/`task_manager` + 发消息 + `llm_turn` |
| 记忆召回策略 / 知识库检索策略 | 策略与数据形状是业务 | 已有 `runtime.memory` / `runtime.knowledge` 只作参考，模块自建 |
| 流式切句 / 发送节奏 / 发言通道 | 属于"怎么说话"，不是"能不能说话" | 配置 + `llm_turn(stream=True)` |
| 群管 / 签到 / 解析器 | 纯业务 | 现有 `@command` / `@module_hook` 已够 |

> 因此 `FeatureRegistry` 里 `proactive` / `schedule` / `memory` / `knowledge` / `onebot_tools`
> 这五个 `ConfigToggleFeature` **保持原样即可**——它们的语义就是"关掉框架的，让我的生效"。

### 7.2 基础接口（必补，缺一个模块就得抄框架代码）

| # | 接口 | 形态 | 改动位置 |
|---|---|---|---|
| B1 | **按会话发消息** | `self.send_to(session_id, message) -> dict \| None`（自动解析 `group_`/`private_` 前缀，走 `bot.send_*` 以复用出站链与全部钩子） | `plugin/base.py`（新增方法）；顺带消掉 5 处重复（`pipeline.py:520`、`manager.py:388`、`stream.py:92`、`scheduler.py:354/397`） |
| B2 | **带 owner 的后台任务** | `self.spawn(coro, name=None)` → `task_manager.create_task(owner=f"module:{name}:{bot_id}")` | `plugin/base.py`；`task_manager.py:43` 的默认值改为必填或加 `owner_of(module)` 助手 |
| B3 | **会话历史读写门面** | `ctx.services.sessions(bot_id)` 返回 `SessionManager`（顺带修 6.8 的类型 key） | `plugin/base.py:ServiceAccess` + `bootstrap.py:222`；`session/manager.py:265` |
| B4 | **框架工具集只读视图** | `ctx.services.tools(bot_id) -> list[ToolSpec]` + 导出 `ToolSpec/build_tools/make_executor` | `agent/__init__.py:47-58`；`agent/tools/spec.py` 增加 `framework_specs(runtime, ctx)`（把 `tool_source._collect_llm_ext` 的 8 段 if 复用一次） |
| B5 | **出站链真正接线** | `OutboundPipeline(node_registry.outbound_nodes() + [SendNode()])` + `rebuild()` | `bootstrap.py:323-327` |
| B6 | **`NodeRegistry` 进 `ServiceAccess`** | `ctx.services.node_registry` | `plugin/base.py:209-235` + `bootstrap.py:222` |
| B7 | **能力清单补全** | `SERVICE_FIELDS` 加 `node_registry`/`sessions`/`tools`/`runtime` | `application/capabilities.py:18-39` |
| B8 | 文档：删 `time_core`、补 `SCHEDULES`/`send_to`/`spawn` | — | `README.md:474`、`docs/MODULE_DEV.md` |

### 7.3 高级接口（可替代 / 可调用）

| # | 接口 | 形态 | 改动位置 |
|---|---|---|---|
| A1 | **跑一轮完整对话（无事件）** | `async def run_turn(runtime, *, session_id, is_private, user_text, user_id=None, group_id=None, event=None, kind="module", stream=False, send=False, system_prompt=None, exclude=()) -> TurnResult` → `TurnResult{text, messages, tool_results, usage, blocked}` | 新增 `agent/loop/turn.py`；内部就是现有 `prepare_prompt` → `build_for` → `apply_post_assemble` → `resolve_invoker().chat/stream` → 写历史；`send=True` 时走 `send_to` |
| A2 | **模块侧入口** | `self.llm_turn(user_text, session_id=..., send=False, stream=False)` | `plugin/base.py`（照 `llm_chat` 的写法） |
| A3 | **服务侧入口** | `ctx.services.llm.turn(...)` | `agent/client.py`（`ModuleLLMClient` 加一个方法转调 A1） |
| A4 | **收敛三条重复路径** | `proactivity/manager._check_and_chat`、`scheduler._fire`、`proactivity/stream.stream_send_initiative` 全部改走 A1 | 生成/发送/落历史从 4 套变 1 套，顺带修 6.4/6.5/6.6/6.7 |
| A5 | **`stream_output` 从框架分支降级为参数** | `run_turn(stream=…)` 决定；框架不再在 3 处各判一次 | `loop/pipeline.py:310`、`manager.py:318`、`scheduler.py:286` |
| A6 | 已有可替代面（**无需新增**） | `assembly`（`build(req)`）、`provider_invoker`（`chat`/`stream`，且 `params` 可改写 model/temperature/max_tokens）、`post_assemble` | `features/controllers.py:290-326`、`assembler.py:736` |
| A7 | （可选）块级扩展 | `register_block(Block)`，让模块贡献一块而不接管整体 | `assembler.py:496` `PromptAssembler.__init__` |
| A8 | （可选）模块自建 HTTP 路由 | `ctx.services.web_api.register(route, handler, methods)`（对齐 AstrBot 的 `register_web_api`） | 新增；`webui/app.py` 挂载点 + `plugin/base.py` 门面 |

### 7.4 建议实施顺序

```
P0  B5/B6/B7     接线类，零行为变化，先把"注册了不生效"的坑填掉
P1  A1            run_turn（新增文件，不改任何现有路径）
P1  A2/A3         模块侧 + 服务侧入口
P2  B1/B2         send_to / spawn（消重复代码）
P2  A4/A5         三条路径收敛到 run_turn（行为对齐，跑全量回归）
P3  B3/B4         会话门面 + 工具集视图
P3  B8            文档同步
P4  A7/A8         可选扩展
```

---

## 8. 一句话结论

**该暴露而未暴露的，不是"主动回复"这类业务，而是三件"原料"：**
**① 按会话发消息（`send_to`）；② 无事件的完整一轮 LLM（`run_turn`/`llm_turn`）；③ 带 owner 的后台任务（`spawn`）。**
加上两个"已经写了但没接线"的（出站链、`NodeRegistry`）和一个"文档说有其实没有"的（`time_core`）。
补齐这五处，主动回复模块就能**只用公开接口**写出来，框架也不必再替它抄 4 遍生成/发送代码。
