> 本文只负责：说明 `app/agent/` 放什么、子包怎么分。上层目录总览见 `docs/ARCHITECTURE.md`。

# `app/agent/` —— 框架级 Agent

**一句话：一轮对话从「收到消息」到「决定发什么」的全部逻辑。**

> 这里原名 `app/llm/`，是一个 91 文件 / 32,900 行的平铺包（顶层挤了 55 个文件）。
> P2 按职责拆成下面 14 个子包，顶层只留 8 个文件。**没有保留兼容 shim**：
> `from app.llm import tool` 已改为 `from app.agent import tool`。

## 子包职责（改需求先定位到这里）

| 子包 | 一句话 | 典型入口 |
|---|---|---|
| `loop/` | 一轮对话的主流程：判定→组装→请求→工具循环→发言 | `chat.py::handle` |
| `assembly/` | 把上下文拼成 messages（含 token 预算与压缩） | `assembler.py` |
| `session/` | 会话与历史（切换 / 落盘 / 富化） | `manager.py` |
| `persona/` | 人格与表达（规则 / 技能 / 标签 / 表情） | `rules.py` |
| `context/` | 群语境与指代（谁在说谁、关注点、昵称） | `referent.py` |
| `tools/` | 工具协议与内置工具（含 OneBot 工具清单与权限） | `spec.py` `policy.py` |
| `delivery/` | 发送侧（发言池 / 节奏 / 分条 / 指令解析） | `speech.py` `pace.py` |
| `media/` | 图片与媒体（下载 / 缓存 / 动图） | `image.py` `store.py` |
| `proactivity/` | 主动消息与定时任务 | `scheduler.py` |
| `memory/` | 长期记忆（SQLite + 召回 + 隐性蒸馏） | `manager.py` |
| `knowledge/` | 知识库（向量检索） | `manager.py` |
| `group_log/` | 群聊日志（供「刚才发生了什么」类查询） | `collector.py` |
| `storage/` | `HistoryStore`/`MemoryStore` 的接口与工厂 | `base.py` |
| `providers/` | 各 LLM 供应商适配（**全库最规范的部分，新代码照这个写**） | `policy.py` |

主链路的几个落点：工具轮循环只剩 `loop/tool_step.py` 一处（`turn_runner.py` 收结局与取消）；
工具结果的结构化 / 落盘 / 取回 / 审批在 `tools/`（`result.py` `spill.py` `recall.py` `approval.py`）；
历史的唯一真相是 `session/session_log.py` 的追加式事件日志（`messages` 只是派生缓存，`verify.py` 在线自检）。

顶层 8 个文件：`runtime.py`（Runtime 聚合对象）、`client.py`（给插件用的门面）、
`telemetry.py`、`hooks.py`、`commands.py`、`config.py`、`config_schema.py`、`__init__.py`。

## 包级导出（插件只需要记这几个）

```python
from app.agent import tool, ToolContext           # 声明一个 LLM 工具
from app.agent import skill                       # 声明一个技能
from app.agent import LlmContext, ToolCallContext # 钩子里的上下文
```

`handle` **故意不从包级导出**——它会让 `app.agent` 反向依赖整个主循环。
需要它请直接 `from app.agent.loop.chat import handle`。

## 已知的环（P4 要拆）

`loop/chat.py` ⇄ `proactivity/scheduler.py` ⇄ `proactivity/stream.py` 三者互为递归依赖：
主循环要拿 `schedule_task` 工具，调度器到点要跑一轮生成。
拆法：把「定时工具」（`build_schedule_tool` / `handle_schedule_tool` / 意图检测）从
「定时引擎」（`TaskScheduler`）分离到 `tools/`。详见 `STYLE.md §12.1`。
