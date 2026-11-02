# 05 · 三个核心模块的骨架、槽接管契约与旧模块退场/迁移机制

> 角色：框架契约设计（只读调研 + 本文件）。**本轮不改任何 `.py`。**
> 输入事实：`app/plugin/**`、`app/agent/{policy,capabilities}.py`、`app/infrastructure/config/binding_store.py`、`app/bootstrap.py`、`app/webui/api/modules.py`、9 个 `app/plugins/llm_*`、`scripts/migrate_v3_split_config.py`、`docs/FEATURE_REGISTRY.md`、`docs/reviews/2026-10-31-assembly-output-consolidation.md`。
> 三新模块命名取自 task-4：`llm_prompt` / `llm_delivery` / `llm_trigger`。

---

## 0. 结论速览（给 Lead 的三句话）

| # | 议题 | 结论 |
|---|---|---|
| 1 | **槽接管** | 走 **路线 A：让 `Module` 自己实现 `build()` / `respond()`（转调 `service.py` 里的能力对象）**。框架控制器 0 行改动、第三方契约不变、产品代码净增约 +30 行。路线 B（能力对象注册）要动 `features/base.py + controllers.py + registry.py` 三个共用文件约 120 行，且必须同时保留"owner 自身合格"的兼容分支——现在没有需要它的场景。**但 A 只是"能接管"，"接管失败必可见"必须另配四道闸（§3.5）**，否则只是把装饰性声明换成能跑的声明。 |
| 2 | **迁移** | 新增 `app/application/core_modules_migration.py`，签名 `async def migrate_core_module_bindings(config_service) -> MigrationReport`；**幂等靠"新行已有键优先 + kv 标记"双保险**，回滚靠"自动备份 DB + kv 快照 + 旧行只禁用不删"。插入点是 **`app/bootstrap.py::run()` 的 `config_service.init()` 之后、`registry.discover()` 之前**（`bootstrap.py:519-576`）——**不是 `start.py`**（`start.py` 只是 venv 启动器，`start.py:1-30` 之后 `exec main.py`，`main.py:5-8` 才调 `app.bootstrap.run`）。 |
| 3 | **降级** | 治疗点不是"加个 if"，而是**每个新模块给出一条"退化成什么"的显式契约**并把它写进日志 + 就绪清单 + 模块页红标（§7 表）。最危险的一条：`llm_delivery` 缺失而 agent 行残留 `speech_via_tool=true` 时，框架会贴协议但不 flush → **复现真实发生过的"整轮静默"**（`docs/reviews/2026-10-31-assembly-output-consolidation.md:214-249`）。这一条必须 fail-loud，不允许静默降级。 |

---

## 1. 前提校正（我实测到、与任务描述不一致的三处）

| 任务描述 | 实测 | 证据 |
|---|---|---|
| 账号 123 有 **9** 个 `llm_*` 绑定行，只有 **4** 个有配置 | **10 行**，**5 行**有配置；`enabled=1` 只有 3 行 | `data/app.db` → `bindings`：`llm_enhance`(cfg 22B, en=1)、`llm_knowledge`(92B, en=0)、`llm_output`(818B, en=1)、`llm_proactive`(591B, en=0)、`llm_tools_extra`(290B, en=1)；其余 5 行 cfg=`{}` 且 en=0 |
| 旧 `module_config` / `module_authority` 是"只读迁移源" | **两张表都已经 0 行**（迁移早已跑完并清空），不能再当迁移输入 | `SELECT COUNT(*)` → 均为 0；`kv["bindings_migrated"]="1"`；`binding_store.py:256-343` 的 `_migrate_bindings_if_needed` 已是历史路径 |
| 迁移调用链在 `start.py` | 在 `app/bootstrap.py::run()`：`bootstrap.py:519-528`（对话策略/触发方式）、`:530-547`（提示词方案）、`:562-571`（Provider/LLM 数据）、`:575` 才 `registry.discover()` | `start.py` 全文是 venv/toolchain/依赖探测 + `exec main.py`；`main.py:5-8` → `asyncio.run(run())` |

**额外发现（迁移必须处理）**：`llm_knowledge` 有 binding 行（含 3 个键）但 `app/plugins/llm_knowledge/` 目录已不存在 → **孤儿行**。`config_partition.py:307-311` 仍把它算进 `MODULE_KEYS`。迁移脚本必须显式处理"有行无模块"与"有键无归宿"两类孤儿，否则这 3 个值会静默消失（与 `scripts/migrate_v3_split_config.py:122-130` 的 `_dump_dropped` 同一纪律：**不许静默丢**）。

---

## 2. 新模块骨架

### 2.1 目录与文件清单（按 `docs/PLUGIN_STYLE.md:9-34` 的硬约束）

```
app/plugins/llm_prompt/      app/plugins/llm_delivery/      app/plugins/llm_trigger/
├── module.json              ├── module.json                ├── module.json
├── main.py                  ├── main.py                    ├── main.py
├── __init__.py              ├── __init__.py                ├── __init__.py
├── config_schema.py         ├── config_schema.py           ├── config_schema.py
├── service.py               ├── service.py                 ├── service.py
└── src/                     └── src/                       └── src/
    ├── __init__.py              ├── __init__.py                ├── __init__.py
    ├── plan.py  (TurnPlan)      ├── strategy.py (respond)     ├── proactive.py
    ├── sections.py (段表)        ├── speech.py  (协议文本)      ├── scheduler.py
    ├── render.py (user 行)      ├── pace.py    (节奏)          ├── store.py
    └── presets.py (方案文案)     └── directives.py             └── facade.py
```

| 文件 | 职责（硬边界） | 依据 |
|---|---|---|
| `main.py` | 只放类属性 + `default_config` + `config_schema = SCHEMA` + 四个生命周期 + **能力对象转发**（§3 A 路线，约 6-10 行/模块）。建议 ≤150 行，上限 220。 | `PLUGIN_STYLE.md:36-49` |
| `config_schema.py` | 只放 `SCHEMA` 常量；键与 `default_config` **一一对应**（闸门可查）。 | `PLUGIN_STYLE.md:39`；现状 9 插件都这么做（如 `llm_output/main.py:103`） |
| `service.py` | **能力对象**：实现框架契约（`build(req)` / `respond(ctx,*,should_stop=None)`），被 `Module` 持有并转发。它靠"根目录例外条款"留在根（单个文件 ≤100 行、只被 `main.py` import、README 写明用途）。 | `PLUGIN_STYLE.md:27-31,102-104`；既有先例 `llm_output/service.py:26`、`llm_assembly/service.py:22` |
| `src/` | 纯函数优先、不读 `self`、不读全局配置；`main.py → src/` 单向依赖。 | `PLUGIN_STYLE.md:63-69` |
| `module.json` | 由 `scripts/gen_module_manifests.py` 从类属性 AST 生成（`ATTR_MAP`：`display_name←name`、`sign`、`description`、`version`）。**`provides`/`supersedes` 不能放清单**——`manifest.py:57-104` 只校验 `name/display_name/version/api_version/entry/requires/requirements`，其余字段只进 `raw`。 | `scripts/gen_module_manifests.py:24-31,50-90`；`manifest.py:107-122` |

### 2.2 `Module` 类的必须 / 可选属性

| 属性 | 必须 | 三个新模块取值 | 依据 |
|---|---|---|---|
| `name` | ✅ | 提示词组装 / 发言与输出 / 主动与定时 | `base.py:322` |
| `sign` | ✅ | `LlmPrompt` / `LlmDelivery` / `LlmTrigger` | `base.py:323` |
| `description` | ✅ | 一句话说清"决定什么" | `base.py:324` |
| `permission` | ✅ | `"everyone"`（纯策略，不响应事件） | `base.py:325` |
| `subscribe` | ✅ | `()`——三者都**不订阅消息事件**（策略模块，`base.py:344` 明确说了） | `base.py:326,344` |
| `category` / `order` | ✅ | `"LLM"` / 10,20,30 | `base.py:329,331` |
| `default_config` | ✅ | 与 `config_schema` 同键集；≤12/≤12/≤10 | `base.py:327` |
| `config_schema` | ✅ | `SCHEMA` | `base.py:328` |
| `provides` | ⚠️ 仅 prompt/delivery | `("assembly",)` / `("output",)`；**`llm_trigger` 不声明** | `base.py:334`；`FEATURE_REGISTRY.md:48-60`（主动/定时是业务不是 feature） |
| `supersedes` | ⚠️ 同上 | `("assembly",)` / `("output",)`；`llm_trigger = ()` | `base.py:335` |
| `requires` | ✅ | `("llm",)`；prompt 另加 `("role_presets",)` | `base.py:338`；`requirements.py` 缺失只 WARNING（`base.py:506-514`）→ 我们不靠它做硬校验 |
| `supports_single_service` | ❌ | 保持默认 `False`（纯 LLM 策略模块不该声明；`base.py:339-345`） | 同左 |
| `version` / `display_name` | ❌ 代码里不要写 | **以清单为准**，loader 会覆盖：`loader.py:154-158` | 同左 |

> `instance.parent/children/source/manifest/version/display_name/api_version/plugin_dir/can_uninstall` 全部由 loader 注入（`loader.py:149-161`），模块**不要**自己写。

### 2.3 生命周期职责表（当前 loader 的真实时序）

时序事实：`load_single` → `instantiate_module`(`loader.py:99-161`) → `bind_module_hooks`/`bind_commands`/`register_hooks`(`:292-299`) → `register_data_sources`(`:302-336`) → `activate`(`:339-366`)；`activate` 内部**先 `on_load`(`:357-360`)、后 `acquire_module`(`:362-366`)**；禁用时 `on_load` 直接跳过（`:349-355`）；热启用走另一条等价链 `app/webui/api/modules.py:167-227`（`_sync_module_runtime`）。

| 钩子 | `llm_prompt` | `llm_delivery` | `llm_trigger` | 纪律 |
|---|---|---|---|---|
| `on_load` | 构建 `PromptPlanner`（段表方案/时间位置/元信息项/资料块）→ 存 `self._service`；`runtime.prompt_sections` 需要替换的段用 `register_section`（`sections.py:248-253`）；**不手工写 `runtime.assembler`**（交给租约，§3） | 构建 `DeliveryStrategy`（mode/bubbles/节奏/协议文本）→ 存 `self._service`；`registry_of(runtime).declare_output(kind, source="llm_delivery")`（`capabilities.py:106-109`）——与 `llm_output/main.py:120-132` 同款但改成"声明由接管者唯一负责" | 启动 `ProactiveManager` / `SchedulerService` 的注册（`spawn` 托管，`base.py:646-656`）；**不接管任何槽** | ① 每个 `on_load` 开头 `runtime = self._runtime()`，`None` **必须 raise**（`_runtime()` 见 `base.py:689-698`），禁止 `return`；② 任何"已注册但后续失败"的分支必须回滚已做的注册再 raise |
| `on_unload` | `unregister_section` 逐条撤（`sections.py:257-262`）；把 `runtime.*_options` 置回 `None` | `revoke_output(source=...)`（`capabilities.py:111-114`）；**不要**自己 `setattr(runtime, "output", ...)`（租约 `release` 会做，`registry.py:118-126`） | 停计时器/取消 spawn 的任务；注销 scheduler | 必须**幂等**：`on_load` 半途失败后再调 `on_unload` 不能二次崩 |
| `on_online` | 空（策略无副作用） | 空 | 真活儿在这里：断线期间不能武装计时器 | `base.py:487-495`：`on_load` 是"实例装配（不需连接）"，`on_online` 才是"可以开始跑"；`register_data_sources(online=False)` 已刻意跳过定时任务（`loader.py:308-311,319-329`） |
| `on_offline` | 空 | 空 | 停掉 `on_online` 启动的一切 | `base.py:497-503`：否则断线后仍对着死连接发消息 |

> **三处现有反例（不要在新区模块里复刻）**：`llm_output/main.py:105-109`、`llm_assembly/main.py:59-62`、`llm_message_meta/main.py:72-75` 全是 `runtime is None → return`，不留任何痕迹。

### 2.4 异常处理纪律：把"静默失败"变成四道闸

| 现状 | file:line | 新模块 / 框架补丁 |
|---|---|---|
| `on_load` 抛异常 → 只 `log.warning`，**模块仍显示"已启用"** | `loader.py:357-361` | ① 级别 `warning` → `error`；② 写 `_record_load_error(module_name, ...)`（`registry.py:508-510`）→ 编目 `status="error"`（`catalog.py:22,35`）→ 就绪清单可见（`readiness_service.py:118-132,254-263`）；③ **`on_load` 失败后不再 `acquire_module`**（现在是无条件执行，`loader.py:362-366`） |
| 禁用/启用热路径同样只 warning | `app/webui/api/modules.py:186-227` | 同一改造：失败写 load_error + 让模块卡片进 error 态；`on_unload` 失败不能让 `authority.enabled` 已知成功 |
| `runtime is None → return` | 见 §2.3 三个反例 | 改 `raise ModuleActivationError("runtime 未装配：账号 %s" % self.bot_id)`，由 loader 捕获并落 error 态 |
| 租约被拒只 warning | `controllers.py:177-182`、`:183-188`、`registry.py:83-90` | `warning` → `error` + 可行动提示；并把"被拒"回传给调用方（§3.5 Gate 2） |

---

## 3. 槽接管的正解

### 3.1 缺陷的完整复现链（先确认病在哪）

| 步骤 | 事实 | file:line |
|---|---|---|
| 1 | 模块声明 `supersedes=("output",)`，但 `Module` 上没有 `respond` | `llm_output/main.py:72-73`；`base.py:319-720` 全文无 `respond`/`build` |
| 2 | 装配槽控制器要求 `require_methods` 全部可调用 | `controllers.py:218-221`（`assembly`→`build`）、`:258-261`（`output`→`respond`） |
| 3 | 判定函数只看 **owner（= Module 实例）** | `controllers.py:142-143` |
| 4 | 不合格 → warning + `return None` | `controllers.py:183-188` |
| 5 | `None` 表示"明确拒绝" → **不建租约**，再补一条 warning | `features/registry.py:83-90`（`snapshot is None` 分支） |
| 6 | 但 `on_load` 无论如何都手工把 **adapter** 装进槽 | `llm_output/main.py:111-116`、`llm_assembly/main.py:63-66` |
| 7 | 于是：**运行时真的被接管了，租约表里却查不到** → `/modules/features` 说"没接管"，`release_module` 什么都不做，卸载时靠模块自己的 `on_unload` 兜底（`:134-149`、`llm_assembly/main.py:68-77`） | 与 `FEATURE_REGISTRY.md:113-121`「缺方法时忽略接管」自相矛盾 |
| 8 | 副作用：热重载/禁用后靠 `isinstance(runtime.output, OutputStrategyAdapter)` 判断（`llm_output/main.py:143`）——A 路线落地后这个判断必然失效 | 同左 |

> 结论：**声明是装饰性的**（`docs/reviews/...-consolidation.md:266` 缺陷 10）。正解只有两条。

### 3.2 路线 A：让 `Module` 实现契约方法（推荐）

**做法**：`Module` 是唯一"接管者"，`service.py` 的能力对象归它私有。

```python
# app/plugins/llm_delivery/main.py（示意，约 10 行）
class Module(BaseModule):
    provides = ("output",)
    supersedes = ("output",)

    async def on_load(self) -> None:
        runtime = self._runtime()                      # None 直接 raise，不许 return
        self._strategy = DeliveryStrategy(runtime, self.config, OWNED_KEYS)
        runtime.output_options = self._strategy.options_view()
        registry_of(runtime).declare_output(self._strategy.kind, source="llm_delivery")
        # 不写 runtime.output —— 由 FeatureRegistry 租约写入

    async def respond(self, ctx, *, should_stop=None):  # ← 契约方法：转发
        async for msg in self._strategy.respond(ctx, should_stop=should_stop):
            yield msg
```

| 改动点 | 文件 | 行数 |
|---|---|---|
| 新模块 `Module` 增加 `respond()` / `build()` 转发 | `llm_prompt/main.py`、`llm_delivery/main.py` | 每模块 +6~10 行 |
| 删除手工装槽 | `llm_output/main.py:111-116`（-6 行）、`llm_assembly/main.py:63-66`（-4 行）；对应 `on_unload` 的 isinstance 兜底也要删（`llm_output/main.py:134-149`、`llm_assembly/main.py:68-77`，共 -20 行） | 产品代码 -30 行 |
| 框架控制器 | `controllers.py` | **0 行** |
| `BaseModule` | `base.py` | **0 行代码**。⚠️ **绝不要**给 `BaseModule` 加 `respond`/`build` 的空实现：`_usable()` 只查 `callable(getattr(owner, name))`（`controllers.py:142-143`），加了之后**所有模块**都会"合格"，等于把校验彻底废掉。只加注释说明契约。 |
| 契约测试 | 新增 `tests/plugin/test_slot_hijack_contract.py` | 测试代码，不计产品行数 |

**风险与对策**

| 风险 | 说明 | 对策 |
|---|---|---|
| `on_load` 失败后仍建租约 → 每轮 `respond` 抛异常 | `loader.py:362-366` 无条件 `acquire` | §3.5 Gate 1/2：`on_load` 失败不 acquire；模块不进入接管态 |
| Module 与 Adapter 生命周期耦合 | Adapter 需要 runtime，runtime 可能重建（换号 `rebind_bot`，`registry.py:164-178`） | `on_online`/`rebind` 路径重建 `self._strategy`（或 strategy 内部每次取 `runtime`，参考 `base.py:700-704` 的 `_bot()` 实时绑定写法） |
| 多租约"先到先得"语义 | 仍是"第一个租约的 owner 生效"（`registry.py:91-99`） | 不变，符合 `FEATURE_REGISTRY.md:221-228` |
| 卸载还原 | `restore()` 用 `snapshot.get(attr) or default_value()`（`controllers.py:194-199`） | A 路线下快照是"接管前的 `runtime.output`（通常 None）"→ 还原成 `None` = 框架按配置选（`controllers.py:263-265`）语义正确 |

### 3.3 路线 B：FeatureRegistry 支持"能力对象注册"

**做法**：owner 仍是 Module，但 Module 声明"我提供哪个能力对象"，控制器装对象而不是装 owner。

| 改动点 | file:line（现状 → 改法） | 行数 |
|---|---|---|
| `FeatureController` 增加解析协议（`resolve_capability(owner, feature_id)`），默认回落 owner | `features/base.py:17-50` | +12 |
| `FeatureLease` 增加 `capability` 字段 | `features/base.py:65-73`（已有 `providers` 空字段可复用） | +3 |
| `RuntimeSlotFeatureController`：`_usable` 校验**对象**、`suppress_with_owner` 装对象、`on_owner_changed` 重新解析、多租约按 capability 快照 | `controllers.py:142-207` | +40/-25 |
| `FeatureRegistry.suppress/release/release_owner/acquire_module` 传 capability；`query` 输出的 owner 名与 health | `features/registry.py:67-99`、`:101-126`、`:128-153`、`:155-169`、`:172-191` | +20/-8 |
| 三个模块加 `def capability_object(self, feature_id)` | 各 `main.py` | +5 × 3 |
| 文档承诺必须改 | `docs/FEATURE_REGISTRY.md:91-114`、`docs/MODULE_DEV.md:1319-1333` 都写着"接管者 = 你的模块对象" | 2 篇 |

**风险**：① 交棒/多租约/快照语义要按对象维度重算（`on_owner_changed` 现在只 `setattr(runtime, attr, owner)`，`controllers.py:201-207`）；② 第三方按旧文档实现"模块即接管者"会突然不工作 → 必须写兼容分支（owner 自身合格就用 owner）；③ 改动落在 4 个**所有装槽型能力共用**的文件上，回归面覆盖 `assembly`/`provider_invoker`/`output` 三条链 + `tests/plugin/test_lifecycle_gate.py` 等既有闸门。

### 3.4 A/B 对比与推荐

| 维度 | A：Module 实现契约 | B：能力对象注册 |
|---|---|---|
| 产品代码改动 | ≈ +30 / -30 行，全在插件侧 | ≈ +120 / -40 行，跨 4 个框架文件 |
| 框架回归面 | 0（只动可见性闸门） | 3 个槽型能力的全部路径 |
| 与现有文档契约 | **一致**（`FEATURE_REGISTRY.md:91-114`） | 冲突，需改 2 篇文档 + 兼容分支 |
| 能力对象的独立性 | 被 Module 包一层（够用） | 原生支持"一个模块多个能力对象"/非模块来源 |
| 现状需求是否覆盖 | ✅ 3 个新模块都是 1 个能力对象 | 无增量价值 |
| 推荐 | ✅ **A** | ❌ 以后有"非模块来源能力"再演进（届时 `FeatureLease.capability` 可向前兼容加） |

> 补充：**`llm_trigger` 不该声明 `supersedes`**（它没有可接管的槽，主动/定时是业务面：`FEATURE_REGISTRY.md:19-20,57-60`）。它的"可见性"只能靠 §3.5 Gate 4 + §7 降级表，不要给它编一个假 feature_id。

### 3.5 "接管失败必须可见"的实现（与 A/B 无关，必须单独做）

| 闸 | 位置 | 行为 | 量级 |
|---|---|---|---|
| **Gate 1 启动期硬校验** | `loader.py:339-366` 的 `activate()` 前置 | 对 `instance.supersedes` 中每个 feature：`controller = services.features.get(fid)`；`controller is None` → error；调新增的 `controller.check_owner(instance)` 返回缺失方法名 → 非空则 **error + `_record_load_error` + 不 acquire**。`on_load` 抛异常时同样不 acquire | +20 行；`FeatureController.check_owner`（`features/base.py:17-50`）+8 行，`RuntimeSlotFeatureController.check_owner` 复用 `_usable`（`controllers.py:142-143`） |
| **Gate 2 租约结果回传** | `features/registry.py:155-165` | `acquire_module` 从返回 `int` 改为返回 `(granted, rejected:[{feature_id, reason}])`（或在 `FeatureLease` 上加 `rejected`）；`activate` 拿到 rejected 非空 → 写 `instance.lease_failures` + error | +12 行 |
| **Gate 3 告警级别与口径** | `controllers.py:177-182`、`:183-188`、`registry.py:83-90`、`loader.py:359-361`、`webui/api/modules.py:186-227` | `warning` → `error`；文案必须含"**该能力不会被接管**"与下一步（"检查模块是否实现 X()／重启"）。运行期仍不抛（不炸一轮请求），但状态必须错 | 改 6 处字符串 + 级别 |
| **Gate 4 可查询 + 可展示** | `features/registry.py:172-191` 的 `query()` 增加 `health: ok\|degraded\|rejected` 与 `missing_methods`；`webui/api/modules.py:280-284`（`/modules/features`）与模块卡片透出；`readiness_service.py:254-263` 增加"核心能力接管"检查项（现成的 `LEVEL_WARN` 机制）；`ModulesPage.vue:489`（`hasIssues` 告警条）与 `:612-615`（supersedes chip）直接复用 | +18 行 + 1 个就绪检查 + 前端 2 处小改 |

**验收契约（写测试）**：
1. 模块声明 `supersedes=("output",)` 但不实现 `respond` → 启动后 `features.query("output", 123)["suppressed"] is False` **且** 模块 `status == "error"` **且** 日志有 1 条 `ERROR`（现状：只有 WARNING 且显示已启用）。
2. 实现了 `respond` 的模块 → `runtime.output is module`、`query()["owners"][0]["module"] == "llm_delivery"`、禁用后 `runtime.output is None`。
3. `on_load` 抛异常 → 不建立租约、`runtime.output` 未被指向该模块、`load_error` 非空。

---

## 4. 旧模块退场：shim 与删除时点

### 4.1 shim 最小实现草图

原则：**shim 只做两件事——打 deprecated 日志、不产生任何行为**。绝不 `import` 新模块去"转调"（那会让新旧两份接管撞车）。

```python
# app/plugins/llm_assembly/main.py（shim 版，约 30 行）
"""【已废弃】组装策略已并入 `llm_prompt`（见 docs/plan/three-core-modules.md）。

保留原因：让存量 bindings 行与用户配置有归宿、可回滚；
本模块**不再接管任何能力、不再注册任何钩子**，启用与否都不影响行为。
删除时点：迁移脚本在所有账号跑过 >= 2 个 release 之后（见 05-skeleton.md §4.2）。
"""
from app.core.logger import logger
from app.plugin import BaseModule


class Module(BaseModule):
    name = "组装策略（已并入提示词组装）"
    sign = "LlmAssemblyDeprecated"
    description = "已废弃：能力并入 llm_prompt，仅用于迁移期"
    permission = "everyone"
    subscribe = ()
    category = "LLM"
    hidden = True          # 模块页默认隐藏（base.py:332），不诱导用户去开
    deprecated = True      # 新增类属性：仅作标记，框架不校验
    migrate_to = "llm_prompt"
    provides = ()          # ← 关键：清空，否则会与 llm_prompt 抢槽
    supersedes = ()
    default_config = {}    # 不再持有键；旧值由迁移脚本搬到新模块

    async def on_load(self) -> None:
        logger.add_info(str(self.bot_id)).warning(
            f"[Deprecated] {self.module_name} 已并入 {self.migrate_to}："
            "本模块不再接管任何能力；配置已由迁移脚本转移，请在模块页停用它"
        )
    # on_unload / on_online / on_offline 全空（基类默认即可）
```

| 注意 | 说明 |
|---|---|
| `provides`/`supersedes` 必须清空 | 否则 shim 与 `llm_prompt` 同时声明 `assembly` → 多租约"先到先得"会让你以为新模块失效（`registry.py:91-99`） |
| `hidden=True` | `base.py:332` 已支持；避免用户在模块页看到两个"组装策略" |
| 不写 `deprecated` 到 `module.json` | `manifest.py` 不校验该字段；写了只会进 `instance.manifest` 的 raw，**没人读**。UI 的"已废弃"角标应读类属性 `/modules` 接口透出（`webui/api/modules.py` 的模块 payload） |
| 目录不删、代码不 import 新模块 | 旧插件可能被用户在 `data/module/plugins/` 侧有副本；跨模块 import 会破坏"插件按合成包名加载"的隔离（`loader.py:369-419`） |

### 4.2 shim 何时删 & 数据不丢

| 阶段 | 动作 | 数据 |
|---|---|---|
| T0（本次重构） | 9 个旧目录改成 shim（仅 `llm_knowledge` 无目录，见 §1） | 旧 bindings 行**保留但 `enabled` 置 0**；配置内容复制到新模块行 + 快照进 `kv` |
| T0+1 release | 观察期：模块页显示"已废弃"，日志统计 shim 被启用次数 | 不动 |
| T0+2 release | 删 shim 目录 + 同提交删 `tests/plugins/test_*` 对应用例（AGENTS.md：删模块同提交删测试） | **同提交**清理旧 bindings 行（此时才能删，见 §5.5） |
| 任意时刻 | 回滚 = 恢复 `backups/app.core-migration.<ts>.db` 或从 `kv` 快照重放 | 见 §5.4 |

---

## 5. 配置 / 启用位迁移

### 5.1 输入事实（实测，必须按这个设计）

| 事实 | 证据 |
|---|---|
| 唯一存储是 `bindings`（`capability_id`=模块名；`scope_type ∈ {global,account,channel}`；列：`enabled/config_json/permission_json/priority/updated_at` —— **没有元数据列**，无法就地标 deprecated） | `binding_store.py:45-47`、`:212-229`、`list_bindings :49-65` |
| 原始行读取必须用 `get_binding(capability_id, scope_type, scope_id)`；**不能用 `get_module_authority`**——后者会对"无行"的模块返回 `module_default_enabled`（老账号=True），会把 6 个关着的模块迁成开启 | `binding_store.py:117-129` vs `:469-477` + `:456-467`（`modules_default_enabled`，账号 123=1、bot1=0，见 `bots` 表） |
| 写入用 `save_binding`（先落库成功再换内存，失败抛出）而不是 `set_binding`（先改内存后台落库） | `binding_store.py:187-210` vs `:159-185` |
| 现有 10 行 → 5 有配置 / 3 enabled=1 / 1 孤儿无目录 | §1 实测 |
| 幂等范例两种：`kv` 标记（`dialogue_policy_migration.py:36-37,81-84`）与"源已消失"（`migrate_v3_split_config.py` 的设计说明 + `:109-112` 模块已有值优先） | 同左 |

### 5.2 行级映射（9→3 与 enabled 裁决）

| 新模块 | 吸收的旧 `capability_id` | 新 `enabled` 裁决 | 账号 123 预期 |
|---|---|---|---|
| `llm_prompt` | `llm_assembly`(7 键) + `llm_message_meta`(14) + `llm_enhance`(1) + `llm_context.referent_prompt_enable` + `llm_proactive.*_prompt*`(5) | **主来源 = `llm_assembly.enabled`**；`llm_message_meta.enabled` 作为并列来源取 OR；`llm_enhance` **不计入**（见下方 ⚠️） | `llm_assembly=0`、`llm_message_meta=0` → **`llm_prompt` = 0** |
| `llm_delivery` | `llm_output`(24) + `llm_proactive.stream_proactive_enabled` + `llm_schedule.stream_scheduled_enabled` | 主来源 = `llm_output.enabled` | `llm_output=1` → **`llm_delivery` = 1** |
| `llm_trigger` | `llm_proactive`(17-5) + `llm_schedule`(3) | OR(`llm_proactive.enabled`, `llm_schedule.enabled`) | 两个都 0 → **`llm_trigger` = 0** |

> ⚠️ **需 Lead 拍板（P1）**：若把 `llm_enhance.enabled` 计入 `llm_prompt` 的 OR，账号 123 会因为"只想开 debug_prompt"而把整个提示词模块打开——语义放大。建议：`debug_prompt` 作为一个普通键迁过去，但**不参与 enabled 裁决**，同时迁移日志显式打一行"`llm_enhance` 的 debug 开关已带走，模块启用位不跟随"。

**键级映射不重复造表**：以 task-4 产物 `logs/review/plan/04-keymap.md` 为唯一来源，代码里落成一张纯函数表：

```python
# app/application/core_modules_migration.py
#: 旧键 → (新模块, 新键, 转换函数)；无转换写 None
KEYMAP: dict[str, tuple[str, str, Callable[[Any], Any] | None]] = {
    "speech_via_tool":          ("llm_delivery", "mode", _mode_from_speech_flag),
    "stream_output":            ("llm_delivery", "mode", _mode_from_stream_flag),
    "stream_sentence_max_length": ("llm_delivery", "sentence_max_length", None),
    "include_time":             ("llm_prompt", "time_position", _time_pos_from_bool),
    "user_message_format":      ("llm_prompt", "user_line_format", None),
    "behavior_rules_override":  ("llm_prompt", "behavior_rules", None),
    "debug_prompt":             ("llm_prompt", "debug_prompt", None),
    # …全量见 04-keymap.md
}
```

**冲突裁决（同一新键被多个旧键指向）**——固定优先级，胜负必须打日志：

| 新键 | 优先级（高→低） | 理由 |
|---|---|---|
| `llm_delivery.mode` | `mode`(若已存在) > `speech_via_tool` > `stream_output` | `mode` 是唯一事实源；`speech` 比 `stream` 更具体（更"窄"的语义优先） |
| `llm_prompt.time_position` | `prompt_time_position` > `include_time`(bool→三态) | 显式三态优先于布尔推导 |
| `llm_prompt.user_line_format` | `user_message_format` > `meta_sender_style`/`meta_sent_style` | 词表收敛见 consolidation §3.3（词表失配 4 个键） |

### 5.3 脚本签名、幂等、回滚、逐条日志

```python
# app/application/core_modules_migration.py
MIGRATION_FLAG = "three_core_modules_migrated_v1"

@dataclass
class MigrationReport:
    accounts: int
    written: dict[str, int]        # 新模块 → 写入键数
    enabled: dict[str, bool]       # 新模块 → 裁决后启用位
    dropped: dict[str, dict]       # 账号 → {旧键: 原值}（无归宿，落盘不静默丢）
    disabled_old: list[str]        # 被置 0 的旧 capability_id
    skipped: list[str]             # 已有新行而被跳过的账号/键
    errors: list[dict]             # [{account, module, error}]

async def migrate_core_module_bindings(config_service) -> MigrationReport: ...
```

| 要求 | 实现 |
|---|---|
| **幂等** | 双保险：① `kv[MIGRATION_FLAG]=="1"` 时只做"补漏"（仍算 plan，但 0 变化直接返回）；② 合并规则 `merged = {**migrated, **existing_new}`（**新行已有键优先**，照抄 `scripts/migrate_v3_split_config.py:109-112` 的口径），重跑天然收敛。flag 只在**全部账号全部成功**后写（`dialogue_policy_migration.py:36-37,81` 同款；无账号时不写，`:38-40`） |
| **可重跑** | 同上；另提供 `--dry-run` 只打 plan（`migrate_v3_split_config.py:133` 的参数 + `apply_split` 内的 dry-run 早返回分支） |
| **失败回滚** | ① 跑前 `shutil.copy2(db_path, backups/app.core-migration.<ts>.db)`（照抄 `migrate_v3_split_config.py:137-148`）；② 用 `save_binding`（先落库，失败内存不动，`binding_store.py:187-210`）；③ 单账号失败 → 记 `errors` + **不写 flag** + 继续下一个账号（其他账号照常，`agent_prompt_preset_migration.py:36-42` 同款）；④ 旧行内容先整体快照进 `kv["three_core_legacy_snapshot"]`（JSON），支持 `--rollback` 从快照重放 |
| **逐条日志** | 每个账号一行：`[Migrate] 123 llm_output→llm_delivery 24→9 keys, enabled 1→1`；每个被丢弃的键一行 `dropped stream_send_by_sentence=... (0 读取点)`；结尾一行汇总。全部走 `logger`，不用 print（`PLUGIN_STYLE.md:80`） |
| **dry-run 输出** | 账号 × 新模块 × 键数 × enabled × dropped 清单，便于 Lead 在真跑前拍板 |

### 5.4 插入 `start.py`（真实是 `app/bootstrap.py`）的具体位置

```
app/bootstrap.py::run()
  ...
  config_service = _container.get(ConfigService)      # :519
  await config_service.init()                        # :520  ← bindings 已加载
  await migrate_legacy_dialog_policies(...)          # :524
  await migrate_trigger_overrides(...)               # :528
★ await migrate_core_module_bindings(config_service)  # ← 插在这里（新的一步，独立可提交）
  ... prompt_preset_service.load_overrides(...)      # :538
  ...
  registry.discover()                                # :576  ← 必须早于它
  await bot_service.ensure_accounts()                 # :584  ← 必须早于它（首个装配就用新配置）
```

为什么不放更后面：`ensure_accounts()`（`:584`）会按各账号装配模块实例，`on_load` 读的是 `bindings`；迁移若在其后，第一个小时的运行仍是旧配置（且 shim 已清空 `supersedes`，会出现"旧模块不接管、新模块没配置"的窗口）。

为什么不用 `start.py`：`start.py:1-30` 的职责是 venv/toolchain/依赖/launch，`main.py:5-8` 才进入 `app.bootstrap.run()`；在 `start.py` 里加迁移会破坏"只准标准库"的硬约束（`start.py` 文档头）。

顺带（同批但独立提交）：`tests/application/test_core_modules_migration.py` 锁四条契约——幂等（跑两次第二次 0 变化）、不覆盖新行已有值、无行模块不被 `module_default_enabled` 误开、孤儿行（`llm_knowledge`）落 dropped 不静默。

### 5.5 迁移后旧行：禁用而非删除

| 方案 | 评价 | 建议 |
|---|---|---|
| 直接 `DELETE FROM bindings WHERE capability_id IN (...)` | 干净，但 `binding_store.py` **没有公开的 delete API**（只有 `_persist_binding` 在行缺失时删，`:231-240`）→ 要么加 `delete_binding()`（+12 行），要么脚本直接走 SQL | 留到 T0+2 与 shim 同删 |
| **`enabled=False` + 清 `config_json`？** | 清 config 会丢回滚依据 | ❌ 不清 config |
| **`enabled=False` 保留原 config（推荐）** | 旧行变成"已停用的历史快照"，UI 里显示为停用（用户的模块页本来就这么显示）；重启也不会重新接管（`registry.py:156-160` 已禁用模块不 acquire；`loader.py:349-355` 已禁用跳过 on_load） | ✅ T0 就这么做；配置值同时已复制到新行，回滚靠 `kv` 快照 + DB 备份 |

> 与 `_migrate_bindings_if_needed` 的口径保持一致：那里旧表就是"保留为回滚快照"（`binding_store.py:259` 注释「幂等：kv 标记之后跳过；旧表保留为回滚快照」）。我们沿用同一哲学：**旧行 = 回滚快照，不是垃圾**。

---

## 6. UI / 清单

### 6.1 新模块的 schema 能直接渲染吗？——能，两个缺口

| 能力 | 后端白名单 | 前端渲染 | 三新模块是否用得到 |
|---|---|---|---|
| `boolean/bool` | `schema_utils.py:23-26` | `FieldControl.vue:20,172-179`（v-switch） | ✅ debug 开关等 |
| `number/integer/float`（含 `min/max/step`） | 同上 | `FieldControl.vue:21,130-136,230-238` | ✅ 气泡上限/字数/间隔 ms |
| `select`（`options` 支持 dict 与数组两种写法） | `:25` + `:64-69` 校验 | `FieldControl.vue:22,138-146,199-207` | ✅ `mode`、`time_position`、`user_line_format` |
| `textarea`（`rows`） | `:25` | `FieldControl.vue:23,209-218` | ✅ 行为规则/prompt 模板 |
| `text/string/password/time` | `:24-26` | `FieldControl.vue:221-248` | ⚠️ 仅局部（时段可用 `time`） |
| `string_list/list/dynamic/repeater` | `:28`（`COMPLEX_TYPES`） | `ConfigForm.vue:207-232` → `StringListWidget/ListWidget/DynamicWidget/RepeaterWidget` | ✅ 会话白名单、MCP 服务器列表 |
| `resource/resources`（下拉 + 悬挂值占位） | `:30` + `:57-62` 校验 | `FieldControl.vue:27,44-128,181-197` | ✅ embedding 模型选择（`memory_embedding_model_id` 同款） |
| `group`（分组卡片） | `:32` + `split_schema :93-102` | `ConfigForm.vue:34-75,178-204` | ✅ 建议每组 ≤6 键 |
| **`multiselect`（枚举多选）** | ❌ 不在白名单（会记 `unknown_type`，`schema_utils.py:54-55`） | ❌ 无组件 | ⚠️ `TurnPlan.meta_fields`（"要渲染哪些元信息"）天然是多选枚举；不用它就只能 `string_list`（无候选下拉，用户手打键名 = 新一代"开关不明"） |

**落地口径（按不新增框架类型的最省路径）**：元信息项先用 `string_list` + `options` 提示 + `description` 列全部合法值；若 Lead 认可体验价值，再单开一个提交加 `multiselect`（三处：`schema_utils.py:23-32` 白名单 + `FieldControl.vue` 新增分支 + `ConfigForm.vue` 分支，约 +40 行）。

**链路事实**：后端 `/modules` 里 `config_schema` 走 `webui_schema(...)`（`app/application/bot_service.py:336-337`，含 `issues` 诊断），前端模块配置页 `ModuleConfigPage.vue:354-371` 用 `ConfigForm` 渲染，`ConfigForm` 直接吃 `{groups, items}`。**新模块的 `SCHEMA` 只要用上面这些 type，不需要改 UI 一行。**

### 6.2 `module.json` 与类属性：谁说了算、怎么保持一致

| 事实 | file:line |
|---|---|
| 加载期**以清单为准**：`instance.version`、`instance.display_name`、`instance.api_version`、`instance.manifest` 全来自 `Manifest`；`instance.name`（代码内引用名）仍来自类属性 | `loader.py:154-158` |
| 清单必填：`name`（且必须是合法标识符）、`display_name`、`version`；`api_version ∈ {1}`（当前）；`entry` 默认 `main.py` | `manifest.py:70-93`、`:26-28` |
| 内置模块缺清单 → 只 WARN，类属性兜底（`version=0.0.0`）；**第三方缺清单 → 拒绝加载**；第三方清单 `name` 与目录名不一致 → 拒绝 | `loader.py:69-96`、`:121-127` |
| 一致性机制 = 生成器，不是手写：`scripts/gen_module_manifests.py` 用 AST 读 `class Module` 的常量（`ATTR_MAP`：`display_name←name`/`sign`/`description`/`version`），`--check` 供 CI | `scripts/gen_module_manifests.py:24-31,50-90,93-96` |
| `provides`/`supersedes`/`hidden`/`deprecated` **不在清单 schema 里**，只在类属性；写进 `module.json` 只会进 `raw`，无人读 | `manifest.py:57-104` 字段全集 |

**流程写进 checklist**：改类属性 → `python scripts/gen_module_manifests.py --force` → 提交 `module.json` 与 `main.py` 同一提交 → CI `--check`。

---

## 7. 降级行为表（本轮最该治的病）

### 7.1 三个模块各自的降级契约

| 模块 | 触发条件 | 系统**退化成什么**（具体到代码路径） | 用户可感知后果 | 必须留痕（级别 / 内容） | UI 呈现 |
|---|---|---|---|---|---|
| **`llm_prompt`** | `authority.enabled=false` | `assembly` 槽**完全不被接管** → `runtime.assembler` 仍是框架默认 `PromptAssembler`（`controllers.py:223-227` 的 `default_value`）；段表 = `builtin_sections.default_sections()` 内置 12 段（`sections.py:133-137`）；user 行格式回到框架默认；`ctx.state["message_meta_injected"]` 由原 `llm_message_meta` 写入（`docs/reviews/...-consolidation.md:189-194`），模块不在 → **格式图例永不注入** | 模型看不到"谁在说话/几点了/引用的是谁"；人格/行为段退回内置文案；prompt 里写"格式见上"而实际无图例 | `WARNING` 启动一条：`[Degrade] llm_prompt 未接管 assembly → 使用内置 12 段表；user_line_format=default；元信息图例未注入`；每轮 `debug` 一行 `assembler=PromptAssembler(builtin)` | 模块卡片灰 + 红条"提示词组装使用内置默认"（模块页）；就绪清单新增 `modules` 子项 warn（`readiness_service.py:254-263` 机制） |
| **`llm_prompt`** | `on_load` 抛异常 / 装配失败 | 同上 **+ `status="error"` + `load_error` 文案** | 同上，且用户能看到"插件加载失败" | `ERROR`：`llm_prompt on_load 异常: …（回退内置段表）` + `_record_load_error`（`registry.py:508-510`） | 卡片 error 态（`catalog.py:22,35`）；就绪检查 warn |
| **`llm_delivery`** | `enabled=false`（正常禁用） | `runtime.output` 保持 `None`（`controllers.py:263-265`）→ `resolve_output()` 回退 `select_default_output(runtime.config)`（`output.py:294-307` → `:241-253`）→ 读 **agent 行** 的 `stream_output` / `speech_via_tool`（账号 123：均为 `false`）→ **`BlockingOutput` 一次性发** | 原本 `mode=stream` 的用户会觉得"回复不再逐句、一次到"；气泡上限/节奏配置全部失效 | `WARNING` 启动一条：`[Degrade] llm_delivery 未接管 output → 回退 BlockingOutput（agent.stream_output=False, speech_via_tool=False）` | 页顶红条"发言方式回退为一次性发送（原：流式）"；`/modules/features` 显示 `output.suppressed=false` |
| **`llm_delivery`** ⚠️ | `enabled=false` **但 agent 行残留 `speech_via_tool=true`** | 回退后**仍按 speech 语义贴协议**（协议文本读 agent 配置）+ `send_message` 工具在 → **模型只思考不说话 → 整轮静默**。这是 `docs/reviews/...-consolidation.md:214-249` 记录过、真实发生过的 P0 | 用户 @ 了却不回，且**日志可能一片安静** | `ERROR` **不允许静默**：`[Degrade] llm_delivery 未接管而 agent.speech_via_tool=true：发言通道缺输出实现，本轮将强制 BlockingOutput 并摘掉 send_message 工具` + 每轮 `WARNING` 附 `silent_risk=true` | 首页红条 + 就绪清单 **blocker 级**提示"发言通道配置与环境不一致" |
| **`llm_trigger`** | `enabled=false`（正常禁用） | 主动消息 manager 与定时 scheduler **不注册/不启动** → 已保存的定时任务不执行、到点不主动开口；数据仍在 `data/llm/<bot>/`（不在 bindings 里） | **当前就是这样，而且完全没有提示**——用户以为"到点会说"，实际永远不说 | `INFO` 启动一条：`[Degrade] llm_trigger 已禁用：主动消息与 N 条定时任务不会执行（数据保留）` + 每个跳过点 `debug` | 「主动与定时」页红条 + 任务列表每条标"未生效（模块已停用）" |
| **三者都关** | 最小回路 | 内置 12 段表 + `BlockingOutput` + 无主动/定时 | 仍能被动回复，但格式/元信息/发言方式全默认 | `WARNING` 一条汇总 `[Degrade] 最小模式：prompt/delivery/trigger 均未接管` | 首页 readiness 一条 warn，文案指向模块页 |

### 7.2 明确**不允许**的降级方式（把现有的病逐条钉死）

| 不允许 | 现状证据 | 替代 |
|---|---|---|
| 租约被拒但运行时被 `on_load` 手工接管 | `controllers.py:183-188` + `llm_output/main.py:111-116` | 路线 A + Gate 1/2（§3） |
| 模块显示"已启用"但实际没接管 | `loader.py:357-361` 只 warning | Gate 3/4 |
| 贴了发言协议却没有输出层实现 | consolidation §3.4 第 4 点（`turn.py` 全文无 `speech`） | §7.1 第 4 行 fail-loud |
| "没发出去"没有任何日志 | consolidation 验收契约 5（`:372`） | 每轮一行 `TurnPlan` 摘要（source/speak_mode/spoken/原因）——**三个新模块统一从 `TurnPlan` 出这行** |
| 禁用模块后配置静默失效、无 UI 提示 | 现在模块页只有"已停用"灰标（`ModuleConfigPage.vue:40,278`） | 每模块一条"禁用会退化成什么"的 `description`/UI 红条，文案来自 §7.1 |

---

## 8. 不确定项 & 需要 Lead 拍板的点

### 8.1 需要拍板（阻塞落地）

| # | 问题 | 我的建议 | 影响 |
|---|---|---|---|
| P1 | **槽接管走 A 还是 B** | A（§3.4） | 决定改动面：插件侧 30 行 vs 框架 120 行 + 2 篇文档 |
| P2 | **是否接受"A + 四道可见性闸"作为同一个重构的一部分** | 接受，且 Gates 先做（它们对 9 个旧模块也立即生效） | 只做 A 不做 Gates = 换了个方式继续静默 |
| P3 | **`llm_enhance.debug_prompt` 是否参与 `llm_prompt.enabled` 裁决** | ❌ 不参与（否则账号 123 会因调 debug 而打开整个 prompt 模块） | 影响账号 123 迁移后的实际形态（`llm_prompt` 0 vs 1） |
| P4 | **旧 bindings 行**：T0 只禁用保留，还是直接删 | 只禁用保留（`kv` 快照 + DB 备份），T0+2 删 shim 时同删 | 决定是否要给 `binding_store.py` 加 `delete_binding()`（现无公开 API，`:231-240` 只有私有删除路径） |
| P5 | **shim 保留几个 release** | ≥2 个 release（T0 改 shim → 观察 → T0+2 删目录+删行） | 决定 `docs/MODULE_DEV.md` 的废弃公告与 CI 豁免 |
| P6 | **元信息项多选是否新增 `multiselect` schema 类型** | 先 `string_list` 落地，体验不够再单开提交 | 决定是否动 `schema_utils.py` 白名单 + 2 个 Vue 文件 |
| P7 | **`llm_trigger` 是否也走 `build_plan`（依赖 consolidation 缺陷 3 的修复）** | 是——但前提是先修"主动/定时传 `BaseModule` 当 runtime"（consolidation 缺陷 3，`manager.py:238,286` / `scheduler.py:448,516`） | 若不修，trigger 模块只是把坏实现搬家 |
| P8 | **三个新模块的 `order`/`category`/是否 `pinned`** | `category="LLM"`、`order=10/20/30`、不 pinned；shim `hidden=True` | 影响模块页排序，非阻塞 |

### 8.2 不确定项（不要当结论）

| # | 事项 | 现状 |
|---|---|---|
| U1 | `llm_knowledge` 的 3 个键最终归宿 | 目录已删、`MODULE_KEYS` 仍占位（`config_partition.py:307-311`）；consolidation §8-5 也把它列为待拍板。迁移脚本按"无归宿 → dropped + 落盘"处理，需要 Lead 决定是迁进 `llm_prompt` 还是删键 |
| U2 | 权限位（`permission_json`：`group_mode/group_list/user_mode/user_list`）要不要跟着迁 | 三个新模块 `permission="everyone"`、`subscribe=()`，不按群响应（`base.py:325,344`）→ 语义上权限位无意义；但旧行里账号 123 全是 `{}`，无法从数据判断历史用法。建议：**不迁**，旧行保留原 permission 作快照 |
| U3 | `bot1`（`modules_default_enabled=0`）账号的迁移结果 | 该账号 0 条 `llm_*` binding → 迁移后应保持"无行 = 默认关"（`binding_store.py:456-467`）。若迁移脚本给所有账号都**主动建行**，会把这个账号的新模块变成"有行 enabled=0"（无害但语义变了）。建议：**只给已有旧行的账号建行** |
| U4 | `FeatureLease.providers` 字段（`features/base.py:72`）是否已有人用 | 全仓未见写入点；若无人用，B 路线可以复用它存 capability（不影响 A） |
| U5 | 审计/日志面：`TurnPlan` 摘要打哪一级 | consolidation 验收 5 要求"静默必留痕"；`logs/user.log` 里的 `[Prompt]` 目前只打段长度（`:222-223` 证据）。新模块应统一 `logger.info` 一行，但具体字段集需与 log-forensics 侧对齐 |

---

## 附录 A · 实施提交顺序（一步一提交，可独立回滚）

| # | 提交 | 内容 | 依赖 |
|---|---|---|---|
| 1 | `fix(plugin): 接管失败升级为 error 并记录 load_error` | Gate 3（`loader.py:357-361`、`controllers.py:177-188`、`registry.py:83-90`、`webui/api/modules.py:186-227`） | 无 |
| 2 | `feat(plugin): 启动期校验接管契约，拒绝时不留租约` | Gate 1/2（`activate` 前置 + `check_owner` + `acquire_module` 返回值） | #1 |
| 3 | `feat(webui,readiness): 暴露能力接管 health 并纳入就绪清单` | Gate 4（`registry.query` + `/modules/features` + `readiness_service.py:254-263` + `ModulesPage.vue:489`） | #2 |
| 4 | `feat(plugin): 新建 llm_delivery（接管 output 槽）` | 目录 + `module.json` + 类属性 + `respond()` 转发；旧 `llm_output` 改 shim | #2 |
| 5 | `feat(plugin): 新建 llm_prompt（接管 assembly 槽）` | 同上；旧 `llm_assembly`/`llm_message_meta`/`llm_enhance` 改 shim | #2 |
| 6 | `feat(plugin): 新建 llm_trigger（主动与定时统一入口）` | 无槽；`on_load` 注册 + `on_online/offline`；旧 `llm_proactive`/`llm_schedule` 改 shim | #2、P7 |
| 7 | `feat(migrate): 9 个旧模块 bindings → 3 个新模块（幂等/可回滚）` | `core_modules_migration.py` + `bootstrap.py` 一行插桩 + 契约测试 | #4-6 |
| 8 | `chore(config): 补 config_partition 新键归属 + 删废弃键` | 键归属表与新 schema 对齐（否则 `stale_keys()`/`unknown_keys()` 闸门会红） | #4-6 |
| 9 | `docs(plugin): MODULE_DEV/FEATURE_REGISTRY 同步槽接管与废弃策略` | 文档 | 全部 |
| T0+2 | `refactor(plugin): 删除 9 个 shim 目录与旧 bindings 行` | 同提交删对应测试 | 观察期结束 |

## 附录 B · 关键证据索引（file:line）

| 主题 | 证据 |
|---|---|
| 槽接管拒绝逻辑 | `app/plugin/features/controllers.py:142-143,171-192,218-227,258-265`；`app/plugin/features/registry.py:67-99,101-126,155-169,172-191`；`app/plugin/features/base.py:17-50,65-73` |
| 手工装槽（现状病根） | `app/plugins/llm_output/main.py:105-132,134-149`；`app/plugins/llm_assembly/main.py:59-77`；`app/plugins/llm_message_meta/main.py:72-103` |
| 静默失败 | `app/plugin/loader.py:349-366`；`app/webui/api/modules.py:167-227`；`app/plugin/registry.py:458-510` |
| 元数据唯一来源 | `app/plugin/manifest.py:57-165`；`app/plugin/loader.py:69-96,149-161`；`scripts/gen_module_manifests.py:24-31` |
| 配置存储 | `app/infrastructure/config/binding_store.py:45-65,117-129,159-210,256-343,423-519` |
| 读取链（含待修的短路） | `app/agent/policy.py:34-52,70-85,98-112,148-153`；`app/agent/delivery/output.py:241-307` |
| 能力声明 / 段表 / 收集面 | `app/agent/capabilities.py:106-167`；`app/agent/assembly/sections.py:109-215,228-262`；`app/agent/loop/providers.py:103-153` |
| 迁移范例 | `scripts/migrate_v3_split_config.py:43-130,133-187`；`app/application/dialogue_policy_migration.py:27-84,89-133`；`app/application/agent_prompt_preset_migration.py:19-43`；`app/bootstrap.py:509-571` |
| schema / UI | `app/plugin/schema_utils.py:20-107`；`app/application/bot_service.py:335-337`；`app/webui/dashboard/src/components/config/FieldControl.vue:19-28`；`.../ConfigForm.vue:199-232`；`.../views/ModuleConfigPage.vue:354-371`；`.../views/ModulesPage.vue:489,612-615` |
| 降级可展示面 | `app/application/readiness_service.py:118-132,254-263`；`app/plugin/catalog.py:22,25-39` |
| 病案（不回/静默） | `docs/reviews/2026-10-31-assembly-output-consolidation.md:214-249,253-266,306-312,364-374` |
