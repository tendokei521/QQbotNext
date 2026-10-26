# X1 黑盒评测集（`tests/eval/`）

给 prompt / 工具循环 / 历史压缩提供一张**回归网**：改完不用人读回复，跑一遍就知道
"请求次数、工具顺序、结局、关键短语"有没有变差。

## 怎么跑

```bat
:: 只跑评测（推荐，也是 CI 里可选的单独一步）
venv\Scripts\python.exe -m pytest tests/eval -q -m eval

:: 看每个场景的名字与耗时
venv\Scripts\python.exe -m pytest tests/eval -m eval -v

:: 只跑某一个场景
venv\Scripts\python.exe -m pytest tests/eval -q -m eval -k two_tool_rounds
```

默认全量里它们**只收集不执行**（显示为 skipped）：

```bat
venv\Scripts\python.exe -m pytest tests/eval -q      :: 17 skipped
```

## 为什么默认不进全量

1. **慢且吵**：每个场景要驱动真实 `run_turn` + 多轮脚本（其中一条会写 60KB 工具结果），
   失败信息是"行为变了"而不是"代码错了"，不适合当每次提交的硬门禁；
2. **不靠"记得加 -m"**：只在 `tests/eval/conftest.py` 里注册 marker 并用
   `pytest_collection_modifyitems` 在**没有** `-m eval` 时给用例加 skip——收集得到
   （不会被无声遗漏），但默认不执行、不拖慢 `pytest -q`；
3. **不动全局配置**：`pyproject.toml` / `pytest.ini` 一个字没改，评测约定不外溢。

## 目录

| 文件 | 作用 |
|---|---|
| `conftest.py` | 注册 `eval` marker；默认跳过 |
| `harness.py` | 最小驱动：真实 `OpenAICompatProvider` 只替换 `_request` / `_stream_once`，真实 `run_turn` 只 patch `prepare_prompt` / `assembly.build_for` |
| `test_scenarios.py` | 读 `scenarios/*.yaml`、逐轮执行、按 `expect` 断言；另有场景目录自检 |
| `scenarios/*.yaml` | 场景本体（当前 16 个） |

## 场景清单

| 场景 | 锁住什么 | 请求数 | 结局 |
|---|---|---|---|
| `basic_reply` | 一次请求、正文原样回来 | 1 | completed |
| `two_tool_rounds` | 两轮工具恰好 3 次请求、回填成对 | 3 | completed |
| `two_tools_one_round` | 单轮两个工具的执行/回填顺序 | 2 | completed |
| `three_tool_rounds_max` | 轮数用尽不再多请求一次 | 3 | max_rounds |
| `reasoning_only_max_tokens` | 只有思考没正文 ≠ silent | 1 | max_tokens |
| `empty_reply_silent` | 空回复不发多余请求 | 1 | silent |
| `stream_error` | 429/5xx 类错误不被吞成空回复 | 1 | error |
| `tool_error_result` | 工具报错仍回填并继续循环 | 2 | completed |
| `long_tool_result_spill` | 60KB 工具结果落盘为预览 + 路径，不崩 | 2 | completed |
| `group_chat_history` | 历史条数原样进首请求、老约定还在 | 1 | completed |
| `long_history_20_rounds` | 20 轮前信息仍在首请求（41 条 messages） | 1 | completed |
| `proactive_message` | `kind=proactive` 真写进 PromptRequest | 1 | completed |
| `blocking_no_stream` | 阻塞路径（`stream=false`）走得通 | 1 | completed |
| `blocking_max_tokens` | 兜底话术不冒充 completed | 1 | max_tokens |
| `multi_turn_followup` | 同一会话连续两问、脚本不串 | 1 + 1 | completed + completed |
| `top_level_provider_script` | 顶层脚本队列按 `requests` 切分到各轮 | 1 + 1 | completed + completed |

## 场景字段

```yaml
name: two_tool_rounds            # 必填、全局唯一（也是 pytest id）
description: 一句话说明           # 必填，写"锁什么"
config:                          # 可选，覆盖 harness.DEFAULT_CONFIG
  max_tool_rounds: 3
stream: true                     # 可选，false = 走阻塞路径 provider.chat
kind: chat                       # 可选，chat / proactive / schedule / module
history:                         # 可选，模拟"已组装好的历史"，原样拼在本轮 user 前
  - {role: user, content: "（前情）..."}
turns:
  - user: "本轮用户输入"          # 必填
    provider_script:             # 每条 = 一次模型响应（必须与真实请求数一致）
      - "一段正文"                # 字符串简写
      - text: "正文"
        reasoning: "思考"
        finish_reason: length
      - tool_call: {name: probe_a, arguments: {x: 1}, id: c0}
      - tool_calls:              # 单轮多个工具（顺序即模型顺序）
          - {name: probe_a, arguments: {}, id: c0}
          - {name: probe_b, arguments: {}, id: c1}
      - error: "HTTP 429 上游限流"
      - raise: "boom"            # 仅阻塞路径：让 _request 抛异常
    expect:                      # 必填 reason + requests/max_requests，其余可选
      requests: 3                # 精确请求次数（写错会被 harness 直接判定为脚本不一致）
      max_requests: 3
      tools: [probe_a, probe_b]  # 工具执行顺序
      contains: ["两件事都查好了"] # 回复里必须出现的关键短语
      not_contains: []
      reason: completed
      first_request_messages: 7  # 第一条请求的 messages 条数（历史量化）
      first_request_contains: [] # 第一条请求里必须还能找到的老信息
      tool_result_contains: []   # role:tool 回填内容里必须出现的短语
      prompt_kind: proactive     # PromptRequest.kind
      spill_files_min: 1         # 至少要落盘几个 spill 文件
```

`provider_script` 也可以放在**顶层**（多轮共用一个队列），此时每轮必须写
`expect.requests` 用来切分；单轮场景写在 `turns[0].provider_script` 更直观。

## 探针工具

`harness.ToolProbe` 提供 5 个固定名字的工具（`arguments.result` 可以覆盖返回文本）：

| 工具 | 返回 |
|---|---|
| `probe_a` / `probe_b` / `probe_c` | 固定的短文本 |
| `probe_fail` | 以 `error:` 开头的失败结果 |
| `probe_long` | 超长文本，`arguments.size` 指定长度（默认 60000），触发 spill |

## 怎么加一个场景

1. 在 `scenarios/` 新建 `<name>.yaml`（文件名排序即执行顺序）；
2. 填 `name` / `description` / `turns`；每轮至少 `user` + `expect.reason` +
   `expect.requests`（或 `max_requests`）；
3. `provider_script` 的条数 == 该轮真实请求数：
   普通回复 1 条；一轮工具 = 工具响应 + 最终回复 2 条；两轮工具 3 条；
4. 先跑 `-m eval -k <name>` 对齐，再跑全量 `-m eval`；
5. 如果脚本条数和真实请求数对不上，harness 会抛 `EvalHarnessError`
   （这是**场景写错**，不是被测代码变差——别用 `max_requests` 掩盖）。
6. `test_catalog_is_well_formed` 会检查：场景数 ≥ 12、`name` 唯一、必填字段齐全。

## 覆盖边界（如实写）

- **不覆盖真实取料**：`prepare_prompt` 被替换成最小实现，`history` 是我们直接塞给
  `assembly.build_for` 的字面量。所以 `group_chat_history` / `long_history_20_rounds`
  锁的是"组装好的历史原样进请求、条数对得上"，**不锁** `history_mode` /
  `history_recent_messages` 的裁剪策略本身。等 Q2 落地后应再加一层"跑真实
  `prepare_prompt`"的评测。
- **取消（aborted）没做**：`run_turn` 目前没有 abort 入口——`TurnRequest` 没有
  `abort` 字段，`AbortScope` 只在 `loop/pipeline.py` 里创建。现成钩子只到
  `run_stream_step` 层（见 `tests/agent/loop/test_turn_abort.py`），不在本轮
  "黑盒 `run_turn`"的测量面上；等 `run_turn` 长出取消入口再补场景。
- **没做**：引用消息 / 表情 / 定时任务（`kind=schedule`）——前两个要真实事件对象
  （`TurnRequest.event` 路径），第三个与 prompt 形态无差异，价值低。
- **不覆盖 provider 内部重试**：`_request` / `_stream_once` 被整体替换，
  `retry_attempts` 那层 HTTP 重试由 loop 单测负责；评测只锁"一次 turn 发几次请求"。
- 首批只做了 1 个"长依赖"场景，计划 §X1 提到 3 个；另外两个建议随 Q2（历史按需）
  一起补：压缩后仍能回答 20 轮前信息、裁剪后引用消息仍在。

## 设计来源

- `tests/agent/loop/test_tool_loop_contract.py`：`_ScriptedProvider` 的"真实适配器 +
  假 HTTP 底座"做法；
- `tests/agent/loop/test_turn_reason_matrix.py`：patch `prepare_prompt` 后跑 `run_turn`
  的最小 harness；
- `docs/llm-context-log-plan.md` §X1（目标与首批场景清单）。
