> 本文只负责：说明 `app/domain/` 放什么、不放什么。上层目录总览见 `docs/ARCHITECTURE.md`。

# `app/domain/` —— 领域模型与纯规则

**一句话：协议无关的类型、契约与纯判断。谁都能 import 它，它不 import 任何业务层。**

## 包含

| 文件 | 职责 |
|---|---|
| `bot.py` | `IBot` 抽象——模块只依赖它收发消息，不认 OneBot |
| `events.py` | 类型化事件（消息 / 通知 / 申请） |
| `message.py` | 消息段模型（文本 / 图片 / @ …） |
| `time_parser.py` | 中文时间表达式解析（「明天早上8点」→ 时间戳 / 重复规则） |
| `trigger.py` | 触发判定：这句话是不是在叫机器人 / 是不是在要求定时 |
| `splitter.py` | 长文本分条（按标点与长度切分，不含发送逻辑） |
| `pace.py` | 发送节奏的计算（间隔、打字延迟的纯函数） |
| `tags.py` | 标签剥离（`strip_all_tags`：把内部标记从正文里去掉） |
| `modalities.py` | 模型能力判定：这个模型支持图片/语音吗 |

## 边界（重要）

- 允许 import：`app.core.*`、`app.domain.*`、标准库、三方库。
- **不得** import：`app.agent` `app.infrastructure` `app.plugin` `app.services` `app.webui`。
  `tests/architecture/test_architecture_boundaries.py` 会闸死。
- **不得做 IO**：读文件、连库、发网络请求都不属于这里。需要 IO 就说明它该去
  `infrastructure/`（例如 `media_cache` 就落在 `app/infrastructure/fs/`）。

## 判断一个文件该不该搬进来

问三个问题，全是「是」才搬：

1. 它的 import 里**没有任何** `app.*` 业务依赖？（只依赖 core / domain / 标准库 / 三方）
2. 它的函数是**纯的**？（同样输入 → 同样输出，最多读「当前时间」）
3. 它回答的是「业务上该怎么判断」，而不是「怎么把这件事做出来」？

反例：`app/agent/tools/policy.py` 看着像纯策略，但它依赖 `app.agent.tools.spec` 的
`ToolSpec` 类型，所以留在 `agent/`。

搬进来之后记得同步搬测试：`app/domain/splitter.py` ↔ `tests/domain/test_stream_splitter.py`。
