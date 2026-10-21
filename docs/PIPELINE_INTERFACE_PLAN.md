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
