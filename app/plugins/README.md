> 本文只负责：说明 `app/plugins/` 放什么、不放什么。插件开发指南见 `docs/MODULE_DEV.md`，框架侧见 `../modules/README.md`。

# `app/plugins/` —— 业务插件

**一句话：仓库自带的 14 个业务插件，每个子目录 = 一个插件。**

> 这里原名 `module/modules/`（仓库根目录下），P5 搬进 `app/`，因为「代码」应该
> 跟着代码树走，而 `module/` 现在只剩**运行时数据**（`configs/` `data/` `plugins/`
> 与 `uninstalled_modules.json`）。

## 目录约定

```
app/plugins/<name>/
├── __init__.py         包标记（插件内部一律用相对导入：from .service import ...）
├── main.py           **唯一必需**：导出 class Module(BaseModule)
├── config_schema.py    配置 schema（WebUI 表单由此渲染）
├── service/            业务逻辑（推荐；df_password / time_sign_in / recall_back 是范本）
├── pages/index.html    可选：插件自带页面（GET /api/module/<name>/page）
└── module.json         可选：manifest（名称/版本/入口/权限声明）
```

## 现有插件

`单一服务` 列 = 该插件声明了 `supports_single_service = True`（同群多账号时可指定唯一
负责账号，见 `docs/MODULE_DEV.md` §2.5.1）。纯 LLM 策略插件（`subscribe = ()`）不声明。

| 插件 | 干什么 | 单一服务 |
|---|---|---|
| `astrbot_alignment` | 对齐 AstrBot 的组装方式（参考实现，带 `pages/`） | — |
| `bilibili_parser` | 解析 B 站链接（视频/动态，含 wbi 签名） | ✓ |
| `douyin_parser` | 解析抖音链接（含 abogus/xbogus 签名） | ✓ |
| `df_password` | 定时推送密码（`service/` 子包，推荐范本） | ✓ |
| `emoji_reply` | 表情回应 | ✓ |
| `example` | 最小示例（含 `pages/` 读写示例） | — |
| `group_log` | 群消息记录 | ✓ |
| `group_request` | 入群申请处理 | ✓ |
| `llm_enhance` | 防抖合并 + 群聊用户信息感知 | — |
| `log_sender` | 聊天里用 `/log`（user.log）/ `/logs`（四个日志文件）取回本轮次日志 | ✓ |
| `poke_reply` | 戳一戳回应 | ✓ |
| `recall_back` | 撤回回应（`service/` + `recall_db.py`） | ✓ |
| `time_sign_in` | 每日打卡（`service/` 子包） | ✓ |
| `what_to_eat` | 吃什么 | ✓ |

## 边界

- 插件**只通过** `app.plugin`（插件 API）与 `app.agent`（`tool` / `skill`）使用框架，
  **不得** import `app.webui` 或 `app.bootstrap`。
- 插件之间**不得**互相 import。要共享代码就抽到 `app/infrastructure/`（如
  `fs/media_cache.py` 就是从插件侧下沉的）。
- 插件位置与 import 名**已解耦**：加载器按文件路径加载，写进 `sys.modules` 的是合成
  包名 `qqbot_plugin_<name>`。所以搬家不会再牵动 import 语句。

## 加一个插件

`docs/MODULE_DEV.md` 有完整步骤。最短路径：建目录 → 写 `main.py` 导出 `Module` →
重启或点 WebUI 的「刷新模块」。
