# EXE 打包方案设计（PyInstaller / Windows 单文件程序）

> 状态：**提案，未立项**（2026-10-27）
> 结论：**技术可行**，成本集中在「路径适配层」，不在依赖。
> 预计工作量 **2~3 天**（含联调），产出物为 **onedir**（不是 onefile）。
> 本文件只回答「怎么做、代价是什么、要不要做」；未获批准前不改任何代码。

---

## 0. TL;DR

| 问题 | 回答 |
|---|---|
| 能打成 exe 吗 | **能**。全部依赖在 Windows 有现成 wheel，无需编译器；没有多进程/自我重启式代码 |
| 直接 `pyinstaller main.py` 能跑吗 | **不能**。frozen 后 6 个地方会坏，其中 4 个必然踩（数据落临时目录、插件加载不到、前端 503、`.env`/令牌丢失） |
| 改动量 | 新增 1 个路径适配模块（约 60 行）+ 改 6 处引用 + 1 个 `.spec`；业务逻辑零改动 |
| onefile 还是 onedir | **onedir**。onefile 每次启动解压 ~100MB 到 temp，慢、杀软误报高、退出即删目录 |
| 体积 | 预估 **90~150 MB**（onedir 目录），启动 2~5 秒 |
| 真正的代价 | 不是打包难度，而是**长期多养一条分发通道**（CI、自更新、插件生态、每次依赖升级都要重新验证） |

---

## 1. 目标与非目标

**目标**

1. 用户拿到一个目录 / 一个 `.exe` 双击即起，**不需要安装 Python、不需要建 venv、不需要联网装依赖**；
2. 与源码运行**语义一致**：同样的 `data/`、`.env`、插件、WebUI、WebSocket 行为；
3. 内置插件（24 个）开箱可用；zip 插件仍可安装、可热加载；
4. 产物可复现、可校验（复用 `deploy.json` 式的逐文件 sha256 思路）。

**非目标**

1. **不替代**现有 `-kit.zip` 免安装包与 wheel 发行（三条通道并存，见 §2）；
2. **不内置 NapCat**：协议端是用户自备的外部程序，本项目不托管、不下载、不启停（见 [`onebot-v11-connection-scope.md`](onebot-v11-connection-scope.md)）；exe 只负责"程序本体"；
3. 不做 macOS/Linux 二进制；
4. 不保证插件可以访问 exe 内的只读资源（插件应只用 `services` 与可写数据目录）。

---

## 2. 与既有决策的关系（必须先看这一节）

`docs/oneclick-bundle-and-onboarding-design.md` 的 **P5** 已决策：**「免安装」= zip + 内嵌 Python，不是 exe**。
本提案不推翻该决策，而是提出**第二条通道**，并附带一个现状事实：

> **`--embed-python` 目前是半成品**：`scripts/build_deploy_kit.py:211-217` 只是把 `python-embed.zip` 原样塞进包，
> 全库 grep `python-embed` 只在构建脚本里出现，`start.py` **没有任何解包/使用逻辑**。
> 也就是说现在的 "免安装包" 仍然要求用户机器上已有 Python 3.10+，README-FIRST 里写的"首次启动自动建 venv"成立的前提是系统 Python 存在。

所以三条路线的真实状态是：

| 通道 | 现状 | 用户要装什么 | 维护成本 |
|---|---|---|---|
| wheel（`pip install qqbot-next`） | 可用 | Python + pip | 低 |
| `-kit.zip` | 可用，但**名不副实**（要系统 Python） | Python 3.10+ | 低 |
| **exe（本提案）** | 不存在 | **什么都不装** | **高**（见 §6） |

**若批准，建议顺带把 B 方案补全**（`start.py` 识别并解包 `python-embed.zip`）：它比 exe 便宜得多，
且能覆盖"不想装 Python"这个诉求的 80%。两者不冲突，但**同时做两件就是三倍维护**，需明确优先级。

---

## 3. 现状盘点：可打包性证据

### 3.1 对打包友好的（不需要动）

| 项 | 证据 |
|---|---|
| 入口干净 | `main.py` → `asyncio.run(app.bootstrap.run())`；`app/bootstrap.py:589` 另有同步 `main()` |
| 单进程 Web 服务 | `app/bootstrap.py:552` 传 **app 对象** 给 `uvicorn.Config`，`reload=False`，没有 `uvicorn.run()` 字符串入口 |
| 无自我重启 | `app/` 内无 `multiprocessing` / `os.exec` / 自我 `sys.executable` 重入 → **不需要 `freeze_support()`**（`start.py` 里的 `sys.executable` 是构建期起 venv/装依赖，不进 exe） |
| 插件按路径加载 | `app/plugin/loader.py:347-383` `spec_from_file_location` + `exec_module`，合成包名 `qqbot_plugin_<name>` |
| 前端已构建 | `app/webui/dashboard/dist` 130 文件 / 5.0 MB |
| 依赖全有 wheel | fastapi / uvicorn[standard] / aiohttp / curl_cffi / brotlicffi / aiosqlite / pydantic / pillow |
| 无系统级外部工具依赖 | 唯一子进程是 NapCat（外置）与用户自配的 MCP command（`app/plugins/llm_tools_extra/mcp.py:54`） |

### 3.2 阻断项（P0，不改必炸）

| # | 位置 | 症状 | 修法 |
|---|---|---|---|
| 1 | `app/core/settings.py:11` `ROOT_DIR = Path(__file__)...`；`:40-41` db/logs、`:52` module_dir、`:55` plugins_dir 全挂它 | frozen 下 = `_MEIPASS`（onefile 退出即删）→ 数据每次清零、令牌每次变 | 引入「包根只读 / 数据根可写」双根（§4） |
| 2 | `app/webui/app.py:59-63` `_DASHBOARD_DIST`、`:94` `index.html`、`:218` `StaticFiles` | 首页返回 503「前端尚未构建」 | 静态资源按**包根**定位 + datas 打进包 |
| 3 | `app/plugin/catalog.py:163` `iterdir()`、`registry.py:310/365` 目录扫描 | 一个插件都加载不到 | `app/plugins` 整目录作为 datas |
| 4 | `app/core/webui_token.py:74-86` `.env` 兜底 `ROOT_DIR/.env` + `settings.py:26` `env_file=".env"`（相对 CWD） | `.env` 写进临时目录；令牌重启即变 | `env_file` 传绝对路径（数据根 / exe 同级） |
| 5 | `app/core/env_template.py:63/66/75/79` 生成的是**相对路径** `data/app.db`、`logs`、`data/module`、`app/plugins` | 从快捷方式/计划任务启动时按 CWD 解析 → 数据跑到别处 | frozen 下模板写绝对路径（或注入数据根） |
| 6 | `app/plugins/llm_tools_extra/onebot/manifest.py:36` `Path(__file__).with_name("manifest.json")`（114 KB） | `llm_tools_extra` 起不来，工具清单为空 | JSON 作为 datas 落在同目录 |

### 3.3 派生问题（P1，不炸但会错）

| 位置 | 问题 | 处理 |
|---|---|---|
| `app/application/capabilities.py:47-53` | `importlib.metadata.version("qqbot-next")` frozen 下 `unknown` | 只影响能力清单展示；可改为读打包常量 |
| `app/application/update/version.py:35-43` | `read_local_version` 读 `pyproject.toml`，frozen 下恒返回 `0.0.0` | 改为读打包常量，否则更新器永远认为「有新版」 |
| `app/application/update/updater.py:28-29` `UPDATABLE_PREFIXES` | 自更新会**覆盖 `app/`、`main.py` 等源码文件** | frozen 下必须禁用（§6.3） |
| `app/infrastructure/fs/paths.py:28/50` | `_PROJECT_ROOT` 拼 `data/llm` | 改数据根 |
| `app/agent/session/enrich.py:61` | 同上，拼 `data/llm` | 改数据根 |
| `app/infrastructure/config/config_service.py:162/181/197` | 用 `root` 找旧布局配置（`webserver/webconfig.json` 等）读取迁移 | 传**包根**；frozen 下目录不存在 = 无可迁移，安全 |
| `app/application/module_install_service.py` / `installer.py:184` | zip 插件写 `data/module/plugins`、比对 `builtin_dir` | 写数据根；`builtin_dir` = 包根 `app/plugins` |

### 3.4 一个容易被忽略的耦合：插件存在**双身份**

框架代码里有多处**静态 import 插件内部模块**（架构上属于 webui/agent → plugins 的越界）：

- `app/webui/api/agent.py:17-18` → `app.plugins.llm_tools_extra.src.onebot.{manifest,security}`
- `app/agent/tools/state.py:137/198/296` → 同上
- `app/agent/storage/__init__.py:28/39` → `app.plugins.llm_memory.store`、`app.plugins.llm_knowledge.store`

后果：同一个插件文件在 `sys.modules` 里可能同时以 `app.plugins.X...`（静态 import）与
`qqbot_plugin_X...`（加载器别名）两个名字存在 → **模块级单例/缓存分裂**。
这在**源码模式下今天就已经如此**，frozen 不制造问题，但会让「datas 清单」变得不确定：
PyInstaller 会因静态 import 把部分插件模块**冻结成字节码**，同时加载器又从磁盘按路径加载一份。

**打包时的处理**：`app/plugins` 同时作为 datas（保证加载器能按路径读到）与静态分析自动收集的部分保持现状，不额外加 hiddenimports。
**顺带建议**（可另开单）：把这三处越界 import 改成通过 `services`/资源接口读取清单，让插件真正成为"可热替换的磁盘包"，否则 exe 版会长期带着一份"冻结副本"。

---

## 4. 核心设计：双根模型

### 4.1 三个根

```
bundle_root   只读   Python 代码 + 前端 dist + 内置插件源码 + templates
              源码运行 = 仓库根
              frozen    = sys._MEIPASS（onefile/onedir 都是解包目录，读写该目录不可依赖）

data_root     可写   data/（app.db、module/、llm/、updates/、protocol/）、logs/、.env
              源码运行 = 仓库根（现状不变）
              frozen    = <exe 所在目录>  ← 默认，绿色便携
                          或 %APPDATA%\QQBotNext（可选，见 §6.4）

cwd           不可依赖   快捷方式/计划任务/管理员启动都可能不同
```

**原则**：`bundle_root` 只用于「读随程序分发的文件」；任何 `write_text` / `mkdir` / `sqlite connect` 只能落在 `data_root`。
写这条原则的地方集中到一个小模块，业务代码只调函数，不再自己拼 `__file__`。

### 4.2 新增 `app/core/runtime_paths.py`

```python
"""运行时根目录解析：包根（只读）/ 数据根（可写）的唯一出口。"""

def is_frozen() -> bool: ...            # getattr(sys, "frozen", False)
def bundle_root() -> Path: ...          # frozen: Path(sys._MEIPASS)；源码: 仓库根
def data_root() -> Path: ...            # frozen: exe 同级；源码: 仓库根
def env_path() -> Path: ...             # data_root()/".env"
def ensure_data_dirs() -> None: ...     # data/ data/module data/llm logs
```

约束：**只依赖标准库**（这个模块会被 settings / logger 在最早期加载，不能引入 app 内依赖，参照 `start.py` 的"只准用标准库"原则）。

### 4.3 改造点位

| 文件 | 改法 |
|---|---|
| `app/core/settings.py:11` | `ROOT_DIR` 语义改为 **`BUNDLE_ROOT`**（拆包只读根）；新增 `DATA_ROOT`；`db_path`/`log_dir`/`module_dir` 默认值改挂 `DATA_ROOT`；`plugins_dir` 挂 `BUNDLE_ROOT`；`project_root` 属性保留但注释写明"仅旧布局迁移读取用" |
| `app/core/settings.py:26` | `env_file` 改由 `runtime_paths.env_path()` 传入绝对路径；同时允许环境变量 `QQBOT_ENV_FILE` 覆盖（测试需要） |
| `app/core/env_template.py:63-81` | 模板值在 frozen 下写**绝对路径**（`data_root()/data/app.db` 等），源码模式保持相对路径（不改现有用户行为） |
| `app/core/webui_token.py:74-86` | 兜底改为 `runtime_paths.env_path()`，不再用 `ROOT_DIR` |
| `app/webui/app.py:59-63` | `_BASE_DIR` → `bundle_root() / "app" / "webui"` |
| `app/infrastructure/fs/paths.py:28/50` | `_PROJECT_ROOT` → `bundle_root()`（迁移源）/ `data_root()`（`data/llm`） |
| `app/agent/session/enrich.py:61` | → `data_root() / "data" / "llm"` |
| `app/plugins/llm_tools_extra/onebot/manifest.py:36` | 保持 `Path(__file__).with_name`（spec 保证 json 同目录）；或改 `importlib.resources.files(...)` |
| `app/application/capabilities.py:47` / `update/version.py:35` | 优先读打包常量 `app/_build_info.py`（spec 生成），回退现有实现 |
| `app/application/update/updater.py` | frozen 下 `UpdateService.status()` 直接返回「exe 版不支持自更新」，WebUI 隐藏入口 |

**兼容性底线**：源码运行的行为**一个字节都不能变**（现有 `data/`、`.env`、相对路径、令牌位置全部维持原样）——
所有改动都是 `if is_frozen(): ... else: 现状`。测试只需覆盖 `is_frozen() == False` 的旧路径 + monkeypatch 的 frozen 路径。

---

## 5. `.spec` 设计

### 5.1 入口

`packaging/entry.py`（新增，不污染 `main.py`）：

```python
import multiprocessing
from app.bootstrap import main

if __name__ == "__main__":
    multiprocessing.freeze_support()   # 现在用不到，防将来加进程池
    main()
```

### 5.2 datas（必须随包，全部来自 `bundle_root`）

| 源 | 目标（包内相对路径） | 体量 |
|---|---|---|
| `app/webui/dashboard/dist` | 同名 | 130 文件 / 5.0 MB |
| `app/webui/templates` | 同名 | `login.html` 6 KB |
| `app/plugins` | 同名（**排除 `__pycache__` / `*.pyc`**） | 168 文件 / 794 KB（含 24×`module.json`、`onebot/manifest.json` 114 KB、`example/pages/index.html`） |
| `pyproject.toml` | 同名（可选，仅当未改为读打包常量时） | 4.6 KB |

> `.env` **绝不入包**（首次启动在 `data_root` 生成）；`data/`、`logs/` 同理。
> 打包前必须校验 `app/webui/dashboard/dist/index.html` 存在 —— 复用 `scripts/build_deploy_kit.py:64` 的 `DIST_MARKER` 思路，别让用户拿到 503 包。

### 5.3 binaries / hiddenimports

```python
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

binaries = []
binaries += collect_dynamic_libs("curl_cffi")     # _wrapper.pyd + .libs/libcurl-impersonate-*.dll（约 3.9 MB）
binaries += collect_dynamic_libs("brotlicffi")    # cffi 后端
# pillow / pydantic_core / httptools / websockets 的加速件由官方 hook 覆盖，实测确认即可

hiddenimports = [
    # uvicorn 用 import_from_string 动态解析协议/循环实现，静态分析看不到
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
    "websockets.legacy", "websockets.legacy.server",
]
# 可选：装了 sqlite-vec 才支持知识库 ANN；未装自动降级（plugins/llm_knowledge/vector.py:41-45）
try:
    import sqlite_vec
    hiddenimports += ["sqlite_vec"]
    binaries += collect_dynamic_libs("sqlite_vec")   # 向量扩展 .dll
except ImportError:
    pass
```

`app.plugins.*` **不要**写进 hiddenimports：部分模块会因静态 import 自动被收集（§3.4），
但真相来源必须是磁盘上的 `app/plugins`（datas），否则用户装新插件 / 改插件会失效。

### 5.4 其它 EXE 参数

| 参数 | 取值 | 理由 |
|---|---|---|
| `--onedir` | ✅ | onefile 每次解压 ~100MB，退出即删，杀软误报高 |
| `console=True` | ✅ | `app/bootstrap.py:538` 的**就绪清单**与令牌横幅走 `print`；`--noconsole` 会让"群里 @ 没反应"重新变成静默 |
| 图标 | **仓库里没有任何 `.ico`**（全库 grep 为空，`dashboard/` 只有 `dist/docs/node_modules/src`）→ 需新做一枚 | — |
| 版本资源 | `--version-file` 由 `pyproject.toml` 版本生成 | 文件属性里能看到版本 |
| 名称 | `QQBotNext.exe` | — |

---

## 6. 冲突与取舍

### 6.1 前端静态资源
只读即可，走 `bundle_root`，无需拷贝到数据根。`StaticFiles(directory=...)` 在 frozen 下正常工作。

### 6.2 插件生态
- **内置插件**：随包（datas）。用户改不了、也不该改（升级会被覆盖）。
- **zip 插件**：仍写到 `data_root/data/module/plugins`，热加载路径不变 → 生态能力不减。
- **插件双身份**（§3.4）不因打包恶化，但建议单独排期收敛。

### 6.3 自更新：必须砍
`app/application/update/updater.py` 的模型是「下载 kit.zip → 覆盖 `app/`、`main.py` → 重启」。
exe 里没有这些源文件，覆盖会写到解包目录且下次启动就没了。
**方案（推荐）**：frozen 下 `UpdateService.status()` 返回 `{"unsupported": true}`，WebUI 隐藏更新卡片；
版本更新改为「重新下载新 exe 目录 → 用户替换 → 数据目录不动」。
若要自动化，需要 a) 下载到临时目录 b) 退出后替换 exe（Windows 上需要 helper 进程），成本高，**建议二期再说**。

### 6.4 装在哪：Program Files 是坑
`data/module` 需要写权限。若用户把 exe 解压到 `C:\Program Files\`，非管理员启动会**静默失败**（SQLite 打不开、插件装不上）。
两条出路：
- **绿色便携（默认，推荐）**：数据根 = exe 同级，文档明确"不要放进 Program Files"；
- **安装器模式**：数据根 = `%APPDATA%\QQBotNext`，程序目录只读。多一个环境变量开关即可，§4.2 已预留。

建议先做绿色便携 + 启动时**探测写权限**，不可写就打印明确中文提示并退出，而不是抛 traceback。

### 6.5 协议端（NapCat）
必须外置，且**不归本项目管理**。exe 只解决"程序本体"，用户仍要自己下 NapCat、起好服务，
并在 WebUI「账号管理」里填 WS 地址（正向）或把 NapCat 的反向 WS 指向本程序监听地址。
文档需在第一步就写清楚，否则"双击了但连不上 QQ"会被当成 exe 的锅。
（本项目不提供安装/启停/扫码，见 [`onebot-v11-connection-scope.md`](onebot-v11-connection-scope.md)。）

### 6.6 体积与启动
`venv/Lib/site-packages` 约 70 MB，加 Pillow 14.8 MB / pydantic-core 5.7 MB / curl_cffi 原生件，预估 **90~150 MB**。
onedir 启动 2~5 秒（首轮 Windows Defender 扫描更慢）。可接受，但明显比源码版慢——需在 README 里说明。

---

## 7. 验证矩阵（DoD）

| # | 场景 | 判据 |
|---|---|---|
| 1 | 冷启动 exe | 控制台出现就绪清单；WebUI `http://127.0.0.1:9200` 首页 **200 且非 503** |
| 2 | 插件 | `/api/modules` 列出 24 个内置插件，日志无 `ImportError` |
| 3 | 数据落位 | `data/app.db`、`data/module/`、`logs/` 出现在 **exe 同级**；`_MEIPASS` 下不产生 `data/` |
| 4 | 令牌稳定 | 首次启动生成 `.env` → 重启后同一 token（`WEBUI_TOKEN` 不变） |
| 5 | 中文路径 | 解压到 `C:\Users\张三\Desktop\测试 目录\` 仍能启动（PyInstaller 经典坑） |
| 6 | 无管理员 | 普通用户启动 + 装一个 zip 插件成功 |
| 7 | 网络模块 | `curl_cffi` 路径可用（例如 bilibili/douyin 解析插件能发出请求）——验证原生件未被漏收 |
| 8 | 知识库（可选） | 若打包时装了 sqlite-vec：`enable_load_extension` 成功；未装：自动降级且不报错 |
| 9 | 协议端 | 连真实 NapCat 收一条群消息并回复（人工） |
| 10 | 更新入口 | frozen 下更新卡片隐藏/提示不支持，不出现"下载后覆盖源码"路径 |
| 11 | 退出 | 关窗口后框架自身子进程无残留、DB 正常关闭、`stat_recorder` 落库 |
| 12 | 体积/启动 | 记录 `du` 体积与冷启动耗时，写进 Release notes |

**自动化底线**：打包改动属于「跨层重构」→ 按 AGENTS.md 大工程档位：全量 `pytest -q` 全绿 + `ruff check .` 无新增。
新增单测至少覆盖 `runtime_paths`（源码模式 / monkeypatch frozen 两种）与 `env_template` 的 frozen 分支。

---

## 8. 实施计划（若立项）

| 编号 | 内容 | 产出 | 估时 |
|---|---|---|---|
| P6-1 | `app/core/runtime_paths.py` + `settings` 双根改造 + 单测 | 源码模式行为不变 | 0.5 d |
| P6-2 | 6 处 P0 点位改造 + `.env` 绝对路径 + 常量版本 | frozen 路径全绿 | 0.5 d |
| P6-3 | `packaging/entry.py` + `qqbot_next.spec` + datas/binaries | 能打出 onedir | 0.5 d |
| P6-4 | 联调（§7 的 1~8 项）+ 修 curl_cffi/插件/中文路径坑 | 可自用 | 0.5~1 d |
| P6-5 | CI：`.github/workflows/release.yml` 加 `windows-latest` job（现只有 `ubuntu-latest:13`），`pnpm build` 后跑 PyInstaller，产物 zip + sha256 附到 Release | 自动出包 | 0.25 d |
| P6-6 | 文档：README 发行通道三分支、Program Files 警告、NapCat 前置 | 用户能自助 | 0.25 d |

**必须在 CI 里做的**：`.github/workflows/release.yml:50-52` 已有 `pnpm build`，`:64-66` 已构建 kit；
exe 是 Windows-only，必须新增 `runs-on: windows-latest` 的独立 job，不能塞进现有 job。

---

## 9. 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| curl_cffi 原生 DLL 漏收 → 模块网络全挂 | 中 | 高 | `collect_dynamic_libs` + §7-7 专项验证 |
| 杀软/SmartScreen 误报（未签名 exe） | 高 | 中 | 发布 SHA256 + 文档说明；有条件则代码签名 |
| 用户把目录放 Program Files → 静默写失败 | 中 | 高 | 启动写权限探测 + 明确中文报错（§6.4） |
| 长期三通道维护（wheel/kit/exe） | 高 | 中 | 明确"exe 是附属产物"或"exe 是主推"，否则每次发版 3 倍验证 |
| 插件双身份导致单例分裂 | 低（既有） | 中 | 本提案不改；另开单收敛 §3.4 的三处越界 import |
| 从源码版迁移到 exe 时丢数据 | 中 | 中 | 首次启动检测 exe 同级 `data/` 为空 + 同目录有旧 `data/` → 提示"请手工搬迁"，不要自动猜路径 |
| 体积失控（把 node_modules / venv 误收进包） | 中 | 低 | datas 白名单制（照抄 `build_deploy_kit.py:49-57` 的排除表） |

---

## 10. 待决策项

| # | 决策 | 选项 | 建议 |
|---|---|---|---|
| D1 | **要不要做** | (a) 立项 P6，目标"官方第二通道" (b) 只做实验分支/内部使用 (c) 不做 exe，改为补全内嵌 Python（§2） | **先 (b) 或 (c)**。若诉求只是"用户不装 Python"，补全 `python-embed` 的性价比高一个数量级 |
| D2 | **数据根位置** | (a) exe 同级（绿色便携） (b) `%APPDATA%\QQBotNext`（可放 Program Files） | (a) 起步，预留 (b) 开关 |
| D3 | **自更新策略** | (a) frozen 下禁用（推荐） (b) 下载新 exe + helper 替换 | (a)。(b) 至少再 +1 天且是 Windows 特有的稳定性坑 |
| D4 | **发布承诺** | 是否承诺"与源码版功能完全对齐"（每次发版都要在 exe 上回归 §7 全表） | 建议只在 Release notes 标注"实验性构建" |

---

## 11. 附：与现有脚本的关系

- `scripts/build_deploy_kit.py` 已实现「白名单筛选 + 确定性 zip + `deploy.json` 逐文件 sha256 + 前端必须已构建」，
  exe 的 `datas` 清单和产物校验**直接复用这套规则**，不要另写一份排除表。
- `pyproject.toml:50-60` 的 `package-data` 已经声明了 `module.json` / `pages/*.html` / dashboard dist —— 与 §5.2 的 datas 清单一一对应，可作为交叉校验。
- 打包脚本建议命名 `scripts/build_exe.py`（与 `build_deploy_kit.py` 并列），本地和 CI 共用同一入口。
