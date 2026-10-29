# 插件代码结构与风格规范

> 适用：`app/plugins/<name>/`（内置插件）与 `data/module/plugins/<name>/`（本地/第三方插件）。
> 一句话：**`main.py` 只做接线与编排，实现细节进 `src/`；入口文件与清单永远在根。**
> 配套闸门：`tests/architecture/test_plugin_layout.py`；检查工具：`scripts/plugin_layout.py --check`。

---

## 1. 目录骨架

```text
<插件根>/
├── module.json          # 必须：清单（框架契约）
├── main.py              # 必须：Module 类 + 生命周期 + 编排（唯一入口）
├── __init__.py          # 必须：包标记（插件按包加载，相对导入依赖它）
├── config_schema.py     # 可选：WebUI 配置表单（框架契约）
├── src/                 # 实现细节：工具函数 / 领域逻辑 / 协议算法
│   ├── __init__.py
│   ├── api.py           # 按领域命名，别用 utils.py / helpers.py
│   ├── parsing.py
│   └── store.py
├── pages/index.html     # 可选：插件自带设置页
├── README.md            # 推荐：做什么 / 怎么配 / 数据存哪
└── requirements.txt     # 可选：第三方依赖清单
```

- **根目录白名单**：`module.json`、`main.py`、`__init__.py`、`config_schema.py`、
  `README.md`、`requirements.txt`、`pages/`、`src/`。
- **根目录也能开别的文件**，但要同时满足：只有一个文件、≤100 行、只被 `main.py` import、
  在 README 里写明用途。不满足则进 `src/`（例：79 行的 `wbi.py` 可留根，448 行的
  `bilibili_api.py` 必须进 `src/`）。
- `src/` 内部：文件按领域命名；单文件建议 ≤200 行、上限 300 行；同一子域 ≥4 个文件才拆子包。
- **相对导入**：插件内部一律 `from .src.xxx import ...`，禁止 `from app.plugins.<name>.xxx import ...`
  ——后者在插件被安装到 `data/module/plugins/` 后按合成包名加载，绝对路径会失效。

## 2. `main.py` 只放这些

- 类属性（`name/sign/description/permission/category/pinned/subscribe/order/supports_single_service`）；
- `default_config`（与 `config_schema.py` 的键一一对应）与 `config_schema = SCHEMA`；
- 生命周期：`on_load / on_unload / on_online / on_offline`；
- hook：`@module_hook` / `@llm_hook` / `@send_hook`；
- **编排**：读开关 → 取上下文 → 调 `src/` → 产生副作用（发消息 / 落数据 / 建任务）。

不要放在 `main.py`：正则表、HTML/JSON 解析、加解密与签名、HTTP/重试细节、SQL、长常量表、
与框架无关的通用工具。这些一律进 `src/`。

体量：建议 ≤150 行，上限 **220 行**；超过必须拆。行数只是触发器——不到 220 行但塞了
HTTP/正则，同样要抽走；反之像 `group_log` 那样 195 行纯生命周期与接线的，允许保留。

单个 hook 建议 ≤30 行：

```python
@module_hook("message_group", order=10)
async def handle_message(self, event):
    if not self.config.get("enable", True):
        return
    link = extract_link(event.message)      # src/parsing.py 的纯函数
    if link is None:
        return
    await self.send_to(event.session_id, build_reply(link))
```

## 3. `src/` 写什么

- **纯函数优先**：收参数、返结果；不读 `self`、不读全局配置、不直接发消息。需要配置就由
  `main.py` 取好传进来（这样能单测）。
- 单向依赖：`main.py` → `src/`；`src/` 禁止 import `main`。
- 常见落点：`api.py`（HTTP/签名）、`parsing.py`（提取/清洗）、`store.py`（持久化）、
  `format.py`（文案/消息拼装）、`consts.py`（常量表）、`types.py`（dataclass/TypedDict）。

## 4. 写作风格

| 维度 | 规则 |
|---|---|
| 文件头 | 一行 docstring 说明职责；拆分原因用 3-5 行讲清（保留现有好习惯） |
| 注释 | 讲「为什么」，不复述代码；中文 |
| 命名 | 函数/变量 `snake_case`；常量 `UPPER_SNAKE`；私有 `_helper`；除 `ctx/config` 外不缩写 |
| 类型 | `src/` 导出函数与 `main.py` 私有方法标注参数/返回；模块头 `from __future__ import annotations` |
| 导入 | stdlib → `app.*` → 本地（`.config_schema` / `.src.*`），组间空行 |
| 日志 | 框架 logger：`logger.add_info(account_label(self.bot_id))`；禁 `print`；失败必须带原因 |
| 异常 | hook 不吞异常（框架记录堆栈）；可降级才捕获并 `warning/debug` 留痕 |
| 异步 | 网络用 async 客户端；同步库 `asyncio.to_thread`；禁 `time.sleep`；后台任务 `on_unload` 取消/关闭 |
| 配置 | 只在 `main.py` 读 `self.config.get(key, default)`，默认值与 schema 一致；`src/` 不读全局 |
| README | 三段：做什么 / 配置项含义 / 数据与权限 |

## 5. 模板

`docs/plugin-template/` 是最小可运行骨架（`main.py` + `src/reply.py` + 清单 + README）。
复制改名即可开始；`module.json` 的 `name` 必须与目录名一致。

## 6. 检查与迁移

```bash
python scripts/plugin_layout.py --check        # 列出不合规项与建议移动的文件
python scripts/plugin_layout.py --check --json # 机器可读（CI / 编辑器插件用）
pytest tests/architecture/test_plugin_layout.py -q
```

迁移约定：一次性把根级实现文件移进 `src/` 并改 import，**不保留旧路径 shim**；
根级白名单与存量豁免清单在闸门里以「棘轮」维护——迁完一个删一条，只收紧不放宽。

当前状态（2026-10-29）：`app/plugins/` 的 10 个内置插件已全部符合本规范——实现文件都在
`src/`（`llm_tools_extra/onebot/` 也收进 `src/onebot/`），只有 `service.py` 这类
「≤100 行且只被 main import」的小模块按例外留在根；闸门里的迁移基线已清空。
本地插件（`data/module/plugins/<name>/`）不在本仓库，安装形态同样适用本规范，
用 `python scripts/plugin_layout.py --check` 可随时核对。
