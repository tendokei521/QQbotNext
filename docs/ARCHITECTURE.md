# QQBot Next 架构调整方案

> **状态**：方案稿，**未执行**。代码一行未改。
> **基线**：全库盘点于 2026-10-20（app/ 166 文件 46,681 行 · module/ 76 文件 6,759 行 ·
> tests/ 142 文件 · app/webui/dashboard/ 40+ 组件）。数字口径与 `STYLE.md §12` 一致（文件总行数含空行）。
> **配套**：`STYLE.md §12 已知架构债` 是"现状"，本文是"去处"；`AGENTS.md` 是"怎么提交"。

---

## 0. 怎么读这份文档

| 你想干的事 | 读哪一节 |
|---|---|
| 判断这次改动会影响多大范围 | §2.3 一页表 |
| 查某个文件该搬到哪 | §3 逐文件去向（按目录分表） |
| 知道改一个文件要跑哪些测试 | §6 改 X 该读什么 |
| 决定先做哪一步 | §5 分阶段计划 |
| 知道某件事该写进哪份文档 | §4 准一来源矩阵 |
| 想知道哪些**不做** | §8 |

---

## 1. 诊断：三条结论

### 结论一：不是"整体扁平"，是"一层塌陷 + 几层空壳"

`app/` 顶层已经分了 9 个目录，包级依赖方向也基本正确（`infrastructure` 不 import `llm`、
`domain` 不 import 任何人）。真正的问题只有一个：

**`app/agent/` 顶层平铺 55 个 .py / 20,916 行，加上 6 个子包共 91 文件 / 32,900 行，
占 `app/` 的 70%。** 这一个目录里塞了至少 11 类互不相关的职责（见 §3.1 分类）。
与之对照，本该承载业务规则的 `app/domain/` 只有 4 个文件 595 行，全是 DTO。

所以"分工不明确"的准确表述是：**该厚的层（domain）是空壳，该薄的层（llm）塌成一坨。**

### 结论二：真正的耦合不是"文件多"，是"依赖图有一个跨层的大环"

AST 实测（含函数内惰性 import）求强连通分量。只统计模块层 import 时，环看起来是
**24 文件 / 12,112 行**；把函数内惰性 import 也算进去（`ast.walk` 覆盖函数体），
真实规模是它的两倍以上——`app.agent.loop.chat` 与 `app.bootstrap`、
`app.core.container`、`app.webui.api.*` 全在**同一个环**里。

**这个环是唯一必须动手术的地方，其他都是搬运。**

> **已修（P0–P4）**：**全库 import 图现在是 DAG，0 个强连通块（278/278 无环）**。
> `app.bootstrap` 已出环，`chat.py` 可达闭包从 158 文件 / 41,717 行 降到
> **87 文件 / 23,721 行**。七条反向边的成因与修法见 §5.2。

### 结论三：文档跑在了代码前面，对 Agent 比对人更危险

`STYLE.md` 曾写着「函数尽量 ≤ 50 行」（实际 41 个函数 ≥ 80 行，最大 378）、
「插件目录结构固定 `{module.py, service.py, config_schema.py}`」（13 个插件 7 个不符）、
「函数内惰性导入**允许且鼓励**」（全库 315 处，是环的直接成因）。
人读到会怀疑并偏离；**AI 会严格执行**。已在 `STYLE.md` 修正（见 §4）。

---

## 2. 目标架构

### 2.1 顶层总图

```
app/
├── bootstrap/       装配与环境：只回答"谁在什么时候被创建、按什么顺序启动"
├── core/            与业务无关的内核：settings / container / logger / task_manager / event_bus
├── domain/          纯规则：无 IO、无网络、无 DB、全可单测（现状几乎为空，本方案把它长实）
├── infrastructure/  一切外部 IO：SQLite / 文件 / OneBot 网关 / HTTP / 配置落盘
├── agent/           LLM Agent 能力层（原 app/llm，内部按职责分包）
├── plugin/          插件框架：BaseModule / 注册表 / 权限 / hook / 节点链（原 app/plugin + app/nodes）
├── application/     面向 WebUI 的能力编排（原 app/application）
└── webui/           纯 HTTP 适配：api / schemas / ws / 唯一前端 dashboard

plugins/             业务插件（原 module/）：builtin/ 内置 + installed/ 外部 zip 安装
data/                运行时数据：app.db / llm/ / plugins/ / configs/（迁移源）
tests/               按 app 的分区镜像（见 §3.7）
scripts/             release.py + env 脚本 + experiments/（消融实验）
docs/                设计文档 + 本方案（见 §4）
```

**一句话记忆**：`domain 定规则 → infrastructure 碰世界 → agent 跑 Agent → plugin 挂扩展
→ application 编排给前端 → webui 只做 HTTP`。

### 2.2 唯一允许的依赖方向

```
                    ┌───────────────────────────────┐
                    │   bootstrap（装配根，谁都不依赖它）│
                    └───────────────┬───────────────┘
                                    │ 只允许向下注册
        ┌───────────────┬───────────┴────────┬────────────────┐
        ▼               ▼                    ▼                ▼
    webui/ ────────► application/ ────────► agent/ ────────► plugin/
        │               │                    │                │
        └───────────────┴────────────────────┴────────────────┘
                                    │
                                    ▼
                         infrastructure/ ─────► domain/
                                    │                ▲
                                    └────────────────┘
                    core/ 可被任何层依赖（但不得依赖任何层）
```

**硬规则（可静态校验，见 §7）**：

1. `domain/` **不得** import `app.*` 中除 `core/`（仅 typing/常量）以外的任何模块。
2. `infrastructure/` **不得** import `agent/`、`plugin/`、`application/`、`webui/`。
3. `webui/` **不得** import `agent/` 内部实现，只能 import `application/` 的公开门面
   （这是拆掉 24 文件环的关键——现状 `webui/api/*` 有 6 个文件在环内）。
4. `plugin/` **不得** import `agent/` —— 框架不认识 Agent；Agent 通过 `features.py` 的
   FeatureController 反向挂载。现状是互引（`llm/config.py`、`llm/image_media.py` ↔ `features.py`）。
5. **禁止** `from app.bootstrap import ...`（含 `get_container()`）。
   依赖必须经构造函数或 FastAPI `Depends`（`app/webui/api/deps.py`）。
6. **新增**函数内惰性 import 一律需要行内注释说明原因（`STYLE.md §6`）。

### 2.3 一页表：改动的爆炸半径

| 你要改的东西 | 现在要读 | 改完后要读 | 主要障碍（现状） |
|---|---|---|---|
| 加/改一个 provider | 3 文件 1,122 行 | 3 文件 ~1,200 行 | 已最好，仅需把 `request_policy` 并进来 |
| 改提示词段落 | 8 文件 2,285 行 | 3 文件 ~1,300 行 | `persona_rules` 已隔离，`assembly` 还太大 |
| 改会话历史 | 7 文件 2,045 行 | 4 文件 ~1,400 行 | `history` 里混了 SQLite |
| 加一条 OneBot 工具 | 1 文件 4,792 行 | 2 文件（json + 校验） | 清单是 4,792 行字面量 |
| 改"该不该回复" | **143 文件 42,082 行** | 3 文件 ~600 行 | 判定散在 `chat.py` / `trigger.py` / `proactive.py` |
| 改流式输出行为 | **143 文件 42,082 行** | 4 文件 ~1,800 行 | `stream_response()` 单个函数 378 行 |
| 改 WebUI 某个接口 | **143 文件 42,082 行** | 2 文件 ~500 行 | 65 次 `get_container()` 直调把它焊进环 |
| 加一个业务插件 | 模板 1 个 + 框架若干 | 同左（已足够清晰） | 仅命名撞车需消除 |

**验收指标**（`STYLE.md §12`）：任何入口文件的可达闭包 **≤ 8,000 行**。
现在 `chat.py` = 42,082；目标 ≤ 6,000。

---

## 3. 逐文件去向

> 约定：**动作**列 = `移动`（只改路径与 import）/ `拆分`（需要动代码结构）/
> `外置`（数据搬到 JSON）/ `合并` / `删除` / `新建`。
> 只写 `移动` 的批次可以脚本化、零逻辑变更、风险最低；`拆分` 必须有测试护着再做。

### 3.1 `app/agent/` → `app/agent/`（91 文件，本方案的主体）

改名理由：`app/agent/` 里现在装着 `scheduler`（定时任务）、`image_media`（本地图片仓）、
`qq_faces`（表情表）、`emoji_lexicon`（emoji 映射）——名字说它是"大模型层"，实际它是
"整个 Agent 的行为层"。改名后 `llm` 不再被误读为"只跟模型有关"。

#### 3.1.1 目标分包（11 个，每包一句话）

| 目标包 | 一句话职责 | 文件数 | 约行数 |
|---|---|---|---|
| `agent/loop/` | 一轮对话从入口到产出：准备→生成→流式→收尾 | 7 | ~2,600 |
| `agent/assembly/` | 把原料组装成最终 `messages`（含压缩、预算、落盘对账） | 8 | ~2,200 |
| `agent/persona/` | 提示词段落与人格：说什么/怎么说/不许做什么 | 7 | ~1,700 |
| `agent/context/` | 群语境、指代消解、焦点、昵称与人名 | 7 | ~2,300 |
| `agent/session/` | 会话与历史：存什么、怎么渲染、怎么补全 | 6 | ~2,800 |
| `agent/tools/` | 工具注册、执行、权限过滤（含 onebot/mcp/web/context/session 工具） | 9 | ~2,700 |
| `agent/delivery/` | 决定怎么发出去：切分、节奏、有序池、发言通道、指令渲染 | 6 | ~1,100 |
| `agent/media/` | 图片/动图/语音的获取、落盘、转成 provider 能吃的格式 | 4 | ~1,800 |
| `agent/proactivity/` | 主动消息与定时任务 | 4 | ~2,000 |
| `agent/providers/` | 厂商适配 + 一次请求该长什么样（唯一决策点） | 13 | ~3,100 |
| `agent/config/` | Agent 配置默认值 + 表单 schema（schema 外置为 JSON） | 3 | ~600 + json |
| （保留）`agent/memory/` `agent/knowledge/` `agent/group_log/` | 已有子包**结构正确**，只搬存储部分 | 18 | ~3,900 |

#### 3.1.2 逐文件去向

| 现文件 | 行 | 目标 | 动作 | 备注 |
|---|---|---|---|---|
| `llm/__init__.py` | 162 | `agent/__init__.py` | 拆分 | `llm_data_dir/safe_bot_id/bot_data_dir` → `infrastructure/fs/paths.py`；`migrate_legacy_data` → `infrastructure/fs/migrate.py`；`__init__` 只留再导出 |
| `llm/chat.py` | 2192 | 见 §3.1.3 | **拆分** | 全库最大单文件，拆成 5+ 个 |
| `llm/pipeline.py` | 582 | `agent/loop/pipeline.py` | 移动 | |
| `llm/manager.py` | 433 | `agent/runtime.py` | 移动 | `AgentRuntime`/`AgentManager` 是 agent 的门面，放包根 |
| `llm/context.py` | 51 | `agent/loop/context.py` | 移动 | `LlmContext`/`LlmJob` |
| `llm/locks.py` | 75 | `agent/loop/locks.py` | 移动 | |
| `llm/pool.py` | 117 | `agent/loop/pool.py` | 移动 | 防抖/合并 |
| `llm/tool_loop.py` | 171 | `agent/loop/tool_loop.py` | 移动 | |
| `llm/assembly.py` | 790 | `agent/assembly/assembler.py` | **拆分** | 30+ 顶层符号；把 `resolve_persona_preset/build_persona/persona_begin_dialogs` 移到 `persona/`，`build_schedule_instruction` 移到 `proactivity/` |
| `llm/assembly_inputs.py` | 62 | `agent/assembly/inputs.py` | 移动 | |
| `llm/prompt.py` | 381 | `agent/assembly/prompt.py` | 移动 | |
| `llm/token_budget.py` | 234 | `agent/assembly/token_budget.py` | 移动 | 纯计算部分可再上提 `domain/conversation/budget.py` |
| `llm/compress.py` | 125 | `agent/assembly/compress.py` | 移动 | 依赖 LLM，不能进 domain |
| `llm/prompt_dump.py` | 178 | `agent/assembly/dump.py` | 移动 | 文件 IO 收敛到 `infrastructure/fs/dump.py` |
| `llm/enhance.py` | 315 | `agent/assembly/enhance.py` | 移动 | 用户信息感知注入属装配层 |
| `llm/persona_rules.py` | 500 | `agent/persona/rules.py` | 移动 | |
| `llm/skills.py` | 141 | `agent/persona/skills.py` | 移动 | |
| `llm/tags.py` | 70 | `agent/persona/tags.py` | 移动 | |
| `llm/emoji_lexicon.py` | 207 | `agent/persona/emoji.py` | 移动 | 词表数据可外置 json |
| `llm/qq_faces.py` | 127 | `agent/persona/data/qq_faces.py` | 移动 | **生成物**（`scripts/gen/export_napcat_faces.py` 产出），加 `# generated` 头 |
| `llm/group_context.py` | 684 | `agent/context/group.py` | 移动 | |
| `llm/referent.py` | 314 | `agent/context/referent.py` | 移动 | |
| `llm/focus.py` | 248 | `agent/context/focus.py` | 移动 | |
| `llm/nicknames.py` | 135 | `agent/context/nicknames.py` | 移动 | |
| `llm/display_names.py` | 435 | `agent/context/display_names.py` | 移动 | |
| `llm/context_tools.py` | 1059 | `agent/tools/context_tools.py` | **拆分** | 40 个顶层符号；`build_context_tools` 留此，取数 helper 移 `agent/context/` |
| `llm/session.py` | 713 | `agent/session/manager.py` | 移动 | |
| `llm/history.py` | 502 | `agent/session/history.py` + `infrastructure/persistence/history_repo.py` | **拆分** | `SQLiteHistoryStore` 移入 infra；`HistoryManager` 留 agent |
| `llm/history_model.py` | 676 | `agent/session/model.py` | 移动 | 纯渲染器，可单测 |
| `llm/history_enrich.py` | 395 | `agent/session/enrich.py` + `infrastructure/fs/enrich_store.py` | **拆分** | 现在自己 open/write，改走 infra |
| `llm/session_tools.py` | 500 | `agent/tools/session_tools.py` | 移动 | |
| `llm/tool.py` | 414 | `agent/tools/spec.py` | 移动 | `ToolSpec`/`ToolContext`/执行器 |
| `llm/system_tools.py` | 126 | `agent/tools/system.py` | 移动 | |
| `llm/hooks.py` | 169 | `agent/hooks/registry.py` | 移动 | |
| `llm/mcp.py` | 256 | `agent/tools/mcp.py` | 移动 | |
| `llm/tavily_search.py` | 185 | `agent/tools/web_search.py` | 移动 | |
| `llm/module_client.py` | 260 | `agent/client.py` | 移动 | 对插件暴露的 `ctx.services.llm` |
| `llm/speech.py` | 444 | `agent/delivery/speech.py` | 移动 | 发言通道（outbox + 工具） |
| `llm/send_pool.py` | 181 | `agent/delivery/pool.py` | 移动 | |
| `llm/send_policy.py` | 47 | `agent/delivery/pace.py` | 移动 | 与 `send_pool` 的区别写在 docstring |
| `llm/splitter.py` | 56 | `agent/delivery/splitter.py` | 移动 | |
| `llm/actions.py` | 168 | `agent/delivery/directives.py` | 移动 | 指令→OneBot 段 |
| `llm/image.py` | 632 | `agent/media/image.py` | 移动 | |
| `llm/image_media.py` | 618 | `agent/media/store.py` + `infrastructure/fs/media.py` | **拆分** | 本地仓的路径/落盘移 infra |
| `llm/animated.py` | 139 | `agent/media/animated.py` | 移动 | |
| `llm/proactive.py` | 520 | `agent/proactivity/manager.py` | **拆分** | 判定规则上提 `domain/initiative/policy.py` |
| `llm/initiative_stream.py` | 196 | `agent/proactivity/stream.py` | 移动 | |
| `llm/scheduler.py` | 967 | `agent/proactivity/scheduler.py` + `domain/scheduling/` | **拆分** | `time_parser` 上提 domain；`TaskEntry`→`infrastructure/persistence/schedule_repo.py`；工具入口移 `agent/tools/schedule.py` |
| `llm/time_parser.py` | 312 | `domain/scheduling/time_parser.py` | 移动 | **纯函数无 IO，正适合 domain** |
| `llm/trigger.py` | 49 | `domain/initiative/trigger.py` | 移动 | 纯判定（@/关键词） |
| `llm/request_policy.py` | 615 | `agent/providers/policy.py` | 移动 | docstring 自述"provider 层的唯一决策点"，本就在 provider 层 |
| `llm/commands.py` | 187 | `agent/commands/registry.py` | 移动 | `#llm` 指令总表 |
| `llm/telemetry.py` | 202 | `agent/telemetry.py` | 移动 | |
| `llm/config.py` | 527 | `agent/config/defaults.py` + `agent/config/facade.py` | **拆分** | `DEFAULT_LLM_CONFIG` 与 `AgentConfig` 门面分开 |
| `llm/config_schema.py` | 1072 | `agent/config/schema.json` + `agent/config/schema.py` | **外置** | 1,072 行字面量 → JSON；只留 `_on/_gate/_group_fields` 三个 helper |
| `llm/storage/base.py` | 69 | — | **删除** | 与 `infrastructure/persistence` 重复，零引用 |
| `llm/storage/__init__.py` | 47 | — | **删除** | 上面两个工厂函数无人调用 |
| `llm/providers/*` (12) | 2,528 | `agent/providers/*` | 移动 | **本方案唯一"原样保留"的子包**，作为其余部分的范本 |
| `llm/memory/*` (8) | ~2,100 | `agent/memory/*` | 移动 | `store.py` 的 SQLite 移 `infrastructure/persistence/memory_repo.py` |
| `llm/knowledge/*` (5) | ~700 | `agent/knowledge/*` | 移动 | `store.py` / `vector.py` 的 SQLite 移 infra |
| `llm/group_log/*` (5) | ~1,050 | `agent/group_log/*` | 移动 |**结构已正确**，仅 `store.py` 的 JSONL 落盘走 `infrastructure/fs` |
| `llm/onebot_tools/manifest.py` | 4792 | `agent/tools/onebot/manifest.json` | **外置** | 纯数据；配 pydantic 校验 + `scripts/gen_onebot_manifest.py` |
| `llm/onebot_tools/security.py` | 81 | `domain/tool_policy/policy.py` | 移动 | risk/permission/scope 是**纯规则** |
| `llm/onebot_tools/tools.py` | 313 | `agent/tools/onebot/executor.py` | 移动 | |
| `llm/onebot_tools/__init__.py` | 8 | `agent/tools/onebot/__init__.py` | 移动 | |

#### 3.1.3 `chat.py` 专项拆分（2,192 行 → 8 个文件）

这是全库最贵的一个文件：49 个顶层符号，`stream_response()` 单函数 378 行、
`prepare_prompt()` 256 行、`handle_commands()` 218 行。按"输入的来源 / 输出去哪"切：

| 新文件 | 承接 | 约行数 | 判据（改什么来这里） |
|---|---|---|---|
| `agent/loop/entry.py` | `handle()` `_handle_command_event()` | ~120 | 消息进 Agent 的总入口 |
| `agent/loop/prepare.py` | `prepare_prompt()` `_fetch_pre_history()` `_collect_llm_ext()` `_context_expand_flags()` | ~450 | "本轮要喂给模型什么原料" |
| `agent/loop/generate.py` | `generate_response()` | ~250 | 非流式一轮 |
| `agent/loop/stream.py` | `stream_response()` | ~400 | 流式一轮（含工具循环、flush） |
| `agent/loop/output.py` | `_segments_as_dicts()` `_images_enabled()` `_clean_output_for_history()` `_log_debug_prompt()` `_served_model_label()` `_event_nickname()` | ~200 | 模型的输出怎么清洗、怎么记账 |
| `agent/providers/policy.py` | `_max_tool_rounds()` `_max_empty_retries()` `_reasoning_overflow_retries()` `_turn_budget()` `_effective_max_tokens()` `_max_tokens_override_note()` `_provider_config_for()` `_provider_chain_for()` `_modalities_for_chain()` | 并入现文件 | **请求参数与重试策略**——它们本属于 `request_policy` 的"唯一决策点" |
| `agent/delivery/speech.py` | `_build_speech()` `_speech_intent()` `_speech_fallback_enabled()` `_should_force_speak()` `_flush_speech()` `_force_speak_round()` | 并入现文件 | 发言相关判定与执行 |
| `agent/session/` `agent/memory/` `agent/context/` `agent/media/` `agent/commands/` | `_commit_history_enrichment` `_format_session_history` `_history_meta_flags` / `_memory_block` `_memory_autosave` `_memory_consolidate` / `_build_group_pre_history` `_referent_block` / `_append_round_images` `_history_media_hook` / `handle_commands` | 各并入 | 按归属分散，`chat.py` 只剩编排骨架 |
| `agent/tools/initiative.py` | `build_initiative_tools()` `_proactive_instruction()` | ~120 | 主动消息用的工具集 |

**拆完的效果**：`stream_response` 的 378 行会被拆成「拿预算 → 调 provider 流 →
逐 chunk 交 `delivery/splitter` → 工具回合走 `loop/tool_loop` → flush 走 `delivery/speech`」，
每一段都能单独测。

#### 3.1.4 数据外置（3 个文件，砍掉 5,864 行"代码"）

| 现文件 | 行 | 目标 | 配套 |
|---|---|---|---|
| `llm/onebot_tools/manifest.py` | 4,792 | `agent/tools/onebot/manifest.json` | `schemas/onebot_manifest.schema.json`（pydantic）+ `scripts/gen_onebot_manifest.py` 反向生成 |
| `llm/config_schema.py` | 1,072 | `agent/config/schema.json` | `schemas/agent_config.schema.json` |
| `llm/qq_faces.py` | 127 | `agent/persona/data/qq_faces.json` | `scripts/gen/export_napcat_faces.py` 已能生成，改输出 json |

> 收益：改一条工具描述从"在 4,792 行 diff 里找"变成"改 json 一行"，
> 而且**校验能在 CI 里跑**（现在纯字面量，字段漏了只有运行时才发现）。

### 3.2 `app/core/` —— 基本不动（只有一处必删）

| 现文件 | 行 | 动作 | 说明 |
|---|---|---|---|
| `core/__init__.py` | 49 | **精简** | 现在 re-export 了 logger/container/event_bus/settings/task_manager 全部，导致 `import app.core` 拉进所有内核模块。改为只导出类型，具体模块按需 import |
| `core/container.py` | 97 | **删 `service()`** | 零引用死代码，唯一作用是 `from app.bootstrap import get_container` 这条反向边——**它把 DI 容器本身拖进了 24 文件环**。连带删除变为未使用的 `cast` 导入 |
| `core/event_bus.py` | 86 | 保留 | — |
| `core/logger.py` | 345 | 保留 | 日志本身是 IO 出口，允许直接写文件 |
| `core/settings.py` | 83 | 保留 | — |
| `core/task_manager.py` | 114 | 保留 | — |

### 3.3 `app/domain/` —— 从 595 行长到约 1,800 行（本方案的重点之一）

**关键发现**：`app/agent/` 的 91 个文件里有 **29 个是零内部依赖的"纯"文件（9,849 行，占 30%）**——
它们根本不需要在 Agent 层，其中至少 7 个就是本该在 `domain/` 的业务规则。

| 现文件 / 新建 | 行 | 动作 | 判据 |
|---|---|---|---|
| `domain/bot.py` | 172 | → `domain/model/bot.py` | 接口 |
| `domain/message.py` | 91 | → `domain/model/message.py` | 消息模型 |
| `domain/events.py` | 300 | **保留** | 事件契约，是跨层公共语言 |
| `domain/__init__.py` | 32 | 保留 + 加子包 | — |
| `llm/time_parser.py` | 312 | → `domain/scheduling/time_parser.py` | **实测零内部依赖**（只用 calendar/re/datetime） |
| `llm/scheduler.py` 的 `_next_at` 计算 | — | → `domain/scheduling/recurrence.py` **新建** | 下次触发时间/重复规则是纯计算 |
| `llm/trigger.py` | 49 | → `domain/initiative/trigger.py` | **实测零内部依赖**（只用 typing） |
| `llm/proactive.py` 的判定段 | — | → `domain/initiative/policy.py` **新建** | "此刻该不该主动说话"是纯规则 |
| `llm/onebot_tools/security.py` | 81 | → `domain/tool_policy/policy.py` | **实测零内部依赖**；risk/permission/scope 是纯规则 |
| `llm/persona_rules.py` 的 `detect_caps` 段 | — | → `domain/persona/capabilities.py` **新建** | 能力探测→段落取舍的映射 |
| `llm/splitter.py` | 56 | → `domain/delivery/splitter.py` | 实测纯（只用 re） |
| `llm/send_policy.py` | 47 | → `domain/delivery/pace.py` | 实测纯（只依赖 `app.domain.message`，**全库唯一已正确使用 domain 的例子**） |
| `llm/tags.py` | 70 | → `domain/persona/tags.py` | 实测纯（只用 re） |
| `llm/providers/modalities.py` | 159 | → `domain/model/modalities.py` | 实测纯（copy/typing） |
| `llm/token_budget.py` 的估算段 | — | → `domain/conversation/budget.py` **新建** | 字符→token 估算与裁剪顺序，纯算术 |
| `llm/compress.py` 的 `should_compress` | — | → `domain/conversation/policy.py` **新建** | 判定纯、调用带 LLM（判定进 domain、调用留 agent） |
| `llm/history_model.py` | 676 | 保留在 `agent/session/` | 虽是纯渲染器，但与 Agent 的历史格式强绑定，留 agent 更易找 |

> **纪律**：`domain/` 只准 import `core/` 与 `domain/` 自身。
> 若发现需要 import `agent/`，说明这条规则还不纯，先留在 `agent/`。

### 3.4 `app/infrastructure/` —— 从 21 文件涨到约 33 文件（成为唯一 IO 出口）

目标：**全库只有这里能出现 `sqlite3` / `open(...,"w")` / `httpx` / `Path.write_text`。**

| 现文件 | 行 | 动作 | 说明 |
|---|---|---|---|
| `infrastructure/persistence/database.py` | 189 | 保留 | |
| `infrastructure/persistence/base_repo.py` | 22 | 保留 | **终于会被真正使用**（现状零引用） |
| **新建** `persistence/history_repo.py` | ~200 | 从 `llm/history.py::SQLiteHistoryStore` | |
| **新建** `persistence/memory_repo.py` | ~350 | 从 `llm/memory/store.py` 的 SQL 部分 | |
| **新建** `persistence/knowledge_repo.py` | ~200 | 从 `llm/knowledge/store.py` + `vector.py` | |
| **新建** `persistence/schedule_repo.py` | ~120 | 从 `llm/scheduler.py::TaskEntry` 持久化 | |
| **新建** `fs/paths.py` | ~60 | 从 `llm/__init__.py` 的 `llm_data_dir/safe_bot_id/bot_data_dir` | |
| **新建** `fs/migrate.py` | ~100 | 从 `llm/__init__.py` 的 `migrate_legacy_data` | |
| **新建** `fs/media.py` | ~250 | 从 `llm/image_media.py` 的路径/落盘 | |
| **新建** `fs/enrich_store.py` | ~150 | 从 `llm/history_enrich.py` 的 `_load/_save` | |
| **新建** `fs/dump.py` | ~100 | 从 `llm/prompt_dump.py` 的读写 | |
| **新建** `fs/group_log.py` | ~200 | 从 `llm/group_log/store.py` 的 JSONL 追加 | |
| `infrastructure/config/config_service.py` | 823 | **拆分** | 值存取 `config_values.py` / 建表与迁移 `config_repo.py` / 门面 `config_service.py` |
| `infrastructure/onebot/gateway.py` | 817 | **拆分** | 连接 `gateway.py` / 事件解析 `gateway_events.py` / 派发 `gateway_dispatch.py` |
| `infrastructure/onebot/client.py` | 583 | 保留 | |
| `infrastructure/onebot/codec.py` | 194 | 保留 | |
| `infrastructure/cache.py` | 91 | 保留 | |
| `infrastructure/curl_cffi.py` | 148 | 保留 | |

### 3.5 `app/plugin/` + `app/nodes/` → `app/plugin/`（消除三重撞车）

**现状（P5 后）**：只剩两个 "module" 概念，且分工已清晰——

| 目录 | 是什么 | 谁改 |
|---|---|---|
| `app/plugin/` | 插件**框架**（加载/分发/权限/API） | 改框架才动 |
| `app/plugins/` | 13 个**业务插件**代码 | 加/改业务功能动 |
| `module/` | 插件**运行时数据**（configs / data / zip 安装） | 用户数据，不动 |

原来的 `module/modules`（业务代码）已搬进 `app/plugins/`；`module/plugins`（zip 安装）
保留原地。剩下仍值得做的：把 `app/plugin/` 改名 `app/plugin/`（单数=框架），
顺带把 `app/nodes/` 并进 `plugin/nodes/`，这样 `modules` 与 `module` 的字面混淆彻底消失。
下表是这一步的逐文件去向（**尚未执行**）：

| 现文件 | 行 | 目标 | 动作 |
|---|---|---|---|
| `modules/__init__.py` | 116 | `plugin/__init__.py` | 精简：只导出 `BaseModule`/`ModuleRegistry`/hook 装饰器 |
| `modules/base.py` | 468 | `plugin/base.py` + `plugin/context.py` | **拆分**：`ModuleConfig`/`ServiceAccess`/`ModuleContext` 移 `context.py` |
| `modules/registry.py` | 683 | `plugin/registry/{scan,load,reload}.py` | **拆分**：扫描/加载/热重载三段，`load_single()` 仍是长函数 |
| `modules/authority.py` | 200 | `plugin/authority.py` | 移动 |
| `modules/dispatcher.py` | 61 | `plugin/dispatcher.py` | 移动 |
| `modules/features.py` | 538 | `plugin/features/{registry,controllers/*}.py` | **拆分**：14 个 `*FeatureController` 挤在一个文件，每个一个文件 |
| `modules/hooks.py` | 558 | `plugin/hooks/{registry,decorators,contexts}.py` | **拆分** |
| `modules/api.py` | 96 | `plugin/api.py` | 移动 |
| `modules/nodes.py` | 248 | `plugin/nodes/inbound.py` | 移动 |
| `modules/groups.py` | 110 | `plugin/helpers/groups.py` | 移动 |
| `modules/keyword.py` | 87 | `plugin/helpers/keyword.py` | 移动 |
| `modules/requirements.py` | 48 | `plugin/requirements.py` | 移动 |
| `modules/resource_validation.py` | 80 | `plugin/resource_validation.py` | 移动 |
| `modules/schema_utils.py` | 119 | `plugin/schema_utils.py` | 移动 |
| `nodes/base.py` | 72 | `plugin/nodes/base.py` | 移动 |
| `nodes/registry.py` | 57 | `plugin/nodes/registry.py` | 移动 |
| `nodes/outbound.py` | 46 | `plugin/nodes/outbound.py` | 移动 |
| `nodes/__init__.py` | 9 | — | **删除**（并入 `plugin/nodes/__init__.py`） |

> `modules/media_cache.py` 已在 P3 下沉到 `app/infrastructure/fs/media_cache.py`（✅ 完成）。

### 3.6 `app/application/` → `app/application/`（顺带解两处撞名）

| 现文件 | 行 | 目标 | 动作 | 说明 |
|---|---|---|---|---|
| `services/__init__.py` | 6 | `application/__init__.py` | 精简 | |
| `bot_service.py` | 252 | `application/bot_service.py` | 移动 | |
| `capabilities.py` | 87 | `application/capabilities.py` | 移动 | |
| `config_profile_service.py` | 86 | `application/config_profile.py` | 移动 | |
| `log_service.py` | 69 | `application/log_service.py` | 移动 | |
| `module_install_service.py` | 85 | `application/plugin_install.py` | 移动 | |
| `provider_model_service.py` | 344 | `application/provider_model.py` | 移动 | |
| `provider_preset_service.py` | 326 | `application/provider_preset.py` | 移动 | |
| `provider_service.py` | 124 | `application/datasource.py` | **改名** | 它不是"provider 预设"，是**配置表单的动态下拉数据源**（builtin_groups/builtin_friends）。撞名严重 |
| `resource_registry.py` | 153 | `application/resource_registry.py` | 移动 | |
| `role_preset_service.py` | 154 | `application/role_preset.py` | 移动 | |
| `scheduler.py` | 230 | `application/daily_scheduler.py` | **改名** | 与 `agent/proactivity/scheduler.py` 同名不同物，必须区分 |
| `session_history_service.py` | 557 | `application/session_history.py` | 移动 | 在 24 文件环内，改完注入后应出环 |

### 3.7 `app/webui/` —— 拆掉环 + 收敛成单一前端

| 现文件 | 行 | 动作 | 说明 |
|---|---|---|---|
| `webui/app.py` | 375 | `webui/app.py` + `webui/startup.py` | **拆分**：路由挂载 / 生命周期监听 / 鉴权中间件三分 |
| `webui/api/deps.py` | 24 | **扩张** | 为每个 application service 提供 `Depends`；这是替换 65 次 `get_container()` 的落点 |
| **新建** `webui/api/_responses.py` | ~30 | 统一 `_ok()` / `_err()` | 现在 **9 个 api 文件各写了一份**（agent/bots/config_profiles/media/modules/provider_presets/role_presets/sessions/webui_cfg） |
| `api/agent.py` | 435 | 保留 + **注入改造** | 17 次 `get_container` |
| `api/modules.py` | 539 | 保留 + **注入改造** | 17 次；并在环内 |
| `api/sessions.py` | 378 | 保留 + **注入改造** | 在环内 |
| `api/bots.py` | 154 | 保留 + 注入改造 | 10 次 |
| `api/webui_cfg.py` | 163 | 保留 + 注入改造 | 11 次 |
| `api/media.py` | 132 | 保留 + 注入改造 | 在环内 |
| `api/provider_presets.py` | 183 | 保留 + 注入改造 | |
| `api/config_profiles.py` `api/logs.py` `api/prompt_dump.py` `api/resources.py` `api/role_presets.py` `api/framework.py` | 78–175 | 保留 + 注入改造 | |
| `webui/schemas.py` | 48 | 保留 | |
| `webui/ws.py` | 98 | 保留 | |
| `webui/static/js/main.js` | 74 KB | — | **删除**（见下） |
| `webui/static/js/widgets.js` | 36 KB | — | **删除** |
| `webui/static/css/style.css` | 37 KB | — | **删除** |
| `webui/templates/index.html` | 46 KB | — | **删除** |
| `app/webui/dashboard/` → `webui/dashboard/` | 40+ 组件 | 移动 | 前端源码与构建产物收进 webui，`dist/` 同目录；`app.py` 里 `_render_dashboard` 的路径同步 |

**前端收敛的依据**（不是拍脑袋删）：
`app/webui/dashboard/dist` 已存在并且优先渲染；`/legacy` 只是回退；模块自带页面走的是
`GET /api/module/{name}/page` 独立通道（只有 `app/plugins/example/pages/` 一个用户），
**不依赖 `main.js`/`widgets.js`**。删掉旧版是净收益：**消掉 ~195 KB 与"同一张表单写两遍"的长期税**。

### 3.8 `module/modules/` → `app/plugins/`（业务插件，13 个）—— ✅ 已执行（P5）

先给结论：**这 13 个插件的内部结构总体是对的，不需要重构**，只需统一位置。

已落地：`module/modules/*` → `app/plugins/*`（代码跟着代码树走），
`module/` 只留运行时数据（`configs/` `data/` `plugins/` `uninstalled_modules.json`），
**用户数据零迁移**。

配套的关键改造：插件加载器从「硬编码包名 `module.modules.<name>`」改为**按文件路径加载**
（合成包名 `qqbot_plugin_<name>`），插件位置不再被字符串焊死。

| 曾计划 | 实际 | 说明 |
|---|---|---|
| `plugins/builtin/<name>/` | **`app/plugins/<name>/`** | 比"仓库根 plugins/"更符合「代码在 app 下」 |
| `module/configs/` → `data/plugins/configs/` | **不动** | 迁移会让已部署配置失效，收益不抵风险 |
| `module/data/` → `data/plugins/` | **不动** | 同上 |
| `module/CONFIG_SCHEMA.md` → `docs/` | **仍留在 `module/`** | 待办：文档混在数据目录里，后续可挪 |

各插件内部评估（未变）：

| 插件 | 内部是否有问题 |
|---|---|
| `astrbot_alignment` | ✅ 5 文件（module/config_schema/assembler/astrbot_prompts）结构合理 |
| `bilibili_parser` | ✅ 9 文件，`bilibili_api`+`video`+`forward`+`wbi` 分层清楚 |
| `df_password` | ✅ `service/` 子包（handler/fetch/essence/migrate）是**范本** |
| `douyin_parser` | ✅ 11 文件，签名算法单独成文件 |
| `emoji_reply` | ✅ |
| `example` | ✅ 含 `pages/` 示例 |
| `group_log` | ⚠️ **13.7 KB 全在 `module.py`**（345 行），无 `service.py` → 建议拆出 `service.py` |
| `group_request` | ✅ |
| `llm_enhance` | ✅ 33 行 module.py，逻辑在框架侧 |
| `poke_reply` | ✅ 90 行 module.py，可接受 |
| `recall_back` | ✅ `service/` 子包 + `recall_db.py` |
| `time_sign_in` | ✅ `service/` 子包 |
| `what_to_eat` | ✅ 97 行 module.py，可接受 |

**仍受影响/待做**：`scripts/ops/release.py` 的打包清单、`pyproject.toml` 的 `packages.find`
需要覆盖 `app.plugins`；`group_log` 建议拆 `service.py`。

### 3.9 `tests/` 142 文件 → 按源码镜像分区

现在 142 个 `test_*.py` 全平铺在 `tests/` 一个目录里。改 `agent/assembly/` 时，
没有任何方式能一眼看出"该跑哪几个"。

| 目标目录 | 收纳 | 数量 |
|---|---|---|
| `tests/agent/loop/` | `test_llm_*` 中与主循环/流式/工具循环相关 | ~18 |
| `tests/agent/assembly/` | `test_llm_assembly*` `test_llm_prompt*` `test_llm_token_budget` `test_llm_user_content_parts` `test_llm_post_assemble` | ~14 |
| `tests/agent/persona/` | `test_persona_rules` `test_*emoji*` `test_llm_speech_contract` | ~5 |
| `tests/agent/context/` | `test_group_*` `test_llm_context_tools` `test_referent*` `test_display_names` `test_focus*` | ~12 |
| `tests/agent/session/` | `test_llm_history*` `test_llm_session*` `test_llm_compress` | ~12 |
| `tests/agent/tools/` | `test_llm_onebot*` `test_llm_tool*` `test_llm_mcp*` `test_llm_tavily*` | ~10 |
| `tests/agent/delivery/` | `test_llm_speech*` `test_llm_send*` `test_llm_splitter` `test_llm_stream*` | ~8 |
| `tests/agent/proactivity/` | `test_proactive` `test_schedule` `test_llm_initiative*` | ~5 |
| `tests/agent/providers/` | `test_provider*` `test_llm_provider_invoker` `test_request_policy` | ~8 |
| `tests/agent/memory/` | `test_memory*` (10) | 10 |
| `tests/agent/knowledge/` | `test_knowledge*` | ~4 |
| `tests/plugin/` | `test_module*` `test_features` `test_authority` `test_registry` `test_hook*` `test_dependency_metadata` | ~9 |
| `tests/infrastructure/` | `test_gateway*` `test_codec` `test_config*` `test_cache` `test_database` `test_account_runtime_binding` | ~10 |
| `tests/application/` | `test_session_history*` `test_resource*` `test_role_preset*` `test_agent_manager` | ~6 |
| `tests/webui/` | `test_webui*` `test_api_*` `test_dashboard*` | ~5 |
| `tests/plugins/` | `test_bilibili*` `test_douyin*` `test_astrbot_alignment_plugin` `test_df_password*` `test_recall*` `test_sign*` `test_poke*` `test_emoji_reply*` `test_group_log*` | ~15 |
| `tests/conftest.py` | 保留在根（pytest 自动发现） | — |

**执行方式**：`git mv` 批量迁移 + 全局改相对 import 路径（`tests/` 内一般用绝对 import，
所以多数文件不用改内容）。`pytest` 的 `rootdir` 与 `conftest.py` 位置不变，无需改配置。

### 3.10 `scripts/` 21 文件 → 分三类

| 现文件 | 目标 | 动作 |
|---|---|---|
| `release.py` | `scripts/ops/release.py` | 保留 |
| `requirements.bat` `requirements.sh` `venv.bat` `venv.sh` | `scripts/` | 保留 |
| `export_napcat_faces.py` | `scripts/gen/` | 移动（**代码生成器**，与消融实验不同类） |
| **新建** `scripts/gen/gen_onebot_manifest.py` | 从 `manifest.json` 生成校验器/反向生成 | 新建 |
| `clean_history_ablation.py` `dirty_info_ablation.py` `history_tagging_ablation.py` `injection_style_ablation.py` `json_format_ablation.py` `message_format_ablation.py` `prompt_context_ablation.py` `user_context_ablation.py` `verify_new_tagging.py` `teacher_direction_ab.py` | `scripts/experiments/` | **移动**（10 个消融/对照实验） |
| `diag_account_swap_e2e.py` `diag_disconnect_pairs.py` `diag_group_dispatch.py` `identity_confusion_test.py` `log_timeline.py` `schedule_task_conversation_test.py` | `scripts/diag/` | **移动**（6 个诊断脚本） |
| `remote_check.py` `remote_llm_probe.py` `provider_max_tokens_report.py` | `scripts/ops/` | **移动**（3 个运维探针） |
| `__pycache__/` | — | **删除**（已入库） |

### 3.11 根目录杂物

| 现状 | 动作 | 理由 |
|---|---|---|
| `1/`（含 `data/llm/*/tasks_data.json`） | **删除** | 像是误创建的目录 |
| `test.py`（7 KB） | **删除或并入 `scripts/experiments/`** | 与 `tests/` 体系无关的散件 |
| `deprecated/qq-client/`（含 `__pycache__` 入库） | 移到 `docs/archive/qq-client-source.md` 只留说明，代码删除 | "deprecated" 目录留在仓库里没有价值，git 历史里有 |
| `AstrBot-master/` `astrbot_plugin_private_companion-*/` `astrbot_plugin_proactive_chat-main/` `QQ-agent-main/` `bili-download-probe/` `bili-video-parser/` | 移入 `third_party/`（并加 `.gitignore`），或直接删 | 6 个 vendored 参考实现，是 `docs/` 里那些对齐文档的"原始材料"。**建议只保留 `docs/` 里的分析结论，源码删掉**——它们是 git 历史可恢复的 |
| `qqbot_next.egg-info/` | `.gitignore` + 删 | 构建残留 |
| `.pytest_cache/` `.ruff_cache/` `__pycache__/` | `.gitignore` + 删 | 构建残留 |
| `main.py`（126 B） | 保留 | 薄入口 shim，符合预期 |
| `ci_check_pyproject.py` | 移到 `scripts/ci/` | 只有 CI 用 |
| `gitpush.py` `gitpush.bat` | 移到 `scripts/` | 工具脚本不该在根 |
| `.env` `.env.example` `start.bat` `start.sh` `pyproject.toml` `requirements.txt` | 保留 | 根目录该有的东西 |

> **目标**：根目录只剩 `main.py`、`start.*`、`pyproject.toml`、`requirements.txt`、`.env*`、
> 以及 `app/ plugins/ data/ tests/ docs/ scripts/ dashboard切走后的空位`。

---

## 4. 文档体系：准一来源矩阵

问题不是"文档太少"（现有 300KB+），而是**同一件事被三份文档各自复述，且复述会漂移**
（例：插件结构在 `STYLE.md §6`、`MAINTENANCE.md §7.1`、`MODULE_DEV.md §1`、`README.md` 各写一遍，
其中 `STYLE.md` 那份是错的）。

**规则：一个主题只有一个"准一来源"，其他文档只能一句话概括 + 链接，不许复述细节。**

| 主题 | 准一来源 | 其他文档该做什么 |
|---|---|---|
| 分层与目录职责、文件去向 | **`docs/ARCHITECTURE.md`（本文件）** | `README`/`MAINTENANCE` 只放一句话 + 链接 |
| 代码风格 / 命名 / 类型注解 / 函数长度 | **`STYLE.md`** | 全部指向 |
| 已知架构债 + 验收指标 | **`STYLE.md §12`** | 本文件 §7 只引用指标值 |
| 插件开发契约（目录、钩子、`ctx` API） | **`docs/MODULE_DEV.md`** | `README`「写插件」只给一行 + 链接 |
| 模块配置表单字段 | **`docs/CONFIG_SCHEMA.md`** | — |
| 启动 / 运维 / 环境变量 / 备份 | **`MAINTENANCE.md`** | `README` 只放三行快速开始 |
| 用户视角功能清单 | **`README.md`** | — |
| 提交与验证分档 | **`AGENTS.md`** | `STYLE.md §11` 已改为指向 |
| 设计原因（为什么这么设计） | **`docs/design/*.md`** | — |
| 与外部实现的对齐分析 | **`docs/alignment/*.md`** | — |
| 版本变更 | **`CHANGELOG.md`** | — |

`docs/` 重排：

```
docs/
├── ARCHITECTURE.md          ★ 本文件：目录职责 + 文件去向 + 分阶段计划
├── MODULE_DEV.md            写插件看这个
├── CONFIG_SCHEMA.md         配置表单字段（从 module/CONFIG_SCHEMA.md 移入）
├── design/                  设计文档（为什么）
│   ├── prompt-assembly.md   ← prompt-assembly-redesign.md
│   ├── context-expand.md    ← context-expand-design.md
│   ├── referent.md          ← referent-resolution-design.md
│   ├── memory.md            ← memory-design.md
│   ├── group-log-emoji.md   ← group-log-and-emoji-design.md
│   ├── dashboard.md         ← dashboard-frontend-architecture.md
│   └── features.md          ← FEATURE_REGISTRY.md
├── alignment/               与外部实现的对账（不是本项目规范）
│   ├── qq-agent-assembly.md ← qq-agent-alignment-plan.md
│   ├── qq-agent-diff.md     ← qq-agent-llm-dialogue-diff.md
│   └── qq-agent-prompt.md   ← qq-agent-prompt-comparison.md
├── archive/                 已完成的历史记录（不是待办）
│   └── assembly-takeover-progress.md
└── research/                原始调研材料（只读，不维护）
```

**同步动作**：每份文档开头加一行 `> 本文只负责：xxx。相关主题见 yyy。`

---

## 5. 分阶段执行计划

每阶段 = **一个可独立提交、可独立回滚、可独立验收**的批次。
阶段内的每个 commit 仍按 `AGENTS.md` 的"一步一提交"。

### 5.1 阶段表与执行状态

| 阶段 | 内容 | 状态 | 交付判定 |
|---|---|---|---|
| **P0** 清垃圾 | 删 `container.service()` 等 3 个死函数；数据路径函数下沉 `infrastructure/fs/`；`app/agent` 包 `__init__` 去重导出 | ✅ **已完成** | `bootstrap_imports` 4→0；环 63→29 模块 |
| **P1** 数据外置 | `onebot_tools/manifest.py` → `manifest.json` | ✅ **已完成（缩小范围）** | 4,792 行字面量 → 100 行加载器；175 条逐条对账 |
| **P2** `app/llm`→`app/agent` 分包 | 91 文件按 §3.1 移入 11 个子包 | ✅ **已完成** | 758 处引用改写；全量 1646 passed |
| **P3** 拆环（最关键） | 活动容器下沉 core；`media_cache` 下沉 infrastructure；工具注入策略拆出 `tools/policy.py` | ✅ **已完成** | 环 63 → **3** 模块；`chat.py` 闭包 46,305 → 25,342 |
| **P4** 最后一个环 + 包职责文档 | 定时工具从定时引擎分离（`tools/schedule.py`）；22 份包 README + 闸三 | ✅ **已完成** | **0 个强连通块**；闸三落地 |
| **P5** 目录归一 | `module/modules`→`app/plugins`；`app/modules`→`app/plugin`；`tests/` 分区；`scripts/` 三分；`group_log` 拆 `service.py`；`domain/` 长实 | ✅ **已完成** | 13 插件全加载；1653+ passed |
| **P6** 前端收敛 + 目录收口 | 删 `webui/static`+`templates`+`/legacy`；`dashboard/`→`app/webui/dashboard/` | ✅ **前端部分完成** | `/` 200；`/legacy` 404 |
| **P7** `chat.py` 拆片 | 2,192 行按职责拆到 `loop/` 子文件（`chat.py` 保留重导出，API 不变） | 🟡 **第一轮完成** | 2,192 → **1,668 行**（切出 turn_metrics / assistant_output / tool_source）；三个长函数仍待按段落拆 |
| **P9** 目录收口 | `app/nodes` 并入 `app/plugin/nodes`（✅）；`app/services` → `app/application`（✅） | ✅ **已完成** | **`app/` 顶层 8 个目录，即 §2.1 的最终形态** |
| **P8** WebUI 依赖注入收尾 | `webui/api/*` 的 `get_container()` / `container.get(类型)` 全改 `Depends(get_x_service)` | ✅ **已完成** | **71 → 1**（只剩 `deps.py` 的实现本身），17 条具名依赖 |

### 5.1.1 还没做的（按性价比排序）

1. **`chat.py` 拆片**（2,192 行；最大函数 `stream_response` 378 行）——可读性收益最大。
   做法：按 `_collect_llm_ext`（工具来源）、发言通道（`_build_speech` 一族）、
   用量与预算（`_usage_*` / `_turn_budget` / `_effective_max_tokens`）三块搬到
   `loop/tool_source.py` / `loop/speech_channel.py` / `loop/turn_metrics.py`，
   在 `chat.py` 顶部 `from .xxx import ...` 重导出，**对外零 API 变化**。
   注意 `chat.py` 里 `DEFAULT_MAX_TOOL_ROUNDS` 等模块级常量要一起搬或改指向。
2. **`webui/api/*` 剩余依赖注入**：`deps.py` 已备好 `get_bot_service` /
   `get_module_registry` / `get_provider_preset_service` 等 15 个具名依赖，
   剩下是把端点里的 `container = get_container(request)` + `container.get(X)`
   改成参数注入。闸二 `webui_container_get_calls` 会盯着这个数字只降不升。
3. **`apps/services` → `app/application` 改名** + `SchedulerService` → `cron_service.py`
   （消掉与 `TaskScheduler` 的同名混淆）。
4. **`app/nodes/` 并入 `app/plugin/nodes/`**（它服务于插件派发）。
5. **`docs/` 按 §4 重排**（`docs/{architecture,agent,plugins,frontend,ops,research}/`）。

### 5.2 P0–P6 的实测结果

口径：`scripts/gen/arch_closure.py`，扫描域 `app/` + `module/` + `scripts/`，
行数按 `.count("\n")`，**含函数内惰性 import**。复核就重跑脚本，不要抄这里的数字。

```
                         P2 之后           P6 之后
模块数 / 总行数          276 / 55,039      279 / 55,339
强连通块                2 个              **0 个**
  其中最大              27 文件 / 13,547  —
无环模块占比            91%               **100%（279/279）**
```

| 入口 | P2 之后闭包 | P4 之后闭包 |
|---|---|---|
| `app.agent.loop.chat` | 158 文件 / 41,717 行 | **87 文件 / 23,721 行** |
| `app.agent.assembly.assembler` | 158 / 41,717 | **36 / 9,406** |
| `app.agent.proactivity.scheduler` | 158 / 41,717 | **93 / 25,369** |
| `app.agent.providers` | 22 / 5,427 | 22 / 5,434 |
| `app.agent.memory` | 30 / 7,507 | 30 / 7,514 |
| `app.agent.knowledge` | 27 / 6,030 | 27 / 6,037 |
| `app.plugin.registry` | 8 / 2,400 | 8 / 2,400 |
| `app.infrastructure.onebot.gateway` | 11 / 3,691 | 11 / 3,690 |
| `app.bootstrap` | 158 / 41,717（**在环内**） | 158 / 41,644（**已出环**） |

**拆掉的七条反向边**（每条都只改一处，收益却覆盖整个图）：

| 反向边 | 原来为什么存在 | 现在怎么走 |
|---|---|---|
| `core/container → bootstrap` | `service(type_)` 桥接函数 | 删函数；依赖走构造函数或 `Depends` |
| `agent/__init__ → loop/chat` | 包级重导出 `handle` | 不再导出 `handle` |
| `modules/api → bootstrap` | 插件无参便捷 API 要拿容器 | `app.core.container.get_active()`（唯一允许的服务定位） |
| `agent/media/store → modules` | 只为用 `media_cache` | `app/infrastructure/fs/media_cache.py` |
| `agent/loop/chat → agent/tools/system` | 只为 `is_system_tool_enabled` | 拆出 `app/agent/tools/policy.py` |
| `agent/memory/manager → agent.memory`（包属性） | `from app.agent.memory import extract` | `import app.agent.memory.extract` |
| `agent/loop/chat → proactivity/scheduler` | `build_schedule_tool` 与引擎同文件 | 工具移到 `app/agent/tools/schedule.py` |

**执行中修正的三处方案错误**（都以实测为准，本文档已按实际改写）：

1. `app/llm/storage/` 不是死代码——它是 `HistoryStore`/`MemoryStore` 接口与工厂，
   被 `media/store.py` 与 `session/manager.py` 使用。**保留并搬走**，未删除；
2. `config_schema.py` 与 `qq_faces.py` **不**外置 JSON：前者是「数据 + `_gate`/
   `_on` DSL」，JSON 化会把条件可见性的可读意图摊成重复字面量；后者是生成物
   （`scripts/gen/export_napcat_faces.py --check` 依赖 Python 渲染），只有 127 行；
3. `app/llm` 不留重导出 shim：一个概念只能有一个名字，shim 会让两套入口并存
   并重新制造环。全库引用一次改完，`test_old_llm_package_is_gone` 守门。

### 依赖与顺序约束

```
P0 ──► P1 ──► P2 ──► P4 ──► P5
               │            │
               └──► P3 ─────┘
                     │
                     └──► P6
```

- **P2 必须在 P4 之前**：不然拆完 `chat.py` 又要跟着搬一遍。
- **P3 与 P4 可并行**，但都依赖 P2 的路径稳定。
- **P6 放最后**：前端删旧版要先确认所有页面迁移完。

### 回滚成本

| 阶段 | 回滚方式 | 代价 |
|---|---|---|
| P0 | `git revert` 单 commit | 零 |
| P1 | `git revert`（JSON 与 .py 同时存在一个版本，双读） | 零 |
| P2 | `git revert` 整批（机械移动，无逻辑变更） | 零 |
| P3 | `git revert` 按文件粒度 | 低 |
| P4 | **按文件粒度 revert**（绝不在一个 commit 里拆两个文件） | 中 |
| P5 | `git revert` 整批 | 低 |
| P6 | `git revert` | 零 |

> **铁律**：P2 之前**不要**混入任何逻辑改动。分开才能证明"测试全绿是因为只搬了家"。

---

## 6. vibecoding 手册：改 X 该读什么

这一节是本方案的**最终目的**。改一个需求时，只读下表列出的文件就够；
如果需要读表外的文件，说明**边界划错了**，先质疑架构而不是继续读。

### 6.1 路径地图

| 你的需求 | 必读（≤3 个） | 按需再读 | 跑这些测试 | 明确不该碰 |
|---|---|---|---|---|
| 加/改一个厂商 | `agent/providers/base.py`<br>`agent/providers/__init__.py`<br>`agent/providers/openai_compat.py` | `providers/policy.py` | `tests/agent/providers/` | `loop/`、`webui/` |
| 改请求参数/重试/裁剪 | `agent/providers/policy.py` | `domain/conversation/budget.py` | `test_request_policy` `test_llm_token_budget` | `chat.py`（已不该存在） |
| 改提示词某一段 | `agent/persona/rules.py` | `agent/assembly/assembler.py` | `tests/agent/persona/` | `providers/` |
| 改"该不该回这句话" | `domain/initiative/trigger.py`<br>`domain/initiative/policy.py` | `agent/loop/entry.py` | `tests/agent/loop/` `test_proactive` | `providers/`、`delivery/` |
| 改消息怎么组装成 messages | `agent/assembly/assembler.py` | `assembly/{inputs,prompt}.py` | `tests/agent/assembly/` | `loop/generate.py` |
| 改历史存/渲染 | `agent/session/model.py`<br>`infrastructure/persistence/history_repo.py` | `session/manager.py` | `tests/agent/session/` | `webui/` |
| 加一条 OneBot 工具 | `agent/tools/onebot/manifest.json` | `tools/onebot/executor.py` | `test_llm_onebot*` | `providers/` |
| 改工具权限/风险等级 | `domain/tool_policy/policy.py` | `agent/tools/spec.py` | `test_llm_onebot_tools` | `webui/` |
| 改图片/语音处理 | `agent/media/image.py` 或 `speech.py` | `infrastructure/fs/media.py` | `test_llm_image*` `test_llm_speech*` | `providers/` |
| 改发送节奏/分条数 | `domain/delivery/pace.py`<br>`agent/delivery/splitter.py` | `delivery/pool.py` | `test_llm_send*` | `assembly/` |
| 改主动消息/定时 | `agent/proactivity/manager.py`<br>`domain/scheduling/time_parser.py` | `proactivity/scheduler.py` | `test_proactive` `test_schedule` | `webui/` |
| 改群语境/指代消解 | `agent/context/referent.py`<br>`agent/context/group.py` | `tools/context_tools.py` | `tests/agent/context/` | `session/` |
| 改记忆 | `agent/memory/manager.py` | `memory/recall.py` `infra/persistence/memory_repo.py` | `tests/agent/memory/` | `providers/` |
| 加一个 WebUI 接口 | `webui/api/deps.py`<br>对应的 `application/*.py` | `webui/schemas.py` | `tests/webui/` | **`agent/` 任何文件** |
| 改前端页面 | `webui/dashboard/src/views/<Page>.vue` | `src/stores/` `src/api/http.ts` | `pnpm build` | 后端任何文件 |
| 加一个业务插件 | `docs/MODULE_DEV.md`<br>`plugins/builtin/example/` | — | `tests/plugins/` | `app/` 任何文件 |
| 改插件框架 | `plugin/base.py`<br>`plugin/registry.py` | `plugin/features/` | `tests/plugin/` | `agent/`（禁止） |

### 6.2 三条铁律

1. **先看目录名再看文件。** 找文件时按"这个需求属于哪一层的哪一类"定位，
   而不是全文搜索函数名。目录名答不上来 → 架构没切对。
2. **改动不跨层。** 一次改动落在一个包内。若被迫跨两个包（例如同时改 `agent/` 和 `webui/`），
   说明该功能缺一个 `application/` 门面，**先补门面再改**。
3. **测试路径跟着目录走。** `tests/<层>/<包>/` 与 `app/<层>/<包>/` 一一对应，
   "跑哪些测试"由"改了哪个目录"直接推出，不需要判断。

---

## 7. 验收：可执行的三道闸

架构方案最容易变成"写完就没人看"。三道闸把它变成 CI 能拦的东西。

| 闸 | 实现 | 状态 |
|---|---|---|
| 闸一：可达闭包可度量 | `scripts/gen/arch_closure.py` | ✅ 已落地 |
| 闸二：分层边界测试 | `tests/test_architecture_boundaries.py` | ✅ 已落地（8 条） |
| 闸三：目录完整性 | `tests/test_architecture_layout.py` | ✅ 已落地（7 条） |

### 7.1 闸一：可达闭包（`scripts/gen/arch_closure.py`，已落地）

AST 建 import 图（`ast.walk` 覆盖函数内惰性 import）→ 对每个入口算前向可达集合 →
输出 `(文件, 闭包文件数, 闭包行数, 是否在强连通块)`。

```powershell
venv\Scripts\python.exe scripts\gen\arch_closure.py            # 总报告 + 环清单 + 入口闭包
venv\Scripts\python.exe scripts\gen\arch_closure.py --scc      # 只看强连通块
venv\Scripts\python.exe scripts\gen\arch_closure.py --why app.agent.loop.chat   # 环的成因路径
venv\Scripts\python.exe scripts\gen\arch_closure.py --assert-max 20000          # CI 断言
```

**注意：`app.bootstrap` 与 `app.webui.app` 的闭包天然接近全图**——它们是装配根/
挂载点，按定义就引用所有东西。这两个入口**不设**阈值；有意义的阈值只对
「被一个需求改动的最小单元」设。

| 入口 | 起点 | 现状（P3 后） | P4 后目标 |
|---|---|---|---|
| `app.agent.loop.chat` | 42,082 | **25,342** | ≤8,000 |
| `app.agent.assembly.assembler` | 43,484 | **9,406** | ≤6,000 |
| `app.webui.api.agent` | 43,839 | **32,584** | ≤8,000 |
| `app.agent.providers` | 1,122 | 22 文件 / 5,434 | ≤3,000 |
| `app.infrastructure.onebot.gateway` | — | 11 文件 / 3,690 | 保持 |
| `app.plugin.registry` | — | 8 文件 / 2,400 | 保持 |

### 7.2 闸二：分层边界测试（已落地）

`tests/test_architecture_boundaries.py`（仿 `test_no_silent_except.py` 的 AST 扫描风格）：

- `domain/` 的 import ∈ {`core/`, `domain/`, 标准库, 三方}
- `infrastructure/` **不得** import `agent/` `plugin/` `application/` `webui/`
- `webui/` **不得** import `agent/`
- `plugin/` **不得** import `agent/`
- 全库 **不得**出现 `from app.bootstrap import` / `get_container(`
- 新增函数内惰性 import 必须带 `# lazy:` 注释（白名单机制同 `test_no_silent_except`）

### 7.3 闸三：目录完整性（已落地）

`tests/test_architecture_layout.py`，7 条：

- `app/` 顶层只允许白名单里的 8 个目录（`agent core domain infrastructure modules nodes services webui`）；
- **每个包必须有 `README.md`，首行是 `> 本文只负责：…`**——一句话说不清这个包负责什么，
  就说明它该拆了（已有 22 份：8 个顶层包 + 14 个 agent 子包）；
- `app/agent/` 每个子包 `.py` ≤ 12（超过说明又长成平铺包了）；
- `app/agent/` 顶层 `.py` ≤ 10（当前 8）；
- 每个业务插件目录必须有 `module.py`；
- 不允许只剩 `__pycache__` 的空壳目录（`app/client` 曾是这样，已删）。

白名单与上限都是**棘轮**：只能收紧，不能放宽。P5 会把 `modules`→`plugin`、
`services`→`application`、`nodes` 并入 `plugin`，届时改白名单并同步目录。

---

## 8. 明确不做（划清边界）

| 不做 | 为什么 |
|---|---|
| 重写 `chat.py` 的算法 | 只搬不改语义。想改行为另开 feature 分支，别混在重构里 |
| 引入 DI 框架（dependency-injector 等） | 现有 `Container` 够用，问题不在容器而在"绕过容器直调" |
| 把插件做成独立进程 / 插件市场 | 单人维护，收益远小于成本 |
| 重写前端为另一种框架 | 只收敛重复，不换技术栈 |
| 动 `agent/providers/` 的结构 | 它是全库最规范的部分，是其他包的**范本**，不是改造对象 |
| 追求 100% 测试覆盖 | 目标是"改动的爆炸半径可预测"，不是覆盖率数字 |
| 拆成 monorepo / 多包发布 | 与单人维护 + vibecoding 的目标相反 |
| 统一 `dashboard` 与后端 schema 的重复渲染 | 那是**功能改动**（前端直接消费 `schema.json`），放到 P6 之后单独立项 |

---

## 附录 A：盘点的实测数字

### A.1 起点（方案制定时）

| 指标 | 数值 |
|---|---|
| `app/` | 166 文件 / 46,681 行 |
| `module/` | 76 文件 / 6,759 行 |
| `tests/` | 142 文件 / 29,393 行 |
| `app/agent/` | 91 文件 / **32,900 行**（占 `app/` **70%**） |
| `app/agent/` 顶层平铺 | 55 文件 / 20,916 行 |
| `app/agent/` 内"零内部依赖"的纯文件 | **29 个 / 9,849 行**（占 `app/agent/` 30%） |
| `app/domain/` | 4 文件 / 595 行 |
| `app/nodes/` | 4 文件 / 184 行 |
| 最大强连通块 | 24 文件 / 12,112 行 |
| `chat.py` 可达闭包 | 143 文件 / 42,082 行 |
| 函数内惰性 import | 315 处（`chat.py` 28 处最多） |
| ≥80 行的函数 | 41 个（最大 `stream_response` 378 行） |
| `get_container()` 直调 | 71 次 / 14 文件（65 次在 `webui/api/*`） |
| `__init__.py` 里重复定义的 `_ok/_err` | **9 个** api 文件各一份 |
| 单文件行数 Top3 | `manifest.py` 4,792 · `chat.py` 2,192 · `config_schema.py` 1,072 |

### A.2 现状（P0–P6 之后，`arch_closure.py` 实测）

| 指标 | 数值 | 变化（对比 P2 之后） |
|---|---|---|
| 模块数 / 总行数 | 279 / 55,339 | — |
| 依赖边数 | 781 | — |
| 强连通块 | **0 个（全图无环）** | 从 27 文件 / 13,547 行 |
| 无环模块 | **279 / 279（100%）** | 从 91% |
| `chat.py` 闭包 | **87 文件 / 23,721 行** | 从 158 / 41,717 |
| `assembler` 闭包 | 36 文件 / 9,406 行 | 从 158 / 41,717 |
| `providers` 闭包 | 22 文件 / 5,434 行 | 持平 |
| `bootstrap` 是否在环内 | 否 | 原本在 |
| `get_container()` 直调 | 63 次 / 9 文件（全在 `webui/api/*`） | 从 71 |
| 生产代码 `from app.bootstrap import` | **0** | 从 4 |
| `app/agent/` 顶层平铺 | **8 文件 / 2,939 行** | 从 55 文件 / 20,916 行 |
| `app/` 顶层目录数 | 9（`agent core domain infrastructure nodes plugin plugins services webui`） | 白名单一致 |
| 包职责 README | **22 份** | 从 0 |

> 数字是**快照**。复核时重算：`venv\Scripts\python.exe scripts\gen\arch_closure.py`
> （口径与算法见脚本 docstring 与 `STYLE.md §12`）。

---

*本方案与 `STYLE.md §12` 的关系：§12 描述**现在的债与验收值**，本文描述**怎么还**。
两份都改完后，`app/` 顶层 8 个目录的职责应当能一句话说清，且改任何需求只需读 ≤3 个文件。*

