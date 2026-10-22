# 流水线接口化施工清单

> 配套审计：`docs/research/pipeline-interface-audit.md`
> 口径来源：2026-02 与用户对齐的「业务不给接口 / 基础必补 / 高级可替代」三分法。
> 本文是**可执行的任务单**：每项都能独立提交、独立回滚、独立验证。

---

## 0. 总则

### 0.1 三条铁律

| # | 铁律 | 说明 |
|---|---|---|
| 1 | **不改现有模块接口** | `BaseModule` 的类属性/装饰器/`@llm_hook` 阶段/`FeatureRegistry` 的 8 个 feature_id 全部保持；只新增 |
| 2 | **行为零变化优先** | 纯搬运/接线的任务（P0）不得改变任何可观察行为；行为变化只允许出现在明确标注的任务里 |
| 3 | **一个任务一个提交** | Conventional Commits；测试与功能同提交（`feat(...)`/`refactor(...)`/`fix(...)`） |

### 0.2 验证档位（按 AGENTS.md）

| 档位 | 何时 | 跑什么 |
|---|---|---|
| 小 | 只动文档/注释/命名 | 不跑测试，直接提交 |
| 局部 | 单文件/单函数行为变更 | `venv\Scripts\python.exe -m pytest tests/<相关目录> -q` |
| 收尾闸门 | 每个阶段（P0/P1/P2/P3）结束 | `pytest -q` 全绿 + `ruff check .` 无新增 |

### 0.3 全量回归基线（每次阶段闸门都跑）

```powershell
venv\Scripts\python.exe -m pytest -q
ruff check .
```

**重点护栏（改动前先跑一遍存基线，改动后必须仍绿）：**

| 测试 | 守的是什么 |
|---|---|
| `tests/agent/assembly/test_llm_assembly_paths.py::test_stream_and_generate_send_identical_prompt` | 流式与非流式**必须发出同一个 prompt** |
| `tests/agent/assembly/test_llm_assembly_paths.py::test_both_paths_share_deduplicated_history` | 两条路径历史去重一致 |
| `tests/agent/test_llm_initiative_tools.py` / `test_llm_initiative_compression.py` | 主动/定时路径的工具与压缩 |
| `tests/agent/proactivity/test_proactive.py::test_check_and_chat_reaches_provider_call` | 主动消息确实走到 provider |
| `tests/agent/proactivity/test_scheduler.py`（6 例） | 定时触发/注销/多账号隔离/cron 解析 |
| `tests/agent/delivery/test_llm_speech_flow.py` / `test_send_pool_drain.py` | 发言通道与消息池收尾 |
| `tests/plugin/test_features.py`（11 例） | 租约/交棒/自动还原 |
| `tests/plugin/test_nodes.py` | 入站/出站节点链 |
| `tests/plugin/test_framework_capabilities.py` | capabilities 形状 + `missing_requirements` |
| `tests/architecture/test_architecture_boundaries.py` | 分层依赖（新文件不得引入跨层 import） |
| `tests/agent/test_agent_section_contract.py`（20 例） | 前端分区清单合法性 |

---

## 1. 阶段总览

```
P0 接线与可见性（零行为变化，2 天）
 ├─ P0-1 出站链按注册表构建
 ├─ P0-2 NodeRegistry / 会话 / 工具 进 ServiceAccess
 ├─ P0-3 capabilities 补全
 └─ P0-4 高级配置分区下沉（纯前端清单）

P1 无事件的完整一轮（核心，3 天）
 ├─ P1-1 prepare_turn：取值入口支持 TurnRequest（纯重构）
 ├─ P1-2 run_turn：新 API
 ├─ P1-3 self.llm_turn（模块门面）
 └─ P1-4 ctx.services.llm.turn（服务门面）

P2 原料三件套（2 天）
 ├─ P2-1 self.send_to（消 5 处重复）
 ├─ P2-2 self.spawn（带 owner 的后台任务）
 └─ P2-3 三条平行路径收敛到 P1 的入口（含 5 个既有缺陷修复）

P3 可观测与工具（1.5 天）
 ├─ P3-1 会话历史门面（顺带修单例 key 类型）
 └─ P3-2 框架工具集只读视图

P4 可选扩展（各 1 天，按需）
 ├─ P4-1 块级注册（register_block）
 └─ P4-2 模块自建 HTTP 路由

P5 文档与收尾（1 天）
```

**依赖关系（不可乱序的原因）：**

```
P0-1 ─┬─> P2-1（send_to 依赖出站链真的生效）
P0-2 ─┼─> P2-2（spawn 依赖 services 可见）
      └─> P3-1/P3-2
P1-1 ──> P1-2 ──> P1-3/P1-4 ──> P2-3（收敛依赖 run_turn 存在）
P1-2 ──> P3-2（工具视图要给 run_turn 之外的调用方）
```

---

## 2. P0 · 接线与可见性

### P0-1 出站链按注册表构建

**问题**：`NodeRegistry.register_outbound()` 存在但**无人读取** —— `bootstrap.py:324` 用的是字面量快照
`OutboundPipeline([SendNode()])`。模块注册出站节点 → 静默无效。

**改动**

| 文件 | 位置 | 改什么 |
|---|---|---|
| `app/plugin/nodes/outbound.py` | `OutboundPipeline.__init__`（:23-30） | 增加 `provider: Callable[[], list[MessageNode]] \| None = None`；`run()` 时若给了 provider 就现取节点（`NodeRunner` 内部已按 `order` 排序，`SendNode.order=100`，拦截节点取 <100 即排在它前面） |
| `app/bootstrap.py` | :323-327 | `OutboundPipeline(SendNode-proxy)` → `OutboundPipeline(provider=node_registry.outbound_nodes)`；并固定补一个 `SendNode()` 兜底（registry 里没有 SendNode，终端必须存在） |

**契约（写进 docstring）**
```python
class OutboundPipeline:
    def __init__(self, nodes: list[MessageNode] | None = None, *,
                 provider: collections.abc.Callable[[], list[MessageNode]] | None = None) -> None: ...
```
- `nodes`：固定快照（测试/一次性用）
- `provider`：每次 `run()` 现取（生产用，模块注册后**立即生效**，无需 rebuild）

**测试**
- `tests/plugin/test_nodes.py` 新增 `test_outbound_pipeline_reads_registry_live`：
  构造 `NodeRegistry` → `OutboundPipeline(provider=reg.outbound_nodes)` → 先跑一次断言只有 SendNode 行为 →
  `reg.register_outbound(拦截节点)` → **不重建 pipeline** 再跑一次，断言拦截生效。
- 既有 3 个 outbound 用例（`test_outbound_pipeline_passthrough/intercept/rewrite_params`）必须仍绿。

**验收**：`register_outbound` 后下一条出站消息即被拦截/改写，进程内无需重启。

**风险**：低。`provider` 缺省为 `None` → 走 `nodes` 快照 = 旧行为逐字不变。

**提交**：`fix(plugin): build outbound pipeline from node registry instead of a frozen list`

---

### P0-2 `ServiceAccess` 补齐服务

**问题**：`NodeRegistry` / 会话管理器 / 框架工具集都**不在 `ServiceAccess`**（`plugin/base.py:209-235`），
模块只能 `get_active().get(X)` 偷容器（违反 `api.py` 里写明的依赖方向）。

**改动**

| 文件 | 位置 | 改什么 |
|---|---|---|
| `app/plugin/base.py` | `ServiceAccess`（:209-235） | 新增 4 个字段（见下） |
| `app/bootstrap.py` | `ServiceAccess(...)`（:222-233） | 装配这 4 个字段 |

**新增字段（全部可选、全部可为 None，老代码不受影响）**

```python
node_registry: Any = None      # 入站/出站节点注册表（插件可注册/替换节点）
sessions: Any = None           # callable(bot_id) -> SessionManager（会话历史读写）
tools: Any = None              # callable(bot_id, **kw) -> list[ToolSpec]（框架工具集只读视图）
runtime: Any = None            # callable(bot_id) -> AgentRuntime | None（只读访问，替代偷容器）
```

**装配（bootstrap.py）**

```python
sessions=lambda bot_id: SessionManager(str(bot_id)),   # 注意 str()
tools=lambda bot_id, **kw: framework_tools(...),        # P3-2 落地前先留 None
runtime=lambda bot_id: agent_manager.get_runtime(bot_id),
node_registry=node_registry,                            # 注意：node_registry 此刻尚未创建
```

> ⚠️ **装配顺序陷阱**：`node_registry` 在 `bootstrap.py:315` 才创建，而 `ServiceAccess` 在 `:222` 就构造了。
> 解法：`ServiceAccess` 用 `lambda: node_registry` 惰性取值（字段存 callable 也可以），或把
> `ServiceAccess` 构造移到 `:321` 之后。**推荐后者**（一处顺移，语义最直白）。

**测试**
- `tests/plugin/test_framework_capabilities.py::test_capabilities_with_real_container` 之后新增断言：
  `ctx.services.node_registry is not None` / `sessions("123")` 与 `SessionManager("123")` 是同一对象。

**验收**：模块内 `self.ctx.services.node_registry.register_inbound(...)` 可用。

**提交**：`feat(plugin): expose node registry, sessions, tools and runtime on ServiceAccess`

---

### P0-3 `capabilities()` 补全

**改动**

| 文件 | 位置 | 改什么 |
|---|---|---|
| `app/application/capabilities.py` | `SERVICE_FIELDS`（:18-39） | 追加 `"node_registry"`, `"sessions"`, `"tools"`, `"runtime"` |

**测试**：`tests/plugin/test_framework_capabilities.py::test_collect_capabilities_shape_without_anything` 的
字段集合断言同步；新增 `test_capabilities_lists_new_services`。

**验收**：`GET /api/framework/capabilities` 的 `services` 数组含 4 个新名字；
模块 `requires = ("node_registry",)` 不再报缺。

**提交**：`feat(application): list new module services in framework capabilities`

---

### P0-4 高级配置分区下沉（纯前端清单）

**问题（用户原始诉求）**：`app/webui/dashboard/src/agent/sections.json` 里
**流式输出 `/agent/stream`（order 50）** 与 **长期记忆 `/agent/memory`（order 60）** 仍属 `area: "agent"`（主区），
而工具/主动消息/输出已在 `area: "advanced"`。用户在前端看到的"主页一堆高级配置"就是这两个 + 一个未分层的 nav。

**改动（只动清单与前端路由，不动后端 schema）**

| 文件 | 改什么 |
|---|---|
| `app/webui/dashboard/src/agent/sections.json` | `stream` 与 `memory` 两个 section 的 `area` 改 `"advanced"`，`path` 改 `/advanced/stream`、`/advanced/memory`，`order` 调到 15 / 40 使之与既有 advanced 页连号 |
| `app/webui/dashboard/src/router/*`（或 nav 定义） | 高级区入口默认折叠；`/advanced/*` 全部挂在「高级」分组下（已有 `_AREAS` 概念，按现有实现补 nav 分组） |
| `tests/agent/test_agent_section_contract.py` | `_AREAS`、`test_section_area_matches_path`、`ADVANCED_ALLOWED_IN_BASIC` 相应更新；新增 `test_advanced_sections_are_not_on_main_nav`（断言 `area=="advanced"` 的 section 不出现在主区 nav 列表） |

**验收**：主区（`/agent/*`）只剩：基础配置 / 权限 / 会话管理 / 对话行为 四页；
流式、记忆、工具、主动+定时、输出 五页全部在「高级」区，且默认收起。

**风险**：`test_agent_section_contract.py` 有 20 个用例会一起校验（字段归属唯一、预算、importance 分层），
改 `area` 需同步 `ADVANCED_ALLOWED_IN_BASIC`；**先跑该文件全绿再提交**。

**提交**：`refactor(dashboard): move stream and memory config to the advanced area`

---

## 3. P1 · 无事件的完整一轮（核心）

### P1-1 `prepare_turn`：取值入口支持 `TurnRequest`（纯重构，行为零变化）

**问题**：`prompt_prep.prepare_prompt(runtime, event, ctx)` 的签名绑死 `event`，
于是 `proactivity/manager.py:167-402` 与 `scheduler.py:428-548` 各自手搓一整套取值（口径已漂移）。

**设计**：把"事件 → TurnInput"这一步**从函数里提出来**，让调用方可以直接给 `TurnInput`。
`TurnInput` 已经存在（`app/agent/loop/turn_input.py:43`），字段刚好够。

**改动**

| 文件 | 位置 | 改什么 |
|---|---|---|
| `app/agent/loop/prompt_prep.py` | `prepare_prompt`（:98-317） | 拆成两层：<br>`prepare_prompt(runtime, event, ctx=None, *, session_mgr=None)` → **保持原样**，内部转调下面这个<br>`prepare_prompt_from_turn(runtime, turn, ctx=None, *, session_mgr=None, bot=None)` → 新函数，`event` 从 `ctx.event if ctx else None` 取 |
| 同上 | `:111 normalize_event` | 只在 `prepare_prompt` 里调用；`prepare_prompt_from_turn` 直接收 `turn` |
| 同上 | `:127 record_user_turn` | 加护栏：`event is None` 时若 `turn.user_text` 为空则跳过；`_event_nickname(event)` 改 `_event_nickname(event) if event else ""` |
| 同上 | `:155 _fetch_pre_history` | 新增 `bot=` 参数；`event is None` 时用 `bot` 与 `group_id/user_id` 走同一路径（内部已只依赖 `event.bot`/`event.self_id`/`event.bot_id`，改成参数化即可） |
| 同上 | `:248 event.bot` | → `event.bot if event else bot` |
| 同上 | `:255-274` 本轮图片 | `event is None` 时整段跳过（`user_images=[]`），与现有"没有图片"分支等价 |
| 同上 | 返回 `meta` | 追加 `"bot"` 键（下游 `stream_response`/`generate_response` 用得到） |

**测试**（这一条最关键：**必须先写等价性测试再改**）
- 新增 `tests/agent/test_prepare_turn_equivalence.py`：
  构造一个最小 fake runtime + fake event，分别在改造前/后调用 `prepare_prompt`，断言
  返回的 `PromptRequest` 字段**逐字段相等**（`history` / `pre_history_text` / `group_log_text` /
  `referent_text` / `memory_text` / `skill_blocks` / `available_tools` / `modalities` / `user_text`）。
- 既有 `tests/agent/assembly/test_llm_assembly_paths.py`（3 例）必须仍绿。

**验收**：`prepare_prompt(runtime, event)` 行为 100% 不变；新函数在 `event=None` 时能产出可装配的 `PromptRequest`。

**风险**：中。`record_user_turn` 与图片解析是"事件专属"的，必须用护栏而不是 if 判断糊过去。
**回滚**：新函数是纯新增，旧函数一行不动即天然可回滚。

**提交**：`refactor(loop): split prepare_prompt into event-normalised and turn-based entry`

---

### P1-2 `run_turn`：新 API（新增文件，不改任何现有路径）

**新增文件**：`app/agent/loop/turn.py`

```python
@dataclass
class TurnRequest:
    """一轮对话的全部输入（业务模块/主动/定时都用它，替代"事件"这个前置条件）。"""
    session_id: str
    is_private: bool
    user_text: str = ""
    user_id: str = ""
    group_id: str | None = None
    kind: str = "module"              # chat / proactive / schedule / module
    event: Any = None                 # 可选：有真实事件时带上（图片/message_id/群资料）
    system_prompt: str | None = None  # 覆盖人设（None = 用配置）
    stream: bool | None = None        # None = 按 config.stream_output
    record_history: bool = True       # 是否把这轮写进会话历史
    with_images: bool = True
    extra_user_content_parts: list[str] = field(default_factory=list)

@dataclass
class TurnResult:
    text: str = ""
    messages: list[dict] = field(default_factory=list)   # 最终请求体（对账用）
    tool_results: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    blocked: bool = False             # post_assemble 置空 / 钩子 skip
    error: str = ""
    session_id: str = ""

async def run_turn(
    runtime: Any,
    req: TurnRequest,
    *,
    ctx: Any = None,
    on_sentence: Callable[[str], Awaitable[None]] | None = None,
) -> TurnResult:
    """跑一轮完整对话：取值 → 组装 → post_assemble → 请求 → 可选写历史。

    - `on_sentence` 非空时走流式，逐句回调（回调里做什么由调用方决定：发出去 / 攒起来）；
    - `on_sentence` 为空时非流式，一次性拿到 `TurnResult.text`；
    - **不发送、不触发 pre_send/post_send**（那是交付层的职责，见 `send_to`）；
    - `req.record_history=True` 时按 chat 路径的口径写历史（含 `_clean_output_for_history`）。
    """
```

**内部步骤（照 `apply_post_assemble` 的成品范式写）**

1. `config.set_session(req.session_id)`（`finally: clear_session()`）—— 与现有路径一致
2. `turn = TurnInput(is_private=..., session_id=..., user_id=..., group_id=..., user_text=..., raw_user_text=..., segment_text=..., include_pre_history=...)`
   （无事件时 `include_pre_history` 取 `config.get("include_pre_history")`）
3. `pr, session, meta = await prepare_prompt_from_turn(runtime, turn, ctx, bot=req.event.bot if req.event else bot)`
4. `messages = build_for(runtime, pr)` → `apply_post_assemble(runtime, ctx, messages, req=pr)`；`None` → `TurnResult(blocked=True)`
5. `provider_chain = meta["provider_chain"]`；`use_tools = bool(all_specs) and supports_tool_use(modalities)`
6. 非流式：`resolve_invoker(runtime).chat(...)`，参数集合**逐字复制** `generate.py:97-107`
7. 流式：`resolve_invoker(runtime).stream(...)`，切句用 `split_sentences`，`on_sentence(clean)`；参数集合**逐字复制** `chat.py:342-357`
8. 兜底（搬 `generate.py:169-193` 的原文）：`response.ok` 判假 → `"抱歉，我暂时无法回答，请稍后再试。"`；空正文同理
9. `record_history=True` → `session_mgr.add_message(session_id, "assistant", _clean_output_for_history(config, text))` +
   `if not is_private: session.mark_replied()` + `save_session`
10. telemetry：`runtime.telemetry.record_call_simple(...)`（搬 `generate.py:195-213`）
11. `_memory_consolidate` + `_commit_history_enrichment`（搬 `generate.py:220-221`）

**明确不搬**（保留在原处，风险最低）：
- speech 发言通道（`chat.py:265-548` 那一整套）
- 部分历史 upsert（`upsert_streaming_assistant`）
- `force_speak` 补救轮
→ 这些是**当前 chat 流式路径专属**的语义，`run_turn` 不承载；`stream_response` 保持不动。

**测试**
- 新增 `tests/agent/loop/test_run_turn.py`：
  1. `test_run_turn_non_stream_returns_text`（fake invoker 返回固定文本）
  2. `test_run_turn_stream_calls_on_sentence_in_order`
  3. `test_run_turn_records_history_once`
  4. `test_run_turn_blocked_when_post_assemble_empties_messages`
  5. `test_run_turn_no_event_uses_explicit_session`
- 新增 `tests/agent/loop/test_run_turn_parity.py`：**同一 fake invoker 下，`run_turn` 与
  `generate_response` 发出的 `messages` 完全相同**（用 `prompt_dump` 或直接比对 `TurnResult.messages`）。

**验收**：模块能 `await run_turn(runtime, TurnRequest(session_id="group_123", is_private=False, user_text="总结一下"))`
拿到文本，且 `messages` 与真实聊天路径一致。

**风险**：中。第 6/7 步参数集合是"粘贴复制"，容易漏键 → **用 parity 测试锁**。

**提交**：`feat(loop): add run_turn for running a full LLM turn without an event`

---

### P1-3 模块门面 `self.llm_turn`

**改动**

| 文件 | 位置 | 改什么 |
|---|---|---|
| `app/plugin/base.py` | `BaseModule`（`llm_chat` 之后，:442 附近） | 新增方法 |

```python
async def llm_turn(
    self,
    user_text: str,
    *,
    session_id: str,
    is_private: bool | None = None,   # None = 按 session_id 前缀推断
    kind: str = "module",
    stream: bool | None = None,
    on_sentence=None,
    record_history: bool = False,     # 默认不写：模块自己决定要不要污染会话
    send: bool = False,               # True 时用 send_to 边产边发
    **turn_kwargs,
):
    """用框架跑一轮完整对话（含历史/组装/工具循环/记忆），返回 TurnResult。"""
```

- 内部：`runtime = self.ctx.services.runtime(self.bot_id)`；缺 runtime → 返回空 `TurnResult(error="no_runtime")` + 留痕（**不抛**，与 `llm_chat` 一致）
- `send=True` → `on_sentence` 包装为 `lambda s: self.send_to(session_id, s)`

**测试**：`tests/plugin/test_module_llm_turn.py`（2 例：无 runtime 降级 / `send=True` 调到 `send_to`）

**提交**：`feat(plugin): add BaseModule.llm_turn for module-driven full LLM turns`

---

### P1-4 服务门面 `ctx.services.llm.turn`

**改动**：`app/agent/client.py`（`ModuleLLMClient`，:181 之后）新增

```python
async def turn(self, user_text: str, *, bot_id=None, session_id="", is_private=None, **kw) -> "TurnResult":
    runtime = self._runtime(bot_id)
    if runtime is None:
        from app.agent.loop.turn import TurnResult
        return TurnResult(error="no_runtime")
    from app.agent.loop.turn import TurnRequest, run_turn
    return await run_turn(runtime, TurnRequest(...))
```

**验收**：`res = await ctx.services.llm.turn("你好", bot_id=123, session_id="private_456")`。

**提交**：`feat(agent): expose run_turn through the module LLM client`

---

## 3.5 五个结构性阻塞的专项方案

> 这五条不是"缺一个接缝"，而是**结构导致接缝无法存在**。审计 §9.4 已给出证据，
> 这里给方案。每个方案都遵循同一形状：**现状证据 → 目标契约 → 分步 → 测试 → 验收 → 回滚点**。
> 全部落地后 `tests/architecture/test_extensibility_contract.py` 的记分板应从
> `full=10 / half=23 / unwired=22` 提升到 `full≈34`。

### B-1 事件是生成的前置条件（挡住 4 个接缝）

**现状证据**

| 入口 | 位置 | 硬绑点 |
|---|---|---|
| `prepare_prompt(runtime, event, ctx)` | `loop/prompt_prep.py:98` | 第 111 行 `normalize_event(config, event, ctx)` |
| `generate_response(runtime, event, ctx)` | `loop/generate.py:48` | 第 58 行 `event.message_type` |
| `stream_response(runtime, event, ctx)` | `loop/chat.py:220` | 第 231 行 `event.message_type` |

三条签名都要求**真实事件对象**，于是主动消息 / 定时任务 / 任何外部触发者只能各抄一份取值逻辑
（`proactivity/manager.py:167-402`、`scheduler.py:428-548`）。

**目标契约**：`event` 从"必需"降级为"可选输入"，由 `TurnRequest` 承载一切。

```python
# app/agent/loop/turn.py（新）
@dataclass
class TurnRequest:
    session_id: str
    is_private: bool
    user_text: str = ""
    user_id: str = ""
    group_id: str | None = None
    kind: str = "module"        # chat / proactive / schedule / module
    event: Any = None           # 可选：有就用（图片 / message_id / 群资料）
    stream: bool | None = None  # None = 按 config.stream_output
    record_history: bool = True
    system_prompt: str | None = None
    extra_user_content_parts: list[str] = field(default_factory=list)

@dataclass
class TurnResult:
    text: str = ""
    messages: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    blocked: bool = False
    error: str = ""
    session_id: str = ""

async def run_turn(runtime, req: TurnRequest, *, ctx=None, on_sentence=None) -> TurnResult: ...
```

**分步（每一步独立提交、独立回归）**

| 步 | 动作 | 关键约束 |
|---|---|---|
| B-1.1 | `turn_input.py` 增 `from_event(config, event, ctx)` 与 `from_request(req)` 两个构造器 | 纯新增；`normalize_event` 保留并转调 `from_event` |
| B-1.2 | `prompt_prep.py` 增 `prepare_prompt_from_turn(runtime, turn, ctx=None, *, bot=None, session_mgr=None)`；`prepare_prompt` 转调它 | **行为零变化**：`event is None` 时 `record_user_turn` / 图片解析 / `_event_nickname` 全部走"无事件"分支 |
| B-1.3 | 新增 `loop/turn.py`：`TurnRequest`/`TurnResult`/`run_turn` | 参数集合**逐字复制** `generate.py:97-107` 与 `chat.py:342-357`；**不搬** speech 通道与 partial-history upsert |
| B-1.4 | `generate_response` 改成 12 行薄壳，内部 `run_turn(...)`，返回值仍是 `str \| None` | 公开签名不变 |
| B-1.5 | `stream_response` **只**把取值段换成 `prepare_prompt_from_turn`，其余一行不动 | 400 行里含 speech/force_speak/partial history，**不动内部** |

**测试**

| 用例 | 守什么 |
|---|---|
| `tests/agent/loop/test_turn_input_from_request.py`（新） | `from_event` 与 `from_request` 产出的 `TurnInput` 逐字段相等 |
| `tests/agent/loop/test_run_turn_parity.py`（新） | 同一 fake invoker 下，`run_turn` 与 `generate_response` 发出的 `messages` **完全相同** |
| `tests/agent/assembly/test_llm_assembly_paths.py::test_stream_and_generate_send_identical_prompt` | 流式/非流式 prompt 一致（既有护栏，必须仍绿） |
| `tests/agent/test_llm_initiative_tools.py`（10 例） | 无事件路径的工具仍齐备 |

**验收**：`await run_turn(runtime, TurnRequest(session_id="group_123", is_private=False, user_text="总结"))`
返回非空 `text`，且 `TurnResult.messages` 与真实聊天请求逐字相同。

**回滚点**：B-1.2 与 B-1.3 都是纯新增，删掉新函数即回旧行为；B-1.4/B-1.5 各自可单独 revert。

---

### B-2 工具循环双实现（挡住 2 个接缝）

**现状证据（代码自己写明了这种不对称）**

| 路径 | 循环位置 | 证据 |
|---|---|---|
| 非流式 | **provider 内部**：`for _round in range(max_tool_rounds)` | `providers/openai_compat.py:382-412` |
| 流式 | **调用方内部**：`for _round in range(max_tool_rounds)` | `loop/chat.py:333-503` |

`openai_compat.chat_stream()` 的 docstring 亲口写着：
> 「工具执行循环由调用方（LlmPipeline / chat.stream_response）负责，这里只负责单次 HTTP 流式请求的解析。」

后果：任何"接管一轮对话"的模块必须同时理解两种循环位置；改一处必漏一处
（`max_tool_rounds` 就曾在一个地方硬编码 5，见 `chat.py:305-306` 注释）。

**目标契约**：**provider 只做单次 HTTP 请求，循环统一上移到 loop 层。**

```python
# app/agent/loop/tool_loop.py（已有，扩写）
async def run_tool_rounds(
    runtime, *,
    invoker, chain, messages, params, *,
    max_rounds: int,
    on_text: Callable[[str], Awaitable[None]] | None = None,   # 流式逐句
    budget=None,
) -> tuple[str, list[dict], dict, list[dict]]:
    """驱动一次完整的多轮工具对话。

    Returns: (final_text, tool_results, usage, final_messages)
    循环职责只在这里：轮数、空回复重试、思考吃满预算的收敛轮、工具结果回传。
    """
```

**分步**

| 步 | 动作 | 关键约束 |
|---|---|---|
| B-2.1 | 先统一"执行 + 回传"（**已完成**：`normalize_and_execute_tool_calls`，两条路径都在用） | 若 `openai_compat` 里还有私有实现，改为复用 |
| B-2.2 | 新增 `run_tool_rounds`，内部依次调 `invoker.chat` / `invoker.stream`，把 `chat.py:333-503` 的循环骨架搬进去 | 搬的时候保留全部注释（id 自洽/并行执行/回应要求都在这里解释过） |
| B-2.3 | 给 `ProviderInvoker.chat/stream` 增加 `max_tool_rounds=1` 的显式语义（单次请求），并让 `run_tool_rounds` 负责轮数 | 需要 `openai_compat.chat()` 读 `max_tool_rounds=1` 时**不进入**循环（它现在默认 5） |
| B-2.4 | 加退路开关 `tool_loop_in_loop_layer`（默认 `False` 先灰度一版） | 出问题时能一键回旧路径 |
| B-2.5 | 全绿后把默认值翻成 `True`，下一版本删掉旧分支 | — |

**测试**

| 用例 | 守什么 |
|---|---|
| `tests/agent/tools/test_tool_loop_rounds.py`（新） | 0 轮（无工具）/ 1 轮 / 多轮 / 超上限 / 参数非法 / 工具抛异常，6 例 |
| `tests/agent/tools/test_llm_tool_loop.py`（既有） | 不回归 |
| `tests/agent/providers/test_llm_provider_robustness.py`（既有） | provider 层回退与重试不受影响 |
| `tests/agent/test_llm_reasoning_overflow.py` / `test_llm_empty_reply_retry.py` | 收敛轮与空回复重试随循环上移后行为不变 |

**验收**：`openai_compat.chat()` 只发一次 HTTP（可用 fake transport 统计请求数）；
`run_tool_rounds` 在同一个 fake 上完成 N 轮且 `tool_results` 顺序正确。

**风险**：**高**（provider 语义变更）。缓解：B-2.4 的开关 + 两条路径各自的既有测试都保留。

---

### B-3 四路生成 + 五处发送（挡住 3 个接缝）

**现状证据**

| 路径 | 生成 | 发送 | 落历史 |
|---|---|---|---|
| chat 非流式 | `pipeline.py:314` → `generate_response` | `pipeline.py:520` | `generate.py:215` |
| chat 流式 | `pipeline.py:311` → `stream_response` | 同上 | `chat.py:570-577` |
| 主动消息 | `manager.py:361` invoker / `manager.py:318` 流式 | `manager.py:388-390` | `manager.py:344` |
| 定时任务 | `scheduler.py:594` invoker / `scheduler.py:286` 流式 | `scheduler.py:354/397` | `scheduler.py:404` |

**目标契约**：生成只有 `run_turn`（B-1）；发送只有 `delivery/channel.py::send_item`；
落历史只在 `run_turn` 内一处。

```python
# app/agent/delivery/channel.py（新，不是槽，就是函数）
async def send_item(
    runtime, *, bot, session_id: str, message: Message,
    ctx=None, trigger_event=None, hooks: bool = True,
) -> dict | None:
    """按会话发一条消息：解析 group_/private_ → 走 bot.send_*（出站链 + 全部钩子）。

    hooks=True 时额外触发 LLM 流水线的 pre_send / post_send（主动/定时路径此前完全没有）。
    """
```

**分步**

| 步 | 动作 | 修掉的缺陷 |
|---|---|---|
| B-3.1 | 新增 `domain/session.py::parse_session_id` + `delivery/channel.py::send_item` | 消 5 处重复 |
| B-3.2 | `pipeline._send` 改调 `send_item`（`hooks=False`，因为 pipeline 自己会触发 pre_send/post_send） | — |
| B-3.3 | `manager._check_and_chat` 生成段 → `prepare_prompt_from_turn` + `invoker`；发送段 → `send_item(hooks=True)` | 取值漂移、`pre_send`/`post_send` 不触发 |
| B-3.4 | `scheduler._fire` 同上 | 同上 + `[reply]`/`[@qq]` 不解析 |
| B-3.5 | 删除 `proactivity/stream.py`，流式走 `run_turn(stream=True, on_sentence=…)` | `StreamSendPool` 没传钩子 |
| B-3.6 | 三条路径落历史统一调 `_clean_output_for_history` + `mark_replied()` | 清洗口径不一致、群冷却不重置 |

**测试**

| 用例 | 守什么 |
|---|---|
| `tests/agent/proactivity/test_initiative_output_parity.py`（新） | 主动/定时路径**触发 pre_send/post_send**、**解析 `[reply]`**、**调 `mark_replied`**（三条现在都是红的） |
| `tests/agent/assembly/test_llm_assembly_paths.py`（既有 3 例） | 四路发出的 messages 一致 |
| `tests/agent/proactivity/*`（既有 14 例） | 触发节奏/免打扰/计数不回归 |
| `tests/agent/delivery/test_send_pool_drain.py` | 消息池收尾不回归 |

**验收**：fil fake bot 记录所有调用，断言同一段文本经四条路径产生的**消息段序列 + 钩子序列 + 历史条目**完全一致。

**风险**：**高**（唯一改在跑业务的地方）。缓解：B-3.3/3.4 分两次提交，每次跑
`pytest tests/agent/proactivity tests/agent/assembly tests/agent/delivery -q`。

---

### B-4 全局注册无卸载路径（挡住 3 个接缝）

**现状证据（逐个查实）**

| 注册 | 有反注册？ | 证据 |
|---|---|---|
| `PROVIDERS`（provider 适配器） | ❌ | `providers/__init__.py:70` 只有 `register_provider` |
| `NodeRegistry`（入站） | ⚠️ 有 `remove(name)`，**无按归属清理** | `nodes/registry.py:43` |
| `ProviderRegistry`（数据源） | ❌ | `application/provider_service.py:71-104` 只有 `register_*` |
| `SessionManager._instances` | ❌ 无 evict | `session/manager.py:262` |
| 5 个钩子注册表 + `llm_hooks`/`llm_tools`/`skills` | ✅ | `registry.py:436-464` |
| `FeatureRegistry` 租约 | ✅ | `registry.py:238/272` |

**目标契约**：所有全局注册表支持 `unregister(owner)`，且由**同一处**统一清理。

```python
# 归属约定（与 task_manager 的 owner 对齐）
owner = f"module:{module_name}:{bot_id}"
```

**分步**

| 步 | 动作 |
|---|---|
| B-4.1 | `PROVIDERS`：`register_provider(name, cls, aliases=(), owner=None)` + `unregister_provider(name)` |
| B-4.2 | `NodeRegistry`：`register(node, direction=..., owner=None)` + `remove_owner(owner)` |
| B-4.3 | `ProviderRegistry`：`unregister_module(module)`（按 module_name 清理 list/dynamic） |
| B-4.4 | `SessionManager.evict(bot_id)`，在 `AgentManager._evict_runtime` 里调用（幽灵运行时清理时一起回收历史句柄） |
| B-4.5 | `loader.py::activate` 与 `registry.unload_single` 加统一 `_release_all(instance)`：把上面 5 处串起来，顺序固定（hook → tool → skill → feature → provider → node → data source） |
| B-4.6 | 加载失败回滚：`load_single` 用 try/except 包住工序，失败时调 `_release_all`（现在半途失败会留钩子 + 留下租约） |

**测试**

`tests/plugin/test_unload_is_clean.py`（新）：加载一个"注册了全部 7 类东西"的假模块 → 卸载 →
断言 7 个注册表都回到加载前的长度；再断言热重载 3 次后长度不增长（防幽灵累积）。

**验收**：热重载 10 次后，`len(PROVIDERS)` / `node_registry.inbound_nodes()` /
`ProviderRegistry` 条目数 / `SessionManager._instances` 数量全部不变。

---

### B-5 配置即接口（冻结 10 个接缝）

**现状证据**：`app/agent/config_schema.py` 共 **181** 个配置键；其中约 40 个本质是
"因为没接缝，所以只能做成开关"：

| 类别 | 例子 | 本应是什么 |
|---|---|---|
| 输出形态 | `stream_output`、`stream_send_by_tool`、`speech_via_tool` | `run_turn(stream=…)` / 输出策略接口 |
| 上下文增强 | `context_expand_enable`、`referent_*`（7 键）、`group_log_*` | 上下文 provider 注册表 |
| 路径复用 | `stream_proactive_enabled`、`stream_scheduled_enabled` | 四路收敛后不需要（B-3） |
| 预算策略 | `context_token_budget`、`turn_token_budget`、`empty_reply_retries` | 请求策略钩子 |

**目标**：**不删这些键**（它们是用户可见的功能），而是给每个键标注"可被谁接管"，
并让前端能显示"此项当前由 X 模块接管"。

```python
# app/agent/config_feature_map.py（新）
SEAM_CONFIG_KEYS: dict[str, tuple[str, ...]] = {
    # 接缝 id → 它冻结住的配置键（来自审计 §9.2）
    "S31": ("stream_output", "stream_proactive_enabled", "stream_scheduled_enabled"),
    "S36": ("speech_via_tool", "speech_max_bubbles", "speech_max_bubble_chars"),
    "S15": ("referent_resolve_enable", "referent_prefetch_enable", "referent_ambiguous_policy", ...),
    "S23": ("context_token_budget", "context_token_chars_per_token"),
    ...
}
```

**分步**

| 步 | 动作 | 收益 |
|---|---|---|
| B-5.1 | 新建 `config_feature_map.py`，把 40 个键映射到接缝 id | 让"哪些配置是技术债"可枚举 |
| B-5.2 | `capabilities()` 增加 `seam_config_keys` 字段 | 前端可显示"可被接管"标记 |
| B-5.3 | 先把 3 个高价值项从"框架分支"改成"读槽的默认值"：`stream_output`（→ `run_turn(stream=)`）、`speech_via_tool`（→ 输出策略）、`context_expand_enable`（→ 工具源） | 直接解冻 3 个接缝 |
| B-5.4 | 目标：`config_feature_map` 覆盖的键数从 ~40 降到 <10，并在 `test_extensibility_contract` 里加棘轮 | 度量收敛 |

**测试**：`tests/agent/test_config_seam_map.py`（新）：
① 映射里的每个键都存在于 `config_schema`；② 每个键属于且仅属于一个接缝；
③ 覆盖键数 ≤ 上限（棘轮，当前 40，逐步下调）。

**验收**：WebUI 字段旁能看到"可被模块接管"提示；`capabilities().seam_config_keys` 形状稳定。

---

### 3.6 五条阻塞的相互关系（务必按序）

```
B-1 事件解耦 ──┐
               ├─> B-3 四路收敛 ──> B-5.3 解冻配置项
B-2 工具循环 ──┘
B-4 卸载路径 ──> （独立，可并行；但必须在 B-3 之后做，否则收敛后的入口也要改两遍）
```

- **B-1 必须最先**：它定义 `run_turn`，B-3 的一切收敛都以它为目标形态；
- **B-2 可与 B-1 并行**（两者改的文件不重叠：`providers/` vs `loop/`），但 B-2.3 会改
  `ProviderInvoker` 语义，**必须在 B-3 之前**完成，否则收敛时会同时面对"循环位置变更 + 入口变更"；
- **B-4 放最后**：它是"清理纪律"，等接口形状定了再补；现在这批全文都有 6 个已确认的无卸载点
  （见 §B-4 表格），做完即关闭；
- **B-5.3 依赖 B-1/B-3**：只有 `run_turn(stream=)` 存在，`stream_output` 才能从"框架分支"降级为"默认值"。

### 3.7 完成后记分板预期

| 接缝 | 现在 | B-1 | B-2 | B-3 | B-4 | B-5 |
|---|---|---|---|---|---|---|
| S44/S42/S43/S16/S17 | ✗/✗/◧/◧/◧ | ✅ | | | | |
| S29/S31/S39 | ✗/✗/✗ | | ✅ | ✅ | | |
| S33/S37 | ◧/◧ | | | ✅ | | |
| S5/S6/S12/S27/S50 | ◧/✗/◧/◧/◧ | | | | ✅ | |
| S8/S15/S20/S23/S30/S34/S36 | ◧/✗/◧/✗/◧/◧/◧ | | | | | ✅ |
| **full 合计** | **10** | 14 | 16 | 20 | 25 | **34** |

---

## 4. P2 · 原料三件套

### P2-1 `self.send_to` — 按会话发消息

**问题**：同一段"解析 session 前缀 + 选 API"的代码在 5 处重复：
`loop/pipeline.py:520-524`、`proactivity/manager.py:388-390`、`proactivity/stream.py:92-96`、
`proactivity/scheduler.py:354-356` / `:397-399`。

**改动**

| 文件 | 位置 | 改什么 |
|---|---|---|
| 新增 `app/domain/session.py` | — | `parse_session_id(session_id) -> tuple[bool, str]`（`group_x`→`(True,"x")`；`private_y`→`(False,"y")`；无前缀→`(False, session_id)`）+ `make_session_id(is_group, target) -> str` |
| `app/plugin/base.py` | `BaseModule` | 新增 `async def send_to(self, session_id, message, *, bot=None, timeout=None) -> dict \| None`：<br>解析 → `bot.send_group_msg(int(gid), message)` / `send_private_msg(...)`（**务必走 `bot` 而非 `_direct_send`**，这样出站链 + `before_send/send/api` 钩子全部生效） |
| `app/agent/proactivity/manager.py` | `_session_parts`（:35-41） | 删除，改 import `app.domain.session.parse_session_id` |
| `app/agent/proactivity/stream.py` / `scheduler.py` | 上述 4 处 | 改调 `parse_session_id` + 保留 `bot.send_*`（这两处是运行时内部，暂不引入模块依赖） |

**测试**
- 新增 `tests/domain/test_session_id.py`（前缀解析 4 例）
- 新增 `tests/plugin/test_send_to.py`：fake bot 记录调用；断言群/私聊分发正确、`int()` 转换正确；
  断言走的是 `send_group_msg`（而非 `_direct_send` 直发）

**验收**：模块 `await self.send_to("group_123", "hi")` 能发出，且 `@send_hook` 能收到 `message_id`。

**提交**：`feat(plugin): add BaseModule.send_to with shared session id parsing`

---

### P2-2 `self.spawn` — 带 owner 的后台任务

**问题**：`task_manager.create_task` 默认 `owner="system"`（`core/task_manager.py:43`），
而热重载只清 `module:<名>:<bot_id>`（`registry.py:241/275`）→ **不传 owner 的任务变孤儿**。
这个约定只写在代码里。

**改动**

| 文件 | 位置 | 改什么 |
|---|---|---|
| `app/plugin/base.py` | `BaseModule` | 新增 `@property def task_owner(self) -> str: return f"module:{self.module_name}:{self.bot_id}"` 与 `def spawn(self, coro, *, name=None) -> asyncio.Task`（内部 `self.ctx.services.task_manager.create_task(coro, name=name, owner=self.task_owner)`） |
| `app/core/task_manager.py` | `create_task`（:39-44） | docstring 补一句「模块请用 `BaseModule.spawn`，否则热重载不会取消」 |
| `docs/MODULE_DEV.md` | 新增小节 | 写清 `spawn` 与 owner 约定 |

**测试**：`tests/plugin/test_spawn.py`：模块 spawn 一个 sleep 任务 → `registry.unload_single` → 断言任务被取消。

**验收**：模块热重载后无残留任务（`task_manager.count(owner="module:x:123") == 0`）。

**提交**：`feat(plugin): add BaseModule.spawn that owns background tasks correctly`

---

### P2-3 三条平行路径收敛（含 5 个既有缺陷修复）

> **这是全清单里唯一允许改变可观察行为的任务**，每个子项单独提交、单独回归。

| 子任务 | 改动 | 修掉的缺陷 |
|---|---|---|
| **P2-3a** `proactivity/manager._check_and_chat` 取值段（:183-301）→ `prepare_prompt_from_turn` | 删掉手搓的 `PromptRequest` 构造（约 60 行） | 口径漂移（`include_pre_history`/`fetch_at_nickname` 与 chat 不一致） |
| **P2-3b** `scheduler._build_messages`（:428-548）/`_collect_tools`（:410-427）→ 同上 | 同上 | 同上 |
| **P2-3c** 三条路径的发送后收尾统一 | 统一调用<br>`pre_send`/`post_send` 钩子（`run_stage(runtime,...)`）<br>`build_outbound_builder` 解析 `[reply]`/`[@qq]`<br>`_clean_output_for_history`<br>`session.mark_replied()` | 缺陷 #4/#5/#6/#7（见审计 §6） |
| **P2-3d** 删除 `proactivity/stream.py` | 改走 `run_turn(stream=True, on_sentence=发出去)`；文件删除 | 缺陷 #4（`StreamSendPool` 没传钩子） |
| **P2-3e** `generate_response` 内部改调 `run_turn` | 签名与返回值**不变**（`-> str \| None`） | — |

**测试（每个子项都要跑）**
```powershell
venv\Scripts\python.exe -m pytest tests/agent/proactivity tests/agent/assembly tests/agent/delivery -q
venv\Scripts\python.exe -m pytest tests/agent/test_llm_initiative_tools.py tests/agent/test_llm_initiative_compression.py -q
```
新增 `tests/agent/proactivity/test_initiative_output_parity.py`：
断言主动消息与定时任务的发送**触发 `pre_send`/`post_send` 钩子**、**解析 `[reply]`**、**调用 `mark_replied`**
（这三条现在都是红的，是本次要变绿的目标）。

**验收**：
- 主动消息 / 定时任务 / 普通回复三条路径发出同一个 `messages`（parity 测试）
- 三个路径都会触发 `pre_send`/`post_send`
- 三条路径落历史的文本清洗口径一致（`_clean_output_for_history`）

**风险**：**高**。这是唯一动"正在跑的业务"的任务。
**缓解**：拆成 5 个提交；每次提交后跑上面两条命令；`stream_response` 与 speech 通道**完全不动**。

**提交前缀**：`refactor(proactivity): ...`（a/b/c）、`refactor(proactivity): replace stream_send_initiative with run_turn`（d）、`refactor(loop): route generate_response through run_turn`（e）

---

## 5. P3 · 可观测与工具

### P3-1 会话历史门面 + 单例 key 修复

**问题**：`SessionManager.__new__`（`session/manager.py:265`）用**原始参数**做 key：
`AgentRuntime` 传 `str(bot_id)`，而 `module.bot_id` 是 **int**。模块写 `SessionManager(module.bot_id)`
会拿到**另一个实例**（`123` vs `"123"`），历史写进去框架读不到。

**改动**

| 文件 | 位置 | 改什么 |
|---|---|---|
| `app/agent/session/manager.py` | `__new__`（:265-272） | 首行 `bot_id = str(bot_id)`（**注意**：`_init` 里 `self.bot_id = bot_id` 现在恒为 str，检查 `create_history_store(bot_id)` 调用方是否受影响） |
| `app/plugin/base.py` | `BaseModule` | 新增 `@property def sessions(self): return self.ctx.services.sessions(self.bot_id)` |
| `app/agent/loop/prompt_prep.py` 等 | 各处 `SessionManager(str(...))` | 可保留（`str(str(x))` 幂等） |

**测试**
- 新增 `tests/agent/session/test_session_singleton.py`：
  `SessionManager(123) is SessionManager("123")`，且 `add_message` 后两边都能 `get_history` 到。
- 既有 `tests/agent/session/*`（10 个文件）全绿。

**验收**：模块 `self.sessions.add_message(session_id, "assistant", text)` 写的历史，框架下一轮能读到。

**提交**：`fix(session): key the SessionManager singleton by normalised bot id string`

---

### P3-2 框架工具集只读视图

**问题**：模块能 `@tool` **加**工具，但拿不到框架的（`expand_message` / `get_chat_history` / onebot 系列），
也无法在自己的 agent loop 里跑它们 —— `ToolSpec` / `build_tools` / `make_executor` 全在
`app/agent/tools/spec.py`（内部模块，`app/agent/__init__.py` 明确不承诺稳定）。

**改动**

| 文件 | 位置 | 改什么 |
|---|---|---|
| 新增 `app/agent/tools/catalog.py` | — | `async def framework_tools(runtime, *, session_id, is_private, event=None, bot=None, user_id=None, group_id=None, schedule_enable=False) -> tuple[list[ToolSpec], ToolContext]`：<br>内部**复用** `loop/tool_source._collect_llm_ext`（不复制那 8 段 if），返回 specs + 配套 `ToolContext` |
| `app/agent/__init__.py` | `__all__`（:47-58） | 追加 `ToolSpec`、`build_tools`、`make_executor` |
| `app/plugin/base.py` | `BaseModule` | 新增 `async def framework_tools(self, session_id=None, **kw)`：返回 `(specs, ctx)`，供 `self.llm_chat(msgs, tools=build_tools(specs), tool_executor=make_executor(specs, ctx))` |
| `app/bootstrap.py` | `ServiceAccess.tools` | 装配为 `lambda bot_id, **kw: framework_tools(...)`（P0-2 已留字段） |

**测试**
- 新增 `tests/agent/tools/test_tool_catalog.py`：断言返回的 specs 与 `_collect_llm_ext` 完全一致（防漂移）；
  断言 `make_executor(specs, ctx)` 能执行 `get_current_session` 这类无副作用工具。

**验收**：模块写 `specs, tctx = await self.framework_tools("group_123")` 能拿到框架全部可用工具并调起来。

**提交**：`feat(tools): expose framework tool catalogue to modules`

---

## 6. P4 · 可选扩展（按需，不阻塞主线）

### P4-1 块级注册 `register_block`

**用途**：模块"贡献一块"而不用整体接管 `assembly`。
**改动**：`assembler.py:496` `PromptAssembler.__init__(blocks: tuple[Block, ...] = BLOCKS)` →
增加类方法 `PromptAssembler.with_extra(*blocks)`；`AgentRuntime` 暴露 `add_context_block(block, owner)`，随模块卸载移除。
**风险**：`describe(req)` 的块对齐逻辑（`describe_blocks`）要同步；无测试护栏 → **必须先补测试**。

### P4-2 模块自建 HTTP 路由

**用途**：模块自带 `pages/index.html` 时能提供自己的数据接口（对齐 AstrBot 的 `register_web_api`）。
**改动**：`app/webui/app.py` 增加"插件路由收集器"+ `ctx.services.web_api.register(route, handler, methods, desc)`；
模块卸载时反注册。
**风险**：安全面（路由鉴权是否走 `WEBUI_TOKEN`）+ 热重载残留路由 → **必须有卸载清理测试**。

---

## 7. P5 · 文档与收尾

| 文件 | 改什么 |
|---|---|
| `docs/MODULE_DEV.md` | 新增一节「基础接口」：`send_to` / `spawn` / `sessions` / `framework_tools` / `llm_turn` / `SCHEDULES`（补上现在只存在于 `CONFIG_SCHEMA.md` 的定时声明）；新增「什么时候该写业务模块、什么时候该用这些接口」的判据表 |
| `docs/MODULE_DEV.md` 事件类型表 | **删掉 `time_core`**（已废弃，`cron_service.py:1`），补 `SCHEDULES` |
| `README.md:474` | 同上删 `time_core`；能力表补 4 个新服务 |
| `docs/FEATURE_REGISTRY.md` | 说明 8 个 feature_id 里 5 个是"关掉型"、2 个是"装槽型"，并解释为什么不给业务做槽 |
| `docs/research/pipeline-interface-audit.md` | 把 §7 的完成状态标上（已完成/已废弃） |
| `CHANGELOG.md` | 汇总一段 breaking-less 说明（全是新增） |

---

## 8. 施工清单速查表

| 编号 | 任务 | 主改文件 | 风险 | 前置 | commit |
|---|---|---|---|---|---|
| P0-1 | 出站链按注册表 | `plugin/nodes/outbound.py`、`bootstrap.py:323` | 低 | — | `fix(plugin): build outbound pipeline from node registry…` |
| P0-2 | ServiceAccess 补齐 | `plugin/base.py:209`、`bootstrap.py:222` | 低 | — | `feat(plugin): expose node registry, sessions, tools and runtime…` |
| P0-3 | capabilities 补全 | `application/capabilities.py:18` | 低 | P0-2 | `feat(application): list new module services…` |
| P0-4 | 高级配置分区下沉 | `dashboard/src/agent/sections.json` | 低 | — | `refactor(dashboard): move stream and memory config to advanced…` |
| P1-1 | `prepare_turn` 拆分 | `loop/prompt_prep.py` | 中 | — | `refactor(loop): split prepare_prompt into …turn-based entry` |
| P1-2 | `run_turn` | 新增 `loop/turn.py` | 中 | P1-1 | `feat(loop): add run_turn …` |
| P1-3 | `self.llm_turn` | `plugin/base.py` | 低 | P1-2 | `feat(plugin): add BaseModule.llm_turn …` |
| P1-4 | `services.llm.turn` | `agent/client.py` | 低 | P1-2 | `feat(agent): expose run_turn through module LLM client` |
| P2-1 | `send_to` | 新增 `domain/session.py`、`plugin/base.py` | 低 | P0-1 | `feat(plugin): add BaseModule.send_to …` |
| P2-2 | `spawn` | `plugin/base.py`、`core/task_manager.py` | 低 | P0-2、P0-3 | `feat(plugin): add BaseModule.spawn …` |
| P2-3a | 主动消息取值收敛 | `proactivity/manager.py` | 中 | P1-1 | `refactor(proactivity): reuse shared turn preparation …` |
| P2-3b | 定时任务取值收敛 | `proactivity/scheduler.py` | 中 | P1-1 | `refactor(proactivity): reuse shared turn preparation in scheduler` |
| P2-3c | 发送后收尾统一 | 上述两文件 | **高** | P2-1 | `fix(proactivity): fire send hooks, parse directives, mark replied` |
| P2-3d | 删除 `stream_send_initiative` | `proactivity/stream.py` | **高** | P1-2、P2-1 | `refactor(proactivity): replace initiative streaming with run_turn` |
| P2-3e | `generate_response` 收敛 | `loop/generate.py` | 中 | P1-2 | `refactor(loop): route generate_response through run_turn` |
| P3-1 | 会话门面 + 单例 key | `session/manager.py:265` | 中 | P0-2 | `fix(session): key singleton by normalised bot id` |
| P3-2 | 工具集视图 | 新增 `tools/catalog.py` | 低 | P1-1 | `feat(tools): expose framework tool catalogue …` |
| P4-1 | 块级注册 | `assembly/assembler.py:496` | 中 | P1-2 | `feat(assembly): allow adding context blocks` |
| P4-2 | 模块 HTTP 路由 | `webui/app.py` | **高** | P0-2 | `feat(webui): let modules register their own routes` |
| P5 | 文档 | 4 个 md | 低 | 全部 | `docs: document the module-facing pipeline interfaces` |

---

## 9. 每个阶段的闸门（Gate）

| 闸门 | 条件 |
|---|---|
| **G0** | P0 四项完成；`pytest tests/plugin tests/application tests/agent/test_agent_section_contract.py -q` 全绿；出站链可以运行时注册生效 |
| **G1** | P1 四项完成；新增 `test_run_turn.py` / `test_run_turn_parity.py` 绿；既有全量 `pytest -q` 绿 |
| **G2** | P2 五项完成；三条路径 parity 测试绿；缺陷 #4/#5/#6/#7 由红转绿 |
| **G3** | P3 两项完成；会话单例测试绿 |
| **G4** | P5 完成；`ruff check .` 无新增；文档与代码逐条对得上（用 `capabilities()` 输出核对一遍） |

---

## 10. 明确不做（写进施工文档，防止反复讨论）

| 不做 | 原因 |
|---|---|
| 把 `proactive` / `schedule` / `memory` / `knowledge` / `onebot_tools` 改成槽位控制器 | 它们是**业务**。模块要替代就用自己的 timer + `send_to` + `llm_turn`。现有 `ConfigToggleFeature` 语义（"关掉框架的"）正好够用 |
| 抽 `OutputStrategy` / `DeliveryChannel` / `ContextProvider` / `ToolSource` 注册表 | 上一版方案，过度设计。`llm_turn(stream=, send=)` 一个入口 + `post_assemble` 一个钩子已覆盖全部真实需求 |
| 动 `stream_response` / speech 发言通道内部 | 400 行、4 个既有缺陷修复刚落地、有 6 个测试护栏；只在 `prepare_turn` 层复用，不动内部 |
| 把 `stream_output` 从配置变成槽 | chat 路径仍按配置决定即可；模块/主动路径用 `run_turn(stream=...)` 显式参数 |
| 引入 `register_block` 的强制版本 | P4 可选，等真有第二个模块需要再加 |

---

## 11. 交付判据（做完这一轮，能回答用户最初的抱怨）

1. 主区只剩 4 页（基础/权限/会话/行为），流式、记忆、工具、主动+定时、输出全部在「高级」区且默认收起 → **P0-4**
2. 模块写一个"主动回复"不再需要抄框架：`self.spawn` + `self.send_to` + `self.llm_turn` 三件套 → **P1-3 / P2-1 / P2-2**
3. 同一个 LLM 回复，不管走普通回复/主动消息/定时任务，发出的 `messages`、触发的钩子、落库的文本都一致 → **P2-3 parity 测试**
4. 模块能拿到框架的工具集并自己跑 agent loop → **P3-2**
5. 所有对外接口都能在 `docs/MODULE_DEV.md` 里查到，且 `GET /api/framework/capabilities` 与代码一致 → **P5**
