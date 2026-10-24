# 抖音视频解析（douyin_parser）

自动解析群聊/私聊中的抖音分享链接（短链 / 直链 / 图集 / QQ 小程序卡片），
**下载无水印视频或图集**，并以**合并转发**发送：节点 1 = 文案，节点 2 = 视频/图集。

```
用户: 7.53 复制打开抖音，看看【作者】的作品 https://v.douyin.com/iRNBho6u/
机器人: ┌ 群聊的聊天记录 ────────────────┐
        │ 抖音解析: 📺 文案/封面/作者/时长/链接 │   ← 节点 1
        │ 抖音解析: [视频 无水印 mp4]           │   ← 节点 2
        └────────────────────────────────┘
```

## 处理流程

```
handle（快）
  1 总开关 → 2 群范围 → 3 文本/JSON 段提取链接 → 4 短链归一成 aweme_id
  → 5 作品去重（按 bot 隔离）→ 6 限数 → 7 aweme/detail 取详情 → 8 event.llm.stop()
        ↓ 交给 services.task_manager 后台任务（避免阻塞单 bot 串行事件队列）
_parse_and_send（慢，仅第一个作品）
  9  pick_quality 选清晰度（默认最高可用）
  10 流式下载到本地缓存（.part + 原子改名，边下边卡体积上限）
  11 send_forward_msg（文案节点 + 视频/图集节点）
```

## 技术要点

| 关键点 | 说明 |
|---|---|
| `a_bogus` 签名 | 抖音 Web API 必需：随机串 + 参数指纹 → SM3 → RC4 → 自定义 base64。本插件按算法结构重写（`abogus.py`），常量与公开逆向实现一致 |
| SM3 自带实现 | 上游依赖第三方 `gmssl`；本插件自带纯 Python SM3（`sm3.py`），**不为解析功能新增依赖**。已用国标向量 + OpenSSL 交叉核对 |
| `X-Bogus` 兜底 | 旧签名方案，`a_bogus` 那一路失败时自动重试（`xbogus.py`，仅用标准库） |
| 签名等价性 | 两个签名实现已与上游 `DLWangSan/douyin_parse` 做**逐字节等价对照**（固定随机数 + 时间戳，多组参数），并用固定向量锁在 `tests/test_douyin_signature.py` |
| 无水印 | 优先 `video.bit_rate[]`（按码率/档位排序取最高），回退 `play_addr.url_list`，再回退由 `play_addr.uri` 拼装；`playwm` → `play`，并去掉 `watermark` / `logo_name` 参数 |
| 图集 / Live 图 | Live 图取 `images[].video.play_addr`（存 **mp4**）；静态图取 `url_list`；兜底 `download_url_list`；同图集按「去掉查询串」去重 |
| 体积上限 | 抖音接口**不返回文件大小**，所以是「边下边卡」：超过 `video_max_mb` 立即中断，不再白下几百 MB |
| 下载容错 | CDN 偶发中断 → 回滚到起始偏移再重试（不回滚会写进重复字节）；失败清理 `.part` 半成品 |
| 本地缓存 | 同一作品只下载一次：`data/module/data/douyin_parser/media_cache/<aweme_id>.mp4`（图集 `<aweme_id>_001.jpg`），按 TTL + 容量上限淘汰 |
| 只下一个 | 一条消息命中多个作品时，只有**第一个**下载媒体，其余只发文案节点 |
| User-Agent | 默认沿用 curl_cffi 指纹自带的 UA（bilibili_parser 实测：写死旧 UA 会造成「指纹新、UA 旧」矛盾而被风控）；`user_agent` 配置可逐字复刻上游环境 |

## 配置项

| key | 默认 | 说明 |
|---|---|---|
| `cookie` | 空 | **强烈建议填**：抖音 Web 接口多数情况需要登录 Cookie（含 `ttwid` / `msToken`），留空时成功率明显下降 |
| `user_agent` | 空 | 留空用指纹自带 UA；填上游那个 Chrome/130 UA 可复刻上游环境 |
| `use_forward_msg` | `true` | 合并转发；关闭则回落旧版引用回复（不下载媒体） |
| `enable_video_download` | `true` | 下载并附带无水印视频 |
| `video_quality` | `highest` | `highest` / `1080p` / `720p` / `540p` / `480p`；指定档位缺失时回落最高可用 |
| `video_max_mb` | `80` | 边下边卡体积，超限只发文案节点；`0` 不限制 |
| `enable_album_download` | `true` | 图集 / Live 图下载后放进合并转发 |
| `album_max_images` | `9` | 图集最多下载张数 |
| `video_download_timeout` | `60` | 流式下载单请求超时（秒） |
| `video_cache_enabled` | `true` | 复用本地缓存 |
| `video_cache_ttl_minutes` | `720` | 缓存保留时长；`0` 不按时间淘汰 |
| `video_cache_max_mb` | `1024` | 缓存容量上限；`0` 不限制 |
| `is_reply` | `true` | **仅** `use_forward_msg=false` 时生效 |
| 其余 | — | `max_parse_count` / `show_cover` / 群范围 / 作品去重 / 超时 与 bilibili_parser 同形 |

## 依赖与前提

- 需要 OneBot 支持 `send_group_forward_msg` / `send_private_forward_msg`
  （节点内的媒体片段走本地 `file:///` 上传）；
- 网络请求统一走 `app/infrastructure/curl_cffi.py::CurlCffiClient`（浏览器指纹模拟）；
- **不引入新依赖**：SM3 自带实现，签名只用标准库 + `curl_cffi`。

## 降级链

```
短链解析失败 → 该条跳过（不影响其它链接）
详情拿不到（受限/被删/Cookie 失效）→ 该条不回复
取流失败 → 节点 2 换成文本（原因 + 链接），节点 1（文案）照常送达
下载失败 / 体积超限 → 同上
use_forward_msg=false → 旧版单条引用回复（不下载媒体）
```

## 文件结构

```
douyin_parser/
├── main.py          # 元数据 + module_hook（薄入口）
├── service.py         # 业务编排：提取/归一/去重/下载/发送
├── douyin_api.py      # 网络层：短链、详情、主页作品、流式下载、去重
├── media.py           # 纯逻辑：链接提取、无水印地址、图集整理、缓存命名
├── abogus.py          # a_bogus 签名
├── xbogus.py          # X-Bogus 签名
├── sm3.py             # SM3 哈希（纯 Python，无依赖）
├── forward.py         # 合并转发节点构建（纯函数）
└── config_schema.py   # WebUI 配置表单
```

## 测试

```bash
python -m pytest tests/test_douyin_signature.py tests/test_douyin_media.py \
                 tests/test_douyin_api.py tests/test_douyin_forward.py \
                 tests/test_douyin_service.py -q
```

全部离线（固定签名向量、假 API / 假 stream / 假 Bot），不联网。

## 算法来源与许可（重要）

签名算法常量（字符表、UA 特征码、浏览器指纹模板）与公开逆向实现一致，来源：

- `abogus.py` 参考 `DLWangSan/douyin_parse` 的 `abogus.py`，
  其自述「基于 Evil0ctal/Douyin_TikTok_Download_API，原始实现来自 JoeanAmier/TikTokDownloader（**GPL v3**）」；
- `xbogus.py` 参考同一项目的 `xbogus.py`（自述来自 Evil0ctal/Douyin_TikTok_Download_API，Apache 2.0）。

⚠️ **上游 `abogus` 的实现血统包含 GPL v3 代码**，而本仓库目前没有 LICENSE 文件。
若本项目要对外分发，需要先确认该部分的许可兼容性（或替换为独立实现）。
本插件的其余部分（链接整理、图集处理、转发、缓存、服务编排）为独立实现。

## 免责声明

仅供学习交流使用，请勿用于商业用途。下载的内容请遵守相关法律法规和平台规定。
