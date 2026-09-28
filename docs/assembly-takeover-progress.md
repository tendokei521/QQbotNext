# 插件接管 LLM 组装（AstrBot 迁移）— 改造进度

> **状态（2026-09-28）**：8 项改造已完成 7 项并各自独立提交，全量测试 **1027 passed**。
> 第 8 项（历史契约）代码已写完但**单测未补**，暂存为 git stash（见 §5）。
> 本次会话共 8 个提交：`10e604a` … `98d7f7d`。

## 0. 目标

在框架侧补齐「缝」，让**一个插件**能够在**单个账号**下接管 LLM 的消息组装，
把 `1/AstrBot-master` 的组装与请求模式 1:1 复刻过来，用于迁移对账。

原始差距分析（会话中已给出）识别出 8 个阻断点，本文件记录它们的落地情况。

## 1. 已完成的改造

| # | 改造 | commit | 关键文件 | 新增测试 |
|---|---|---|---|---|
| 1 | `post_assemble` 钩子阶段 + 统一阶段执行器（4 条组装路径收口） | `10e604a` | `app/llm/hooks.py` `assembly.py` `pipeline.py` `context.py` | `test_llm_post_assemble.py`（14） |
| — | 修既有崩溃：`proactive` 里 `_max_tool_rounds` 先用后 import（主动消息全挂） | `7740f5a` | `app/llm/proactive.py` | `test_proactive.py`（+1） |
| 2 | `runtime.assembler` 注入点 + `feature_id="assembly"` 声明式接管/自动还原 | `d2addcb` | `app/modules/features.py` `app/llm/manager.py` | `test_features.py`（+5） |
| 3 | `system_prompt_style`：多块 system 折成一整条（默认不变） | `a61df61` | `app/llm/assembly.py` `config.py` `config_schema.py` | `test_llm_assembly_fold.py`（8） |
| 4 | `PromptRequest.extra_user_content_parts` + `place_images` 保留 | `873a279` | `app/llm/assembly.py` | `test_llm_user_content_parts.py`（10） |
| 5 | `ServiceAccess` 暴露 profile/role preset；RolePreset 扩 `begin_dialogs`/`tools`/`skills` | `6e0bd40` | `app/services/role_preset_service.py` `app/modules/base.py` `bootstrap.py` | `test_role_preset_extended.py`（8） |
| 6 | 请求 messages 落盘通道（与第三方框架逐条 diff）+ WebUI 只读端点 | `26d5ff4` | `app/llm/prompt_dump.py` `app/webui/api/prompt_dump.py` | `test_llm_prompt_dump.py`（14） |
| 7 | `ProviderInvoker` 调用策略缝 + token 预算裁剪 | `98d7f7d` | `app/llm/providers/invoker.py` `app/llm/token_budget.py` | `test_llm_provider_invoker.py`（14）+ `test_llm_token_budget.py`（17） |
| 8 | `SessionManager` 历史契约 + 外部历史导入 | **WIP（未提交）** | `app/llm/session.py` | **缺测试** |

## 2. 插件侧怎么用（最小示例）

```python
# module/modules/my_astrbot_assembly/module.py
from app.modules import BaseModule, llm_hook

class Module(BaseModule):
    name = "AstrBot 组装复刻"
    sign = "AstrBotAssembly"
    # 声明式接管：启用时装上自己的装配器，卸载/禁用时自动还原框架默认装配器
    supersedes = ("assembly", "provider_invoker")

    # 1) 整体替换组装：把 AstrBot 的 build_main_agent 结果搬过来
    def build(self, req):
        # req: assembly.PromptRequest（一次请求的全部归一化输入）
        # 返回 OpenAI 风格 messages
        return my_astrbot_assemble(req)

    # 2) 整体替换 provider 调用策略（AstrBot 的工具循环在 runner 层，不在 provider 内）
    async def chat(self, *, runtime, chain, messages, params):
        return await my_astrbot_runner.chat(messages, params)

    async def stream(self, *, runtime, chain, messages, params):
        async for ev in my_astrbot_runner.stream(messages, params):
            yield ev

    # 3) 组装后微调（不想整体替换时用这个）
    @llm_hook("post_assemble", event_type="*", order=50)
    async def tweak(self, ctx):
        ctx.messages = my_patch(ctx.messages)
        # 置空 ctx.messages = 本轮不请求 LLM
```

相关配置键（Agent 配置页「会话/压缩」组）：

| 键 | 默认 | 用途 |
|---|---|---|
| `system_prompt_style` | `blocks` | `single` = 把所有 system 块折成一整条（对齐 AstrBot 形态） |
| `prompt_dump_enable` | `False` | 每轮最终 messages 落 `data/llm/<bot_id>/prompt_dump.jsonl` |
| `prompt_dump_max_entries` | `20` | 滚动保留条数 |
| `context_token_budget` | `0` | >0 时启用 token 预算裁剪（丢最旧历史，system 与本轮提问必留） |
| `context_token_chars_per_token` | `2.0` | 估算参数（只用于防爆，不用于计费） |

## 3. 与 AstrBot 对账的验收路径

1. Agent 配置里打开 `prompt_dump_enable` + 把 `system_prompt_style` 切到 `single`；
2. 在该账号下跑一轮真实对话（或“主动消息/定时任务”）；
3. 取 `data/llm/<bot_id>/prompt_dump.jsonl` 最后一条，或走
   `GET /api/agent/prompt-dumps?bot_id=<qq>&session_id=group_xxx`；
4. 与 AstrBot 侧同一轮的 `messages` 逐条 diff（AstrBot 侧组装链路见会话中的测绘报告，
   核心是 `build_main_agent` → `ToolLoopAgentRunner.reset` → `_prepare_chat_payload`）；
5. 形状对不上时：先看 `describe()`（块-消息对位）确认是哪一块多/少，再用
   `post_assemble` 钩子或 `build()` 复刻补齐。

## 4. 验证证据（本次会话实际跑过的）

- 全量 `venv\Scripts\python.exe -m pytest -q`：**1027 passed**（会话开始时为 957）；
- 每个提交前都跑了对应子集 + 全量；
- `ruff check`：逐文件与 HEAD 对比违规数**无新增**（新文件零违规）；
  仓库本身存量违规未动（CI 现以 `--exit-zero` 运行）；
- 回归测试有效性：proactive 修复用例在修复前**必红**（`UnboundLocalError`，已实测）；
  多租约交棒用例抓出过 `release_owner` 漏加交棒的真实漏洞。

## 5. 未完成的 WIP（重要）

`app/llm/session.py` 的改造 8 代码已写完并**通过全量测试**（新方法是纯增量、没改动既有行为），
但**缺配套单测**，按仓库规则（新增功能必须配套测试）不能提交，故已 `git stash` 保存：

```bash
git stash list                  # 找到 "WIP 改造8: SessionManager 历史契约"
git stash pop                   # 恢复后补测试再提交
```

内容：
- `HISTORY_ENTRY_FIELDS` / `HISTORY_ROLES` 常量与模块 docstring 里的**存储契约**说明；
- `normalize_import_entry(entry)`：把 AstrBot/OpenAI 风格 `{role, content}`（含多模态
  content 数组 → 取文本块）归一化成框架存储形状，脏数据返回 `None`；
- `SessionManager.history_snapshot(session_id, *, limit, include_junk)`：返回**未投影的
  原始条目深拷贝**（保留 `base.segments` / `meta_lines`），供插件用自己的渲染器组装；
- `SessionManager.import_history(session_id, entries, *, replace, session_type, timeout, persist)`：
  批量导入外部历史，会话不存在则创建，返回 `{imported, skipped, dropped, total}`。

**下一步（恢复后第一步）**：写 `tests/test_llm_history_contract.py`，覆盖
快照是副本/保留 segments、默认滤杂散、`get_history` 与 `history_snapshot` 的差异、
导入归一化（多模态/脏数据/时间戳回退）、`replace` 语义、会话不存在时自动创建、
`MAX_HISTORY_MESSAGES` 裁剪、以及「导入 AstrBot 风格历史后框架组装器能读到」的端到端。
注意 `SessionManager` 是**按 bot_id 的进程级单例**：每个用例要用不同 bot_id，
否则会串到上一个用例已关闭的历史库。

## 6. 剩余待办

| 项 | 内容 | 备注 |
|---|---|---|
| 8 | 补 `test_llm_history_contract.py` 并提交 | WIP 已在 stash |
| 9 | 文档同步：`docs/MODULE_DEV.md`（新增 `post_assemble` 阶段、`assembly`/`provider_invoker` 接管、`extra_user_content_parts`、落盘与 token 预算）、`docs/FEATURE_REGISTRY.md`（§5 能力表加 `assembly` / `provider_invoker`，`suppress_with_owner` / `on_owner_changed` 钩子）、`README.md`（插件 API 与配置键） | 未开始 |
| 10 | 收尾：全量 pytest 全绿 + `ruff check .` 无新增违规 | 每步已在做，最后再总跑一次 |
| 11 | （可选）写一个真实的 AstrBot 组装复刻插件骨架做端到端验证 | 会话中讨论过，未动工 |

## 7. 顺带发现的问题（未修，与本次改造无关）

1. **测试互相污染**：把 `tests/test_llm_assembly*.py` 与 `tests/test_group_log_integration.py`
   放进同一次子集运行，后者会红（`group_log store` 的全局状态没清理）；
   全量按字母序跑则正常。已实测可**不带**本次新增文件复现，属既有问题。
2. **`ModuleConfig.__getattr__` 陷阱**：它把缺失属性返回 `None`，导致
   `hasattr(config, "set_session")` **恒为 True**，随后调用 `None(...)` 抛 `TypeError`。
   生产里 `module.config` 是 `AgentConfig`（真有该方法）所以不受影响，
   但测试替身一旦走到这条分支就会以误导性的 `TypeError` 掩盖真实错误
   （本次修复 proactive 时正是被它挡了一轮）。
3. `scripts/requirements.bat`（已改未提交）与未跟踪的 `qqbot_next.egg-info/`
   是**进入本次会话前就存在**的，我没有碰过，也没混进任何提交。

## 8. 复现命令

```bash
# 全量测试
venv\Scripts\python.exe -m pytest -q

# 本次改造相关的子集（避开已知互相污染的 test_group_log_integration.py）
venv\Scripts\python.exe -m pytest tests/test_llm_post_assemble.py tests/test_llm_assembly_fold.py ^
  tests/test_llm_user_content_parts.py tests/test_features.py tests/test_role_preset_extended.py ^
  tests/test_llm_prompt_dump.py tests/test_llm_provider_invoker.py tests/test_llm_token_budget.py -q

# 单文件 ruff 对比（仓库存量违规不阻断，只看是否有新增）
ruff check app/llm/assembly.py
```
