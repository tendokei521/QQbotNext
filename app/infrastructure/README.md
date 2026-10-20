> 本文只负责：说明 `app/infrastructure/` 放什么、不放什么。上层目录总览见 `docs/ARCHITECTURE.md`。

# `app/infrastructure/` —— 基础设施

**一句话：所有对外部世界的 IO 都从这里出去，且只有这里。**

## 包含

| 子目录 | 职责 |
|---|---|
| `onebot/` | OneBot v11 实现：`gateway`（连接/去重/重连）+ `client`（API 调用）+ `codec`（编解码） |
| `persistence/` | SQLite（aiosqlite）+ `BaseRepository` |
| `config/` | 配置中心 `ConfigService`（SQLite + 内存缓存 + 变更广播） |
| `fs/` | `paths`（数据目录的唯一来源）+ `media_cache`（TTL/容量淘汰缓存策略） |
| `cache.py` | 进程内缓存原语 |
| `curl_cffi.py` | 带浏览器指纹的 HTTP 客户端（绕过部分站点风控） |

## 边界（重要）

- 允许 import：`app.core.*` `app.domain.*`、标准库、三方库。
- **不得** import：`app.agent` `app.plugin` `app.services` `app.webui`。
  `tests/test_architecture_boundaries.py` 闸死。
- 这里**只做 IO，不做判断**。「该不该发这条消息」「该用哪个模型」属于 `domain/` 或
  `agent/`，不属于这里。

## 数据目录唯一来源

任何需要写文件的代码都必须经过 `infrastructure/fs/paths.py`：

```python
from app.infrastructure.fs.paths import bot_data_dir, llm_data_dir
```

不要在别处拼 `Path("data") / ...`——那会让「数据存哪」在多处各说一套。
