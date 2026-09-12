# B站视频解析（bilibili_parser）

自动解析群聊/私聊中的 B 站链接（直链 / b23 短链 / QQ 小程序卡片），
**下载 720P 单文件 mp4**，并以**合并转发**发送：节点 1 = 视频简介，节点 2 = 720P 视频。

```
用户: BV1zq836rEbk
机器人: ┌ 群聊的聊天记录 ────────────────┐
        │ B站解析: 📺 标题/封面/UP/统计/简介/链接 │   ← 节点 1
        │ B站解析: [视频 720P]                    │   ← 节点 2
        └────────────────────────────────┘
```

## 处理流程

```
handle（快）
  1 总开关 → 2 群范围 → 3 文本/JSON 段提取链接 → 4 b23 短链归一
  → 5 BV 去重（按 bot 隔离）→ 6 限数 → 7 view 取视频信息 → 8 event.llm.stop()
        ↓ 交给 services.task_manager 后台任务（避免阻塞单 bot 串行事件队列）
_parse_and_send（慢，仅第一个视频）
  9  playurl<wbi> 取单文件流（fnval=0，实测 mp4，免 ffmpeg）
  10 体积预判 → 11 流式下载到本地缓存（.part + 原子改名）
  12 send_forward_msg（简介节点 + 视频节点）
```

## 720P 是怎么拿到的

| 关键点 | 说明 |
|---|---|
| 单文件模式 | `playurl` 传 `fnval=0` + `qn=64`，返回 `format=mp4720`、音视频合一的 **mp4**（`avc1`+`mp4a`），**不需要 ffmpeg** |
| WBI 签名 | 该接口需签名：`nav` → `img_url`/`sub_url` → 32 位 `mixin_key` → `w_rid`。`nav` 未登录返回 `-101` 但密钥仍有效（`wbi.py`） |
| 密钥缓存 | 类级缓存 6 小时；遇 `-403` 视为密钥轮换，强刷后重试 |
| 风控 | **不要在 `BILI_HEADERS` 里覆盖 `User-Agent`**：`impersonate="chrome"` 提供的是版本自洽指纹（TLS JA3/JA4 + h2 帧 + 自带 UA），写死旧 UA 就形成「指纹新、UA 旧」的矛盾 → 实测单次请求 10 次里 5 次被风控（`code=0` 但 `data` 只有 `v_voucher`）；**去掉 UA 覆盖后 0 次**（8 个视频 7 OK / 1 个受限稿件）。退避 2s/5s/10s ×4 次仅作 IP 限速兜底 |
| 下载中断 | CDN 偶发 `curl: (92) HTTP/2 stream not closed cleanly`；实测 h2 与 v1.1 各 3 次均全量成功（31.7MB），与协议无关 |
| 本地缓存 | 同一视频只下载一次：`module/data/bilibili_parser/video_cache/<bvid>_p1.mp4`，按 TTL + 容量上限淘汰 |
| 只下一个 | 一条消息命中多个视频时，只有**第一个**下载视频，其余只发简介节点 |

## 配置项

| key | 默认 | 说明 |
|---|---|---|
| `use_forward_msg` | `true` | 合并转发；关闭则回落旧版引用回复（不下载视频） |
| `enable_video_download` | `true` | 下载并附带 720P 视频 |
| `video_quality` | `720` | `360` / `480` / `720`；实际清晰度不足时在降级文案中标注 |
| `video_max_mb` | `80` | 超过则只发简介节点 |
| `video_download_timeout` | `60` | 流式下载单请求超时（秒） |
| `video_cache_enabled` | `true` | 复用本地缓存 |
| `video_cache_ttl_minutes` | `720` | 缓存保留时长；`0` 不按时间淘汰 |
| `video_cache_max_mb` | `1024` | 缓存容量上限；`0` 不限制 |
| `is_reply` | `true` | **仅** `use_forward_msg=false` 时生效 |

其余原有配置（`max_parse_count` / `cookie` / `show_cover` / BV 去重 / 群范围）不变。

## 依赖与前提

- 需要 NapCat 支持 `send_group_forward_msg` / `send_private_forward_msg`（节点内的视频片段走本地 `file:///` 上传）；
- `1/bili-video-parser` 是**同源的参考实现**（sync + requests），本插件按其结论做了 async 移植：
  `wbi.py`（签名纯函数）、`video.py`（取流整理 + 缓存策略）、`bilibili_api.py`（网络层）。
  该目录未纳入 git，插件不依赖它，可独立部署。

## 降级链

```
取流/下载失败 → 节点 2 换成文本（原因 + 链接），节点 1（简介）照常送达
use_forward_msg=false → 旧版单条引用回复
```

## 文件结构

```
bilibili_parser/
├── module.py          # 元数据 + module_hook（薄入口）
├── service.py         # 业务编排：提取/去重/取流/下载/发送
├── bilibili_api.py    # 网络层：短链、view、nav(密钥)、playurl、流式下载
├── wbi.py             # WBI 签名纯函数
├── video.py           # 单文件取流整理 + 缓存策略（纯逻辑）
├── forward.py         # 合并转发节点构建（纯函数）
└── config_schema.py   # WebUI 配置表单
```

## 测试

```bash
python -m pytest tests/test_bilibili_wbi.py tests/test_bilibili_video.py \
                 tests/test_bilibili_forward.py tests/test_bilibili_service.py -q
```

全部离线（签名固定向量、假 stream、假 API/假 Bot），不联网。
