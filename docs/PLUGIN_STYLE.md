# 插件代码结构与风格规范

> 适用：`app/plugins/<name>/`（内置，随仓库）与 `data/module/plugins/<name>/`（第三方，运行时安装、不进仓库）。
> 一句话：**`main.py` 只做声明与接线，实现细节进 `src/`；入口与清单永远在根。**
> 闸门：[test_plugin_layout.py](../tests/architecture/test_plugin_layout.py)；检查器：[plugin_layout.py](../scripts/plugin_layout.py)。

---

## 1. 目录骨架

```text
<插件根>/
├── module.json          # 必须：清单
├── __init__.py          # 必须：包标记（相对导入依赖它）
├── main.py              # 必须：Module(BaseModule) + 生命周期 + 编排
├── config_schema.py     # 可选：SCHEMA（WebUI 配置表单）
├── README.md            # 可选：做什么 / 怎么配 / 数据存哪
├── requirements.txt     # 可选：pip 依赖清单（module.json 的 requirements 指向它）
├── pages/index.html     # 可选：插件自带设置页（静态资源同目录）
└── src/                 # 实现细节（必须有 __init__.py）
    ├── __init__.py
    ├── api.py           # 按领域命名，别用 utils.py / helpers.py
    └── store.py
```

`module.json` 最小形态（`name` 必须等于目录名）：

```json
{ "name": "my_module", "display_name": "我的模块", "version": "1.0.0",
  "sign": "MyModule", "description": "一句话", "entry": "main.py", "api_version": 1 }
```

- 必填 `name` / `display_name` / `version`；`name` 只能是 `[A-Za-z_][A-Za-z0-9_]*`；`api_version` 目前只支持 `1`。
- 可选 `sign` / `description` / `author` / `entry`（缺省 `main.py`）/ `requires`（字符串数组）/ `requirements`（清单文件名）。
- 第三方安装时框架校验 `name == 目录名`，不一致拒绝加载（`plugin/loader.py`）。

## 2. 硬规则（闸门逐条可判定）

| 规则 | 检查码 | 内容 |
|---|---|---|
| 入口与清单 | A1 / A2 | 根下必须有 `main.py`、`module.json` |
| 包标记 | A3 / D1 | 插件根与 `src/` 都必须有 `__init__.py` |
| `main.py` 体量 | B1 | ≤ **220 行** |
| 根目录白名单 | C1 / C2 | 只允许 `module.json` `main.py` `__init__.py` `config_schema.py` `README.md` `requirements.txt` `pages/` `src/` |
| 根级实现文件 | C3 | ≤100 行**且**只被 `main.py` import 才可留根，否则进 `src/` |
| 依赖方向 | D2 | `src/` 不得 `import main` |
| 导入 | E1 | 插件内部只用相对导入，禁 `from app.plugins.<自己>.xxx import` |

E1 的理由：loader 按合成包名 `qqbot_plugin_<name>` 加载插件（`plugin/loader.py`），绝对路径安装到 `data/module/plugins/` 后会失效。

## 3. `main.py` 只放这些

- 类属性：`name` / `sign` / `description` / `permission` / `category` / `tags` / `order` / `hidden` / `pinned` / `subscribe`；
- 声明：`default_config`（与 `config_schema.SCHEMA` 键一一对应）、`config_schema = SCHEMA`、`requires`；
- 生命周期：`on_load` / `on_unload` / `on_online` / `on_offline`（装配 ≠ 可跑，见 [MODULE_DEV.md](MODULE_DEV.md) §3）；
- 接线：`@module_hook` / `@llm_hook` / `@send_hook` / `@before_send_hook` / `@api_hook` / `@bot_lifecycle_hook` / `@event_completed_hook` / `@tool_call_hook`、`@command` / `@command_group`、`@tool`；
- 编排：读开关 → 取上下文 → 调 `src/` → 产生副作用（`send_to` / 落数据 / `spawn`）。

不放：正则表、HTML/JSON 解析、加解密与签名、HTTP/重试细节、SQL、长常量表。

## 4. `src/` 写什么

- 纯函数优先：收参数、返结果，不读 `self`、不读全局配置、不直接发消息；需要配置由 `main.py` 取好传进来（能单测）。
- 单向依赖：`main.py → src/`。
- 按领域命名：`api.py`（HTTP/签名）、`parsing.py`（提取/清洗）、`store.py`（持久化）、`format.py`（文案拼装）、`consts.py`、`types.py`。

## 5. 写作风格

| 维度 | 规则 |
|---|---|
| 文件头 | 一行 docstring 说职责；拆分原因 3-5 行讲清 |
| 注释 | 讲「为什么」，中文，不复述代码 |
| 命名 | 函数/变量 `snake_case`，常量 `UPPER_SNAKE`，私有 `_helper` |
| 类型 | 公开函数标注参数/返回；文件头 `from __future__ import annotations` |
| 导入 | 标准库 → `app.*` → 本地（`.config_schema` / `.src.*`），组间空行；行宽 120（ruff `E/F/I/UP/B`） |
| 日志 | `logger.add_info(account_label(self.bot_id))`；不 `print`；失败必须带原因 |
| 异常 | hook 不吞异常（框架记堆栈）；可降级才捕获并 `warning/debug` 留痕 |
| 异步 | 网络用 async 客户端；同步库 `asyncio.to_thread`；不用 `time.sleep`；后台任务用 `self.spawn` |
| 配置 | 读本模块配置只走模块自己的门面（`self.config` / 传参进 `src/`），默认值与 schema 一致；不读别的模块或 Agent 配置 |
| 数据 | `get_config_path(self.module_name)` / `get_data_path(self.module_name)`；不写插件目录 |

## 6. 配置表单要点

- 类型白名单在 `plugin/schema_utils.KNOWN_TYPES`：`text/string/password/number/integer/float/boolean/bool/select/textarea/time`、`string_list/list/dynamic/repeater`、`resource/resources`、`group`。
- 未知 `type` 不阻断，但会进 `issues`（`unknown_type:...`），前端退化成文本框。
- `resource` 字段必须写 `resource` 种类；悬挂引用按 `on_missing` 处理（默认 `keep`，`clear` 表示保存时丢弃）。

## 7. README 与 `pages/`

- README 三段：做什么 / 配置项含义 / 数据与权限。
- `pages/index.html` 是设置页唯一入口，静态资源放 `pages/` 下；越界路径（`../`）由 `registry.module_asset_path` 拒绝。

## 8. 模板

[docs/plugin-template/](plugin-template/) 是最小可运行骨架（`module.json` + `main.py` + `config_schema.py` + `src/reply.py` + `README.md`），复制改名即可；模板自身也过闸门（缺文件报 T1，`main.py` >150 行报 T2）。

## 9. 检查与迁移

```bash
venv\Scripts\python.exe scripts\plugin_layout.py --check          # 列出不合规项
venv\Scripts\python.exe scripts\plugin_layout.py --check --json   # 机器可读
venv\Scripts\python.exe -m pytest tests/architecture/test_plugin_layout.py -q
```

- `--check` 同时扫 `app/plugins/` 与 `data/module/plugins/`；前者必须 0 条，后者是运行时目录（`.gitignore` 的 `module/`），存量不合规只影响本机安装，不进 CI。
- 迁移约定：一次性把根级实现文件移进 `src/` 并改 import，**不留旧路径 shim**；`BASELINE_*` 棘轮已清空，规则只收紧。
- 当前状态：`app/plugins/` 的 5 个内置模块（`llm_trigger` / `llm_context` / `llm_memory` / `llm_tools_extra` / `llm_enhance`）全部合规。
