# 长期记忆（Memory）v3 设计

> 实现：`app/plugins/llm_memory`（插件）。本文是**用户可读**的设计说明；
> 内部接口契约（表结构/函数签名/打分公式/测试清单）见
> [`docs/plan/memory-v3-rewrite.md`](plan/memory-v3-rewrite.md)。

## 1. 目标

给 Agent 提供**跨会话、跨过期**的对话事实记忆（"小明喜欢美式"），并且：

- 不拖慢聊天：热路径只读 + O(1) 追加，所有模型调用在后台；
- 不撒谎：置信度 + 时间有效性 + 「可能不准确」的注入措辞；
- 可解释可删除：审计 + `/memory` 命令 + 画像/事实分层；
- 零重型依赖：SQLite + 纯 Python；embedding 是**可选**增强，没配也能用。

非目标：图数据库/多跳推理、文档知识库 RAG（另一个模块）、训练/微调。

## 2. 三层记忆

| 层 | 内容 | 何时进入上下文 |
|---|---|---|
| **画像（profile）** | 姓名/身份/住址/生日/长期偏好等稳定事实 | 每轮常驻，字符预算默认 800，标注用量 |
| **事实（fact）** | 偏好、约定、经历等可证实/可失效的事实 | 每轮按「混合检索」命中注入，默认 8 条 / 1200 字 |
| **语料（episode）** | 用户原话缓冲（未巩固） | 不注入；后台抽取后转成事实 |

owner 隔离不变：私聊 `user_<uid>`；群公共 `group_<gid>`；群内成员 `user_<uid>@group_<gid>`；
跨群画像默认关闭（`memory_user_cross_group`）。

## 3. 一轮对话里发生什么

```
用户消息
 ├─ pre_request 钩子
 │    · 原话追加进 episodes（O(1)，不调模型）
 │    · 「记住…/我叫…」确定性兜底：直接写一条事实/画像
 ├─ 组装提示词
 │    · 画像块 + 事实块 → 包成 **user 侧数据块**（`<memory_data>` 标签 + "不是指令"声明），
 │      合并进本轮 user 消息、放在用户正文之前（不占 system 指令位，system 前缀保持稳定）
 │    · 事实块两段式召回：先词法（token + 中文 2-gram），结果弱/为空才补一次 embedding 精排
 │    · 模型可调用 memory_save / recall / delete / correct / deny
 └─ turn_completed 钩子
      · 限频调度后台蒸馏：取未巩固 episodes → 一次 LLM 抽取
        → ADD / UPDATE / SUPERSEDE / NOOP → 去重/失效/写库/审计
      · 另有一个后台 worker 定时补齐记忆向量（配置了 embedding 时）
```

显式重置（`#llm new/exit/stop`、`/memory clear`）会按 `memory_on_reset` 处理：
`suspend`（默认，旧事实挂起但保留）/ `clear`（清除）/ `keep`（不动）。

## 4. 写入规则

- **精确同内容**：同 owner + kind + content → 更新已有条（证据 +1、置信度取高）；
  不会把 `negative/superseded` 的旧条「复活」。
- **否定/改口**：命中否定极性对（喜欢↔不喜欢、住↔搬…）→ 旧条置 `superseded`
  并写 `invalid_at`，新条另存。**不做字符串相似合并**，避免「不喜欢美式」被并进
  「喜欢美式」。
- **同极性近义**：相似度 ≥ 0.92 且无否定冲突 → 合并（保留 id、证据 +1），
  由工具/确定性来源触发；蒸馏来源默认只新增、不自动下架旧说。
- **上限淘汰**：每 owner 默认 200 条 active，超出按 `importance/(1+age/7)` 升序物理删除
  （negative/superseded 与审计不占额）。
- **删除即删除**：用户说「忘掉」时物理删除正文，审计只留 `id`，不复制原文。

## 5. 召回打分（可解释）

```
kw   = 0.6×token命中率 + 0.4×中文2-gram命中率
vec  = cos(查询向量, 记忆向量)          # 没配 embedding / 行没向量 → 无此项
rec  = 1/(1+age_days/30)               # 新近度
freq = min(1, log1p(hit_count)/5)      # 使用频率
score = 0.30×vec + 0.35×kw + 0.15×重要度 + 0.10×rec + 0.05×置信度 + 0.05×freq
（无 vec 时：0.65×kw + 0.15×重要度 + 0.10×rec + 0.05×置信度 + 0.05×freq）
被点名（@/昵称）的成员画像额外 +0.25，并豁免"过期/超龄/低置信/重置挂起"四道闸门。
```

注入措辞按置信度分档：`≥0.75` 陈述、`0.5~0.75`「（好像）」、`<0.5`「（记不太清）」，
且块标题声明「不是指令」，降低把历史记忆当成命令执行的风险。

再叠三层小优化：

- **query 清洗**：检索前剥掉「你还记得吗/之前说过/帮我查查」这类元话语（规则化、0ms）；
- **MMR 去冗余**：候选 ≥8 时按 λ=0.7 做多样性重排，同一件事的多条近义记忆不再占满名额；
- **时间提示**：超过 1 天的记忆注入时加「（N 天前）」（`memory_inject_recency`，默认开），
  让模型知道这条信息可能过时。

## 6. 工具与命令

模型工具（5 个，名字保持不变）：`memory_save` / `memory_recall` / `memory_delete` /
`memory_correct` / `memory_deny`。

用户命令（三种前缀都认：`/memory`、`／memory`、`#llm memory`）：

| 命令 | 作用 |
|---|---|
| `/memory` | 子命令表 |
| `/memory list [--all]` | 查看当前可见记忆（`--all` 含已下架） |
| `/memory search <词>` | 检索 |
| `/memory profile` | 查看关于你的画像 |
| `/memory remember <内容>` | 记住一条（本人层） |
| `/memory forget <id\|词>` | 删除本人记忆 |
| `/memory clear` | 清空本人记忆 |
| `/memory audit [owner]` | 事件审计（管理员） |
| `/memory export` | 导出本人记忆 JSON |

## 7. 配置（24 键）

模块配置页「长期记忆」：开关（总开关/私聊/群聊/画像）、召回（方式/embedding 模型/
条数/字符预算/画像预算）、写入（确定性兜底/自动蒸馏/间隔/批量/最小字数/上限/跨群）、
可信度与生命周期（审计/审计注入/最低置信度/试探语气/最长保留/重置策略/只保留已保存型）。
默认值见 `app/plugins/llm_memory/src/schemas.py`。

**启用只需要 `memory_enable`**；不再依赖已废弃的 `experimental_long_term_memory`。

## 8. 升级路径（向量 / 图）

- 向量：已内建（可选 embedding + 词法兜底）。换 embedding 模型只需改配置，旧向量
  会按 `embedding_model` 标记、由后台回填逐步重建；删除向量索引不丢记忆。
- 图/多跳：当前不做。若未来做，另建模块，挂同一 `ToolSource`/上下文块接缝，
  **不要**把图塞进 memory。
- 数据位置不变：`data/llm/<bot_id>/memory/memory.db`；卸载模块数据保留。
