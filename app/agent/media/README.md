> 本文只负责：说明 `app/agent/media/` 放什么、不放什么。上层总览见 `../README.md`。

# `app/agent/media/` —— 图片与媒体：下载、缓存、转存、动图处理。

## 包含

| 文件 | 职责 |
|---|---|
| `image.py` | 图片处理：尺寸、格式、base64/URL 互转 |
| `store.py` | 媒体存储：按 sha 去重落盘，随账号隔离 |
| `animated.py` | 动图 / 视频帧处理 |

## 注意

缓存淘汰策略（TTL + 容量上限）在 `app/infrastructure/fs/media_cache.py`，
与 bilibili/douyin 插件共用同一套（P3 从 `app/modules/` 下沉）。

## 边界

- 允许 import：`app.core` `app.domain` `app.infrastructure` 与本包内文件。
- 不得 import `app.webui` / `app.bootstrap`。跨子包依赖请走对方 `__init__.py` 的导出。
- 新增文件前先问：它属于「取料 / 决策 / 发送 / 存储」哪一类？答不上来就别加。
