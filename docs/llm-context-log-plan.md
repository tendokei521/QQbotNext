# LLM 上下文与日志纵深改造方案（Q0–Q7）

> 状态：**方案稿**（未开工）。上一轮 `docs/tool-loop-refactor-plan.md` 的 P0–P6 已全部落地。
> 本文回答的是它**没覆盖**的那一半：**上下文工程**（提示词怎么长、历史怎么进、token 怎么管）
> 与**日志纵深**（请求是否可复现、事件是否自洽、压缩是否可容错）。
> 依据：`docs/tool-loop-professionalization.md`（DSH 对标）+ 本轮对 DSH 本地包与
> AstrBot / LangBot / MarshoAI / MaiBot 的源码级调研（结论见 §0.2）。
> 与 P0–P6 的关系：**不推翻任何已落地结构**，只在其上加层。

---

## 0. 判断与范围

### 0.1 一句话

**机制层已经到 DSH 同类档位；真正落后的是"上下文工程"与"日志纵深"。**
这两块不改，工具循环再专业也会被"50 条历史全塞 + 按条数压缩 + 请求不可复现"拖住。

### 0.2 差距证据（只列可核实的）

| 维度 | DSH（本地包 `~/.dsh/profiles/node_modules/@deepseek-ai/*`） | 我们（当前代码） |
|---|---|---|
| 请求来源 | `request/header` 先落日志 → `deriveMessages()` 派生 → 深冻结；**system prompt 也是日志事件** | 事件日志只记对话消息；`session_log.py:42-46` 定义了 `turn/*` `tool/*` `request/header` `usage`，**一个都没写** |
| 压缩触发 | token 压力（官方参考：trigger=pressure / context-overflow）；切点 `toolPairingBalanced`，system 节点不入范围；三段括号 `compaction/start|summary|end`，未闭合即崩溃标记；摘要复用原前缀保 KV cache | `compress.py:24-30`：`len(history) > history_rounds`（默认 50）才触发；单发摘要，失败回退，无中间状态 |
| 工具结果 | `{content:ContentBlock[], isError, error:{name,code,reason?}, meta, additionalContexts, concludesTurn}` + `presentCall/presentResult` | `ToolResult(blocks=text, is_error, code, meta)`；无 additionalContexts/concludesTurn；无呈现层 |
| 事件引用 | `tool/result` 用 `sourceEventSeqs` 回指 `tool/call`；改写走 `replace` + `contentGeneration` | 事件间无引用；`tool/call`/`tool/result` 未记录 |
| 提示词 | `sections/contexts/tools/variables` 四类 provider，中央 `SECTION_ORDERS` 定序，`complete` 段整体覆盖，`{{var}}` 插值 | `assembler.py` 固定 `BLOCK_ORDER` 12 块 + `extra_blocks`；插件要加段只能整体替换 assembler |
| 缓存 | 把 KV cache 当设计约束（原地替换 system 段会让该点之后前缀全失效；`systemPromptUpdate:'in-history'` 保前缀） | 只统计 `prompt_cache_hit_tokens`；无 `cache_control`、无稳定前缀约束 |
| 群聊历史 | （同行共识）AstrBot 用工具按需取群历史、LangBot 默认不内联历史、MaiBot 走记忆查询 | 最近 50 条**全量**塞进 prompt（`prompt_prep.py:178` + `compress.py`） |

### 0.3 不做什么（先划边界）

- 不做 DSH 的编码 agent 工具链（fs/shell/sandbox/fs-observation-policy）——QQ bot 不需要。
- 不拆成"一个关注点一个包"（DSH 100+ 包是 CLI agent 的取舍，我们是业务模块制）。
- 不引入 Milvus/Qdrant：`sqlite-vec` 够用。
- 不做多 agent 编排（`forked`/workflow）——除非出现"长任务"真实需求。

---

## 1. 全局约定（先定，后面全部引用）

### 1.1 新增配置键（6 个，全部要在 3 处登记：`config_schema.py` / `config_partition.py` / `config.py`）

| 键 | 默认 | 阶段 | 归属 | 说明 |
|---|---|---|---|---|
| `context_compress_trigger_ratio` | `0.8` | Q1 | 会话管理 → 上下文压缩 | 估算输入占模型窗口的比例超过它才压缩（0 = 退回按条数） |
| `context_compress_keep_ratio` | `0.16` | Q1 | 同上 | 保留最近多少比例原文（越小省得越多、越容易丢细节） |
| `context_compress_backend` | `summary` | Q1 | 同上 | `summary` / `prune` / `both`（prune = 只裁剪旧工具结果，不调 LLM） |
| `history_mode` | `recent` | Q2 | 会话管理 → 会话窗口 | `recent` = 只内联最近 N 条 + 按需取；`full` = 旧行为（50 条全量） |
| `history_recent_messages` | `12` | Q2 | 同上 | `recent` 模式内联多少条 |
| `prompt_cache_enable` | `True` | Q5 | 高级功能 → 模型缓存 | 是否给支持显式缓存的 provider 打 cache 标记 |

**兼容**：`history_rounds`（50）保留为"全文保留条数上限"，`context_token_budget` 保留为"发送前硬裁剪"，两者语义不变。

### 1.2 新增事件类型（写入 `history_events`，不改表结构）

| 事件 | 进 messages | payload |
|---|---|---|
| `turn/start` | ❌ | `{kind, session_id, model}` |
| `turn/end` | ❌ | `{reason, rounds, interrupted}`（`reason` 即 `TurnReason`） |
| `request/header` | ❌ | `{model, system_hash, tools:[{name, hash}], params:{temperature,max_tokens}, chain}` |
| `tool/call` | ❌ | `{call_id, name, args_raw}` |
| `tool/result` | ❌ | `{call_id, source_event_seqs:[seq], is_error, code, chars, spilled_to}` |
| `usage` | ❌ | `{prompt_tokens, completion_tokens, model}` |
| `compaction/start` · `/summary` · `/end` | ❌（summary 以 `replace` 语义投影） | `{cut_seq, kept_from_seq}` / `{text, replaced:[seq]}` / `{cut_seq}` |

> 已有常量在 `app/agent/session/session_log.py:38-46`，本节只是把它们**真正写出去**。

### 1.3 新增接口（三个，其余不动）

```python
# app/agent/assembly/compaction.py（新）
class CompactionBackend(Protocol):
    name: str
    async def compact(self, history: list[dict], *, plan: CompactionPlan) -> CompactionResult: ...

@dataclass(frozen=True)
class CompactionPlan:
    trigger_ratio: float
    keep_ratio: float
    keep_tail: int            # history_rounds 兜底
    pairing_guard: bool = True

# app/agent/assembly/sections.py（新）
@dataclass(frozen=True)
class Section:
    name: str
    order: int
    builder: Callable[["PromptRequest"], list[dict]]
    scope: str = "agent"      # agent | global
    complete: bool = False    # True = 独占整条 system prompt

def register_section(runtime, section: Section) -> None: ...
def unregister_section(runtime, name: str, *, scope: str = "agent") -> None: ...

# app/agent/tools/result.py（扩字段，不改构造）
@dataclass(frozen=True)
class ToolResult:
    blocks: tuple[dict, ...] = ()
    is_error: bool = False
    code: str = ""
    meta: dict = field(default_factory=dict)
    additional_contexts: tuple[str, ...] = ()   # 追加给模型的额外上下文
    concludes_turn: bool = False                 # 要求结束本轮（如 finish 类工具）
```

---

## 2. 阶段详表

### Q1 上下文预算与压缩（P0，3 人日）

**目标不变量**：①压缩由 **token 压力**触发而不是条数；②切点永远不切开工具配对、不切 system/环境块；
③压缩过程有**括号事件**，崩溃后能检测到"半压缩"状态；④压缩后端可换，且"裁剪旧工具结果"这一档**零 LLM 成本**。

**依赖**：无（`SessionLog` 与 `pairing.legal_cut` 已就绪）。

**改动**

1. `app/agent/assembly/context_budget.py`（新）：`usage_ratio(messages, context_window)`，
   复用 `assembly/token_budget.estimate_tokens`；窗口来源：provider 配置 `context_window`
   （缺省 0 = 不启用压力触发，回退按条数）。
2. `app/agent/assembly/compaction.py`（新）：
   - `CompactionPlan` / `CompactionResult`；
   - `SummaryBackend`：把现有 `compress.py:maybe_compress_context` 的逻辑搬进来（行为不变）；
   - `ToolResultPrunerBackend`：从旧段里把 `role:tool` 且 `chars > N` 的结果替换成
     `"[已省略 N 字符，可用 recall_tool_result 取回]"`，**不调模型**；配对守卫照旧；
   - `plan_cut()`：`legal_cut` + "环境/摘要块不入选"（按 `role=system` 且 block 来源拼接后的位置判断）。
3. `app/agent/assembly/compress.py`：`maybe_compress_context` 改为薄适配器（保留签名，
   内部走 `compaction.choose_backend(config)`）；`should_compress` 增加 token 压力分支，
   保留旧条数分支（`trigger_ratio<=0` 时）。
4. `app/agent/loop/prompt_prep.py:211`：把 `history_rounds` 与新增 ratio 一起传入；
   压缩前后 `session_log.append("compaction/start"|"/summary"|"/end")`。
5. `app/agent/session/session_log.py`：新增 `compaction/*` 常量 + `derive_messages` 的
   `replace` 折叠（summary 事件把 `replaced` 的 seq 从投影里剔除，摘要插在 keep 段前）。

**测试**（新文件 `tests/agent/assembly/test_compaction_policy.py`、`test_compaction_pruner.py`）
- 压力触发：构造 0.79 / 0.81 两份历史，只有后者压；
- 切点合法性：含 tool 对的历史，任意切点两侧都闭合（复用 `pairing` 断言）；
- pruner：只替换超阈值的工具结果，user/assistant 不碰；替换后仍可 `recall_tool_result` 取回；
- 括号：压缩中途抛异常 → 日志里有 `compaction/start` 无 `/end`，`derive_messages` 仍是原始历史（不半截）。

**提交**（3 个）：`feat(assembly): drive compaction by context pressure` /
`feat(assembly): pluggable compaction backends with a zero-LLM pruner` /
`feat(session): bracket compaction with start/summary/end events`

**验收**：`pytest tests/agent/assembly tests/agent/session -q` + 全量绿；
`grep -n "len(history) > history_rounds" app/` 只出现在回退分支。

**回滚**：`context_compress_trigger_ratio=0` → 完全回到按条数（一个配置键）。

---

### Q2 群聊历史按需化（P0，2 人日）

**目标不变量**：①默认请求只内联"最近 `history_recent_messages` 条 + 摘要/环境块"；
②更早的内容**可被模型按需取回**（工具），而不是靠"塞满"；③`history_mode=full` 时行为与今天逐字一致。

**依赖**：Q1（共用压缩/裁剪口径）。

**改动**

1. `app/agent/loop/prompt_prep.py:178` 取历史处：按 `history_mode` 截取；
   `recent` 模式下额外生成一段"更早 N 条已省略，可用 `search_history` 或 `expand_recent` 取回"的提示行，
   放在历史块末尾（不进 system，避免污染前缀）。
2. 新系统工具 `app/agent/tools/history_search.py`：`search_history(query, limit=5)`，
   在 `history_events` 上建 **FTS5** 索引（`history_events_fts`），只读本会话；返回带 `message_id`/时间/说话人。
   与 `recall_tool_result` 一样在 `tool_source` / `system.py` / `state.py` / `policy.py` 四处登记。
3. `app/agent/session/history.py`：建 FTS 表 + 触发器（append 时同步）；旧库在首次打开时建索引。
4. 配置灰度：默认 `history_mode=recent`、`history_recent_messages=12`；上线前用 X1 评测集对比。

**测试**：`tests/agent/tools/test_history_search.py`（FTS 命中、只读本会话、无索引时降级 LIKE）；
`tests/agent/loop/test_history_mode.py`（recent/full 两种请求体快照对比）。

**提交**（2 个）：`feat(tools): add search_history over the session event log` /
`feat(agent): inline recent history only, expose older turns on demand`

**风险与对策**：模型"忘记上下文" → ①提示行明确写"可查"；②X1 评测集里放 3 个"依赖 20 轮前信息"的场景，
不达标就不把默认值切过来。

---

### Q3 请求可复现与事件纵深（P1，2 人日）

**目标不变量**：①任一轮的请求头（model/tools/system 指纹/参数）可从日志重建；
②`tool/call` 与 `tool/result` 成对且可回指；③`turn/end.reason` 与遥测一致。

**依赖**：Q1（压缩事件同属这批）。

**改动**

1. `app/agent/loop/prompt_prep.py` 末尾（组装完、发请求前）：`session_log.append("request/header", ...)`；
   `system_hash = sha256(fold(system 段))`；`tools=[{name, hash(description+parameters)}]`。
2. `loop/tool_step.py`：每次工具派发前 `append("tool/call")`（带 `args_raw` 原样字符串），
   结果出来后 `append("tool/result")`，并带 `source_event_seqs=[call_seq]`。
3. `loop/turn.py` / `loop/chat.py`：`turn_started` 时 `append("turn/start")`；
   `turn_stopping` 时 `append("turn/end", reason=..., rounds=...)`；`usage_sink` 落 `usage`。
4. `app/agent/session/verify.py`：自检口径扩为"三重"——
   `(role, content)` 序列一致 **且** system 段 hash 一致 **且** tools 名称集合一致；
   不一致时 `desync-*.json` 里带三者 diff。
5. `app/agent/session/history.py`：加索引 `(bot_id, task_id, type, seq)`；无需改表。

**测试**：`tests/agent/session/test_request_replay.py`（跑一轮 → 从日志重建 messages+header，与实发一致）；
`test_tool_event_pairing.py`（每个 call 都有 result，`source_event_seqs` 指得回去）。

**提交**（3 个）：`feat(session): log request headers, turn boundaries and tool pairs` /
`feat(agent): verify requests against the log, including system and tools` /
`perf(session): index events by type for replay queries`

---

### Q4 提示词 section registry（P1，4 人日）

**目标不变量**：①插件可以**新增一段**提示词而不接管整个装配器；②段顺序由 `order` 决定、可查可测；
③`complete` 段独占整条 system；④插值失败/order 重复**装配失败并留痕**，绝不发畸形 prompt。

**依赖**：Q3（`request/header` 里的 system 指纹才有意义）。

**改动**

1. `app/agent/assembly/sections.py`（新）：`Section` / `SectionRegistry` / `register_section` /
   `unregister_section` / `resolve_sections(runtime)`（按 order 排序，agent scope 遮蔽 global）。
2. `app/agent/assembly/builtin_sections.py`（新）：把现有 12 块从 `assembler.py` 迁成 12 个
   `Section`（每个 `builder` 就是现有 `build_xxx(req)`，一行不差）。
3. `assembler.py`：`PromptAssembler.build()` 改成"解析注册表 → 逐段生成 → 折叠/清洗"，
   保留 `describe()`、`fold_system_blocks`、`extra_blocks` 语义；删掉硬编码 `BLOCK_ORDER`（迁到 builtin）。
4. `app/agent/capabilities.py`：加 `CAP_PROMPT_SECTIONS = "prompt_sections"`，
   模块可声明"我提供 section"；框架只问不猜。
5. `MODULE_DEV.md` 增 §4.7.4：怎么注册一段提示词（含 order 取值约定：0 persona / 100 规则 /
   200 记忆 / 300 背景 / 900 用户）。

**测试**：`tests/agent/assembly/test_section_registry.py`（顺序、遮蔽、complete 冲突报错、插值失败报错、
迁移后 12 块输出与改造前**逐字节一致**——用 `describe()` + 快照对比，这是本阶段的安全网）。

**提交**（3 个）：`refactor(assembly): make prompt blocks a section registry` /
`feat(agent): let modules contribute prompt sections`  /
`docs(agent): document prompt section registration`

**回滚**：`PromptAssembler` 保留旧路径（`use_registry=False`）一个版本，出问题一个开关切回。

---

### Q5 KV cache / prompt caching（P2，3 人日）

**目标不变量**：①同一会话的请求前缀**逐字节稳定**（不因 dict 顺序/时间戳抖动）；
②支持显式缓存的 provider 会打上 cache 标记；③工具集变化时用**一条**消息告知模型，而不是让 system 悄悄变。

**依赖**：Q3（header 稳定化）、Q4（段顺序稳定）。

**改动**

1. `app/agent/assembly/assembler.py`：`system` 段拼接用固定分隔符与固定顺序（已有）+
   `render_stable()`：同一输入必然同一字节，测试锁定（`hashlib.sha256` 快照）。
2. `providers/anthropic.py`：`payload["system"]` 末尾块加 `cache_control: {"type":"ephemeral"}`（可配）；
   `providers/gemini.py`：可选 `cachedContents`（首版只做 `system_instruction` 前缀）；
   `providers/openai_compat.py`：不主动加（DeepSeek/OpenAI 自动前缀缓存），但保持前缀稳定。
3. `loop/prompt_prep.py`：把上一次 `request/header.tools` 的名称集合与本次比较；
   有增删时在历史末尾追加一条 `developer`（或 `system` 尾部）**一条** 说明（"可用工具变化：+x -y"）。
4. `prompt_cache_enable` 开关 + 遥测里记录 `cache_hit/miss`（已有字段）。

**测试**：`test_prompt_prefix_stability.py`（两次同输入 hash 相同；换工具后只有尾部变化）、
`test_cache_markers.py`（anthropic payload 有 cache_control，其它 provider 没有）。

---

### Q6 工具输出表达力 + 呈现层（P2，3 人日）

**目标不变量**：①工具能返回"多模态块/引用"而不只是文本；②能声明"结束本轮"或"追加上下文"；
③调用与结果的**呈现**（给用户看什么）与**内容**（给模型看什么）解耦。

**依赖**：Q1（pruner 需要 `chars` 元数据）。

**改动**

1. `app/agent/tools/result.py`：`blocks` 支持 `{"type":"image","data"|"path","mime"}`、`{"type":"ref","label","target"}`；
   `plain()` 对非文本块渲染为 `[图片: …]` / `[引用: …]`；新增 `additional_contexts` / `concludes_turn`。
2. `app/agent/loop/tool_step.py`：结果里有 `additional_contexts` → 追加到下一轮 messages（`role=user` 的 `[工具补充]`）；
   `concludes_turn` → 停止循环并把 `StepResult.kind` 记为 `assistant`（带 reason 备注）。
3. `app/agent/tools/spec.py`：`ToolSpec` 新增 `present_call` / `present_result`（可选 callable），
   默认实现 = 今天的日志行；`delivery` 层拿它渲染给用户的"工具过程"。
4. `tools/recall.py`：返回 `blocks=[{"type":"text",...}]`（不变），但 `meta` 带 `chars/offset`，供 pruner 用。

**测试**：`test_tool_result_blocks.py`、`test_tool_concludes_turn.py`、`test_tool_presentation.py`。

---

### Q7 中间件 waterfall（P3，2 人日，按需）

**目标不变量**：插件可以在**具体一步**插入，而不是整体接管；失败语义显式（可配 fail-open/closed）。

**改动**：`app/agent/middleware.py`（新）：`runtime.middlewares`，三个点
`tool_call.before(payload) -> payload`、`tool_result.after(payload) -> payload`、
`messages.before_send(messages) -> messages`；串行按 `order`；每点可声明 `fail_closed`。
挂在 `tool_step.py`（两个点）与 `prompt_prep.py`（一个点）。

**测试**：`test_middleware_order.py`、`test_middleware_failure_policy.py`。

---

### X1 黑盒评测集（横向，3 人日，建议在 Q4 之前做）

**目标**：prompt/循环/压缩一改就能量化"是不是变差了"，而不是靠人读回复。

**改动**：`tests/eval/`（新目录）+ `tests/eval/scenarios/*.yaml`：
`{name, turns:[{user, expect_tools:[...], expect_contains:[...]}], provider_script:[...]}`；
用现成的 `_ScriptedProvider`（`tests/agent/loop/test_tool_loop_contract.py:79`）驱动，
`pytest -m eval` 单独跑（不进默认全量，避免慢）。

首批 15 个场景：依赖 20 轮前信息 / 需要 3 轮工具 / 空回复 / 思考吃满 / 被取消 / 超长工具结果 /
群聊多人交替 / 引用消息 / 表情 / 主动消息 / 定时任务。

---

## 3. 顺序、工时与"最小闭环"

```
Q1 压缩预算 ──▶ Q2 历史按需 ──▶ X1 评测集 ──▶ Q4 提示词注册表 ──▶ Q5 KV cache ──▶ Q6 输出表达力 ──▶ Q7 中间件
        └────▶ Q3 事件纵深（与 Q1 并行，压缩事件依赖它的一半约定）
```

| 阶段 | 人日 | 说明 |
|---|---|---|
| Q1 | 3 | 最高 ROI：直接省钱、直接减少"上下文爆掉" |
| Q2 | 2 | 同行共识；需要 X1 兜底再切默认值 |
| Q3 | 2 | "请求可复现"是所有后续排查的基础 |
| X1 | 3 | 给 Q2/Q4 提供回归网 |
| Q4 | 4 | 结构性改造，安全网是"12 块输出逐字节一致" |
| Q5 | 3 | 成本/延迟优化，收益取决于 provider |
| Q6 | 3 | 表达力，按业务需求排 |
| Q7 | 2 | 按需（有插件确实需要"插一脚"时再做） |
| **合计** | **22** | |

**最小闭环（约 10 人日，建议先做这四步）**：Q3 → Q1 → Q2 → X1。
理由：先把"可复现 + 可度量"立起来，再动上下文策略；Q4–Q7 是能力扩展，可以等真实需求。

---

## 4. 风险与对策

| 风险 | 对策 |
|---|---|
| Q2 切默认值后模型"忘事" | X1 里放 3 个长依赖场景；不达标只灰度给部分 bot（`history_mode` 是 per-bot 配置） |
| Q1 token 估算偏差（我们按字符启发式） | 用上游真实 `usage.prompt_tokens` 回写校准；估算只做触发，不做计费 |
| Q4 迁移 12 块时输出漂移 | 迁移安全网：`describe()` + system 全文 sha256 快照，改造前后必须逐字节一致 |
| Q5 cache 标记被某些网关拒绝 | `prompt_cache_enable` 开关 + provider 级能力声明；失败自动去掉标记重试一次 |
| Q3 事件量上涨（每轮 5+ 条） | 事件只在本地 SQLite，单条约 200B；按 `MAX_HISTORY_MESSAGES` 同量级，可接受；必要时加 TTL 清理 |
| 一次性做完 22 人日 | 按上表分批，每阶段独立提交、独立回滚（Q1 有配置开关、Q4 有 `use_registry` 开关） |

---

## 5. 每阶段的"完成"判据

1. 全量 `pytest -q` 全绿 + `ruff` 无新增；
2. 该阶段新增的**不变量测试**能通过"故意回退一处实现"变红（沿用 P0 的变异验证做法）；
3. 配置键在 3 处登记齐全（`config_schema` / `config_partition` / `config.py`），
   `sections.json` 用 `scripts/gen_sections.py` 重生成；
4. `MODULE_DEV.md` 有对应小节（插件作者不需要读源码）；
5. 若改了默认值（如 Q2 的 `history_mode`），必须附 X1 前后对比结果。
