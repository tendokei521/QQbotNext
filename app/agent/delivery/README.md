> 本文只负责：说明 `app/agent/delivery/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/delivery/` —— 发送侧：怎么把一句话变成「什么时候发、发几条、每条多长」。

## 包含

| 文件 | 职责 |
|---|---|
| `speech.py` | 发言通道：文本=思考、`send_message`=真正发出 |
| `pool.py` | 发言池：攒一波再发，避免刷屏 |
| `pace.py` | 发送节奏：间隔、打字延迟 |
| `splitter.py` | 长消息分条（按标点/长度） |
| `directives.py` | 输出里的指令解析（`[split]` 一类标记） |

## 注意

改「发多快/分几条」→ 这里；改「发什么内容」→ `assembly/` 或 `persona/`。

## 边界

- 允许 import：`app.core` `app.domain` `app.infrastructure` 与本包内文件。
- 不得 import `app.webui` / `app.bootstrap`。跨子包依赖请走对方 `__init__.py` 的导出。
- 新增文件前先问：它属于「取料 / 决策 / 发送 / 存储」哪一类？答不上来就别加。
