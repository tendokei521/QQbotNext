# 插件接管 LLM 组装（AstrBot 迁移）— 改造完成记录

> **状态：全部完成，已归档。** 8/8 项框架改造 + 1 个参考插件落地，13 个提交
> （`10e604a` … `5687c16`），全量测试 **1071 passed**（起点 957），
> 改动过的代码文件与新增文件**零 ruff 违规**（存量 32 → 26）。
>
> 📦 **本文件是完成记录，不是待办清单。** 已移入 `docs/archive/`；
> 其中 §7 的未修项已转入当前待办跟踪（见下）。

## 0. 目标与结论

让**一个插件**在**单个账号**下接管 LLM 的消息组装与请求模式，把
`1/AstrBot-master` 的组装 1:1 复刻过来做迁移对账。原先识别的 8 个阻断点已全部消除。

**关键结论**：框架侧原本只有 `@llm_hook` 五个阶段，**全部在组装之外**（`pre_request`
在组装前、`post_response` 在请求后），插件既看不到也改不了真正发出去的 messages；
`register_provider` 也只能换 HTTP 适配器而非调用约定。现在补上了三个口子 + 一套对账工具。

## 1. 交付清单

| # | 改造 | commit | 关键文件 | 测试 |
|---|---|---|---|---|
| 1 | `post_assemble` 阶段 + 统一阶段执行器（4 条组装路径收口） | `10e604a` | `app/llm/hooks.py` `assembly.py` `pipeline.py` `context.py` | `test_llm_post_assemble.py`（14） |
| — | 修既有崩溃：`proactive` 里 `_max_tool_rounds` 先用后 import（主动消息全挂） | `7740f5a` | `app/llm/proactive.py` | `test_proactive.py`（+1，修复前必红） |
| 2 | `runtime.assembler` 槽 + `feature_id="assembly"` 声明式接管/交棒/还原 | `d2addcb` | `app/modules/features.py` `app/llm/manager.py` | `test_features.py`（+5） |
| 3 | `system_prompt_style`：多块 system 折成一整条（默认不变） | `a61df61` | `app/llm/assembly.py` `config.py` `config_schema.py` | `test_llm_assembly_fold.py`（8） |
| 4 | `extra_user_content_parts`（user 侧额外文本块）+ `place_images` 保留 | `873a279` | `app/llm/assembly.py` | `test_llm_user_content_parts.py`（10） |
| 5 | `ServiceAccess` 暴露 profile/role preset；RolePreset 扩 `begin_dialogs`/`tools`/`skills` | `6e0bd40` | `app/application/role_preset_service.py` `app/modules/base.py` | `test_role_preset_extended.py`（8） |
| 6 | 请求 messages 落盘 + WebUI 查询端点（与第三方逐条 diff） | `26d5ff4` | `app/llm/prompt_dump.py` `app/webui/api/prompt_dump.py` | `test_llm_prompt_dump.py`（14） |
| 7 | `runtime.invoker` 调用策略槽 + token 预算裁剪 | `98d7f7d` | `app/llm/providers/invoker.py` `app/llm/token_budget.py` | `test_llm_provider_invoker.py`（14）+ `test_llm_token_budget.py`（17） |
| 8 | 历史契约（快照 / 外部历史导入） | `c5027f0` | `app/llm/session.py` | `test_llm_history_contract.py`（18） |
| 9 | 文档同步（README / MODULE_DEV §4.7 / FEATURE_REGISTRY） | `23ca9fc` | 三份文档 | — |

中途留档：`bc117a1`（本文件初版，用于中断续接）。

## 2. 插件侧用法

```python
# module/modules/my_astrbot_assembly/main.py
from app.modules import BaseModule, llm_hook

class Module(BaseModule):
    name = "AstrBot 组装复刻"
    sign = "AstrBotAssembly"
    # 声明式接管（单账号粒度）：启用时装上，禁用/卸载自动还原，多接管者先到先得 + 交棒
    supersedes = ("assembly", "provider_invoker")

    # 1) 整体替换组装
    def build(self, req):
        # req 是一次请求的全部归一化输入：session_id / user_text / raw_user_text /
        # history / pre_history_text / referent_text / group_log_text / skill_blocks /
        # memory_text / extra_user_content_parts / available_tools / modalities / user_images
        return my_astrbot_assemble(req)

    # 2) 整体替换 provider 调用约定（第三方把工具循环放在 runner 层时用）
    async def chat(self, *, runtime, chain, messages, params):
        return await my_runner.chat(messages, params)

    async def stream(self, *, runtime, chain, messages, params):
        async for event in my_runner.stream(messages, params):
            yield event

    # 3) 只想事后微调（不想整体替换）时用钩子
    @llm_hook("post_assemble", event_type="*", order=50)
    async def tweak(self, ctx):
        ctx.messages = my_patch(ctx.messages)   # 置空 = 本轮不请求 LLM
```

历史侧（迁移时把外部上下文搬进来 / 用自己的渲染器）：

```python
sm = runtime.session_mgr                       # 按 bot_id 的进程级单例
sm.import_history("group_123", astrbot_history)      # 批量导入，自动建会话，脏条目计数丢弃
raw = sm.history_snapshot("group_123")               # 未投影原始条目（含 base.segments）
projected = sm.get_history("group_123", limit=50)    # 投影视图（可直接喂 messages）
```

### 配置键（Agent 配置页「会话 / 压缩」组，默认都不改变现有行为）

| 键 | 默认 | 用途 |
|---|---|---|
| `system_prompt_style` | `blocks` | `single` = 把所有 system 块折成一整条 |
| `prompt_dump_enable` | `False` | 每轮最终 messages 落 `data/llm/<bot_id>/prompt_dump.jsonl` |
| `prompt_dump_max_entries` | `20` | 滚动保留条数 |
| `context_token_budget` | `0` | >0 启用 token 预算裁剪（丢最旧历史，system 与本轮提问必留） |
| `context_token_chars_per_token` | `2.0` | 估算参数（防爆用，不计费） |

## 3. 与 AstrBot 对账的验收路径

1. 打开 `prompt_dump_enable`，把 `system_prompt_style` 切到 `single`；
2. 在该账号下跑一轮真实对话（或主动消息 / 定时任务）；
3. 取落盘最后一条，或 `GET /api/agent/prompt-dumps?bot_id=<qq>&session_id=group_xxx`；
4. 与 AstrBot 侧同轮 `messages` 逐条 diff（AstrBot 链路：
   `build_main_agent` → `ToolLoopAgentRunner.reset` → `_prepare_chat_payload`）；
5. 形状对不上时先看 `assembly.describe(req)` 的块-消息对位，确认哪块多/少，
   再用 `post_assemble` 钩子微调或 `build()` 整段复刻。

落盘的是 **post_assemble 之后、token 裁剪之后**的最终形态 —— 即真正发出去的东西。

## 4. 验证证据（实际跑过的）

| 项 | 结果 |
|---|---|
| 全量 `pytest -q` | **1045 passed**（起点 957，净增 88 例） |
| `ruff check`（会话改动过的 35 个 .py，逐文件对比 `7b88205` 基线） | **零新增违规**；合计 32 → 26（顺带修好 6 处存量） |
| 新增文件 ruff | 全部零违规 |
| 回归测试有效性 | proactive 修复用例在修复前实测必红（`UnboundLocalError`）；多租约交棒用例抓出过 `release_owner` 漏加交棒的真实漏洞 |
| 编译 | `compileall app` 通过 |

## 5. 刻意保留的行为（向后兼容）

- `system_prompt_style` / `prompt_dump_*` / `context_token_budget*` 默认值 = **原行为**；
- `ProviderInvoker` 默认实现原样转调 `chat_with_fallback` / `iter_stream_with_fallback`；
- `FeatureController.suppress_with_owner()` / `on_owner_changed()` 默认实现兼容既有与
  第三方控制器（不需要「谁在接管」的能力一行都不用改）；
- 唯一的行为变更：请求出口从「直接调 providers」改为「经 `resolve_invoker` 转发」。
  测试里打 `app.llm.chat.chat_with_fallback` 的补丁**不再生效**，要打
  `app.llm.providers.chat_with_fallback`（`tests/test_strip_parentheses.py` 已同步；
  该用例同时充当「请求确实经 invoker 转发」的反向证明）。

## 6. 参考插件：`module/modules/astrbot_alignment/`

`5687c16` 交付的 AstrBot 组装对齐插件（26 例测试），是这套接口的第一个真实使用者：

- `supersedes = ("assembly",)` 接管单账号的消息组装；`@llm_hook("pre_request")`
  在组装前预取群名（组装是同步的）；
- system prompt 段落顺序与 AstrBot 逐条对齐：安全段（最后执行但前插）→ GenUI →
  persona `\n# Persona Instructions\n\n{...}\n` → skills → router → workspace →
  local → 工具调用段 → live → 联网引用；
- 用户消息块顺序：正文（`prompt_prefix`）→ 媒体占位 → `<Quoted Message>` →
  `<system_reminder>` → 群聊上下文块 → 图片；单文本块降级为纯字符串；
- 历史默认走 `history_snapshot()` 只投影 `role`/`content`（与 AstrBot 的
  `Message.model_dump()` 一致），persona 的 `begin_dialogs` 前插；
- 原文常量单独放 `astrbot_prompts.py`（逐字，含 `Belows` 拼写这类上游特征）；
- 3 处刻意差异与未复刻项在插件 README 里如实列出。

**后续可选**（本次未做）：

- 连**主循环**一起复刻（trigger 判定、防抖/打断、历史落库、流式切句）→ 那是
  「接管 agent」路径：`supersedes = ("agent",)` + `@module_hook`；
- 若要复刻 AstrBot 的 runner 层工具循环（每轮重算 context），需要 `provider_invoker`
  槽之外再让 provider 层暴露「本轮中间 messages」——目前框架的 provider 内部自己跑
  工具循环，`runtime.invoker` 只能换单轮调用约定。

## 7. 顺带发现但**未修**的问题（与本次改造无关）

> **去向：** 第 1、2 条已转入待办跟踪（仍开放）；第 3 条是当时的过程性说明
> （"未混入提交"），事后无跟踪价值，**已废弃**。

1. **测试互相污染**：`tests/test_llm_assembly*.py` 与 `tests/test_group_log_integration.py`
   放进同一次子集运行，后者会红（`group_log store` 全局状态未清理）；全量按字母序
   正常。已实测**不带**本次新增文件也能复现 → 既有问题。
2. **`ModuleConfig.__getattr__` 陷阱**：缺失属性返回 `None`，使
   `hasattr(config, "set_session")` **恒为 True**，随后 `None(...)` 抛 `TypeError`。
   生产里是 `AgentConfig`（真有该方法）不受影响，但测试替身走到该分支会以误导性
   错误掩盖真实问题（本次修 proactive 时被它挡了一轮）。
3. ~~`scripts/requirements.bat`（进入会话前已改未提交）与未跟踪的 `qqbot_next.egg-info/`
   不是本次产物，**未混入任何提交**。~~（过程性说明，已废弃）

## 8. 复现命令

```bash
# 全量测试
venv\Scripts\python.exe -m pytest -q

# 本次新增测试（避开已知互相污染的 test_group_log_integration.py）
venv\Scripts\python.exe -m pytest tests/test_llm_post_assemble.py tests/test_llm_assembly_fold.py ^
  tests/test_llm_user_content_parts.py tests/test_features.py tests/test_role_preset_extended.py ^
  tests/test_llm_prompt_dump.py tests/test_llm_provider_invoker.py tests/test_llm_token_budget.py ^
  tests/test_llm_history_contract.py -q

# 本次会话全部改动一览
git log --oneline 7b88205..HEAD
git diff --stat 7b88205..HEAD
```
