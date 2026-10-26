# 模块（插件）管理：现状审计 + 统一化重构方案

> 面向：框架维护者。目标是把「内置模块 / 外部安装模块」的本地数据管理统一成一套
> **可解释、可运维、可回滚**的模型，参照 AstrBot 的插件管理实现。
>
> 本文所有"现状"结论都带 `文件:行号` 证据；标注 **实测** 的段落来自本机真实运行（附录 A）。
> 写作时基线：`main@25eb82f`。

---

## 0. 一页结论（先回答三个问题）

| 问题 | 结论 |
|---|---|
| **内置模块放哪？** | 仓库内 `app/plugins/<name>/`（随 Python 包发布，`settings.plugins_dir`，`app/core/settings.py:55`）。24 个目录里只有 **13 个**有 `module.json`，其余 10 个只有类属性元数据。 |
| **外部安装模块放哪？** | 运行时数据目录 `data/module/plugins/<name>/`（`settings.zip_plugins_dir`，`app/core/settings.py:57-60`；装配在 `app/bootstrap.py:274-281`）。当前为空。 |
| **"不在同一目录"属实吗？** | **属实**。而且 `docs/ARCHITECTURE.md:92` 写的是目标形态 `plugins/: builtin/ + installed/`——**文档描述的那套树从未实现**，所以你听到的"设计初衷"和代码现状确实对不上。 |
| **"外部 zip 不会解压"属实吗？** | **不属实（但体感像真的）**。`install_from_zip()` 确实会解压（`app/plugin/registry.py:519-535`），但**只认"单层且顶层目录名 == module.json 里的 name"这一种布局**（`app/plugin/registry.py:436-443`）。GitHub 下载的 `repo-main/`、带 `__MACOSX/` 的 mac 压缩包、多层包装的 zip，都会**装成嵌套目录 → 加载失败 → 目录已落盘但模块不出现**（实测见附录 A）。这才是"装了没用/像没解压"的真正原因。 |
| **真正该修的是什么？** | 不是把两个目录物理合并，而是：**统一清单（module.json）+ 统一编目 + 统一安装管道（布局容错/事务/冲突/更新/删除）+ 统一 UI 语义**。AstrBot 自己也不是一个目录：内置在包内 `astrbot/builtin_stars/`，第三方在 `data/plugins/`，它统一的是**上面这四件事**。 |

方案分 7 个可独立提交的步骤（§4），P0 两步就能修掉"装不上/装坏"的核心痛点，P1/P2 补齐统一编目与管理闭环，P3 收文档与迁移。

---

## 1. 现状审计

### 1.1 目录与数据布局

| 类别 | 现状路径 | 依据 | 备注 |
|---|---|---|---|
| 内置模块**代码** | `app/plugins/<name>/` | `app/core/settings.py:55`、`app/bootstrap.py:275`、`pyproject.toml:46-54` | 随 `app` 包发布；`module.json` + `pages/*.html` 有 package-data 声明 |
| 外部模块**代码** | `data/module/plugins/<name>/` | `app/core/settings.py:52,57-60`、`app/bootstrap.py:276` | `MODULE_DIR=data/module` 可覆盖；`data/module/` 整体被 `.gitignore` 忽略 |
| 模块**配置 / 权限 / 启停** | SQLite `bindings`（按 `(module, account_id)` 存） | `app/infrastructure/config/binding_store.py:368-433`；旧表 `module_config`/`module_authority` 见 `app/infrastructure/persistence/database.py:19,27`（仅迁移期读） | **与模块来源无关**：同名模块不管内置还是外部，共用同一份配置/权限/开关 |
| 模块**运行时数据** | `data/module/data/<name>/` | `app/core/settings.py:72-75`、`app/plugin/api.py:58-67` | 插件用 `get_data_path(name)` 拿，`bilibili_parser` / `douyin_parser` / `recall_back` 已在用 |
| 模块**旧配置目录**（兼容） | `data/module/configs/<name>/` | `app/core/settings.py:67-70`、`app/plugin/api.py:46-55` | 历史遗留，新代码不应再写 |
| 软卸载状态 | `data/module/uninstalled_modules.json` | `app/core/settings.py:62-65`、`app/application/module_install_service.py:16-85` | 只记"隐藏了谁"，不删文件 |
| 自定义配置页 | `<模块目录>/pages/index.html` | `app/plugin/registry.py:155-161` | **解析有 bug**，见 §1.7 P0-2 |
| 旧布局迁移 | 仓库根 `module/` → `data/module/` | `app/infrastructure/fs/migrate.py` | 已幂等，与本方案无关 |

关键结论：

1. **"代码"和"数据"已经分得比较干净**（代码在 `app/plugins` 或 `data/module/plugins`，配置在 DB，数据在 `data/module/data`）。
2. **配置/数据定位只用模块名，不带来源**——这本身是好事（升级不丢配置），但也意味着**同名冲突必须显式拒绝**，否则外部插件会静默复用/污染内置模块的配置（见 §1.7 P0-3）。
3. 目录树里"两个根"是真实存在的，但**扫描/展示/安装/卸载对两个根的处理并不一致**。

### 1.2 扫描与加载顺序

```python
# app/plugin/registry.py:211-227
roots = [(self.plugins_dir, "local")]          # 先内置
if zip_dir is not None:
    roots.append((zip_dir, "zip"))             # 后外部
```

- `discover()`（`registry.py:292-313`）与 `load_all()`（`registry.py:315-349`）都是**按 roots 顺序扫，名字进 `seen` 集合后不再看第二个根**。
  → **同名时内置永远赢，外部那份被静默忽略**（连 WARNING 都没有）。
- `_module_source()`（`registry.py:169-177`）同样"先 local 后 zip"来判定来源。
- `_resolve_module_dir(name, source=None)`（`registry.py:192-205`）在 `source=None` 时**只返回 `plugins_dir/<name>`**；`_resolve_module_path()`（`207-209`）就是这种调用。
- 入口解析在 `app/plugin/loader.py:42-54`（`main.py` 优先、兼容旧 `module.py`），导入用合成包名 `qqbot_plugin_<name>` 按路径加载（`loader.py:302-338`）——**导入层本身与来源无关，这是重构的有利条件**。
- 实例元数据：`source`、`version`（读 `module.json.version`，缺省 `0.0.0`）、`can_uninstall=True`（写死）（`loader.py:106-108`）。

### 1.3 zip 安装全链路

```
POST /api/modules/install-zip            app/webui/api/modules.py:402-419
  └─ 临时文件 mkstemp(.zip) → registry.install_from_zip()
       app/plugin/registry.py:412-458
       ├─ _extract_zip_plugin()          registry.py:519-535
       │    · 路径穿越检查（"/" 开头或含 ".."）
       │    · 校验：任意条目 endswith("module.json") 即可（_find_manifest_entry, 537-542）
       │    · z.extractall()              ← 会解压，但**不解码 GBK 文件名**
       ├─ _find_manifest()               registry.py:544-560
       │    · 只看 tmp 根 + 一层子目录
       │    · name 取 manifest["name"]，非法名拒绝（430-434）
       ├─ 顶层目录处理                    registry.py:436-443
       │    · 仅当「只有一个目录 且 目录名 == name」才 strip
       ├─ 目标已存在 → 直接报错"已安装"（444-446）
       ├─ copytree / move 到 <zip_plugins_dir>/<name>（452-455）
       └─ _after_install_from_zip() → load_single(name)（460-470）
            · 加载失败 → raise ValueError("安装后加载失败")，**但落盘目录不清理**
```

**实测（附录 A）四种常见 zip 布局**：

| zip 布局 | 结果 | 最终目录 | 能否加载 |
|---|---|---|---|
| A `demo/{module.json,main.py}`（顶层同名） | OK | `plugins/demo/main.py` | ✅ |
| B `repo-main/{module.json,main.py}`（GitHub 下载） | **OK（不报错）** | `plugins/demo/repo-main/main.py` | ❌ 嵌套 |
| C `{module.json,main.py}`（无顶层目录） | OK | `plugins/demo/main.py` | ✅ |
| D `demo/` + `docs/`（多顶层目录，含 mac `__MACOSX/` 同类） | **OK（不报错）** | `plugins/demo/demo/main.py` | ❌ 嵌套 |

> 注意：上表用桩替换了 `load_single`，所以能看到"落盘成功"这一层；真实链路里 B/D 会在 `_after_install_from_zip` 抛
> `安装后加载失败`，**但坏目录已经留在 `data/module/plugins/demo/`**，下次再装会命中"已安装"（`registry.py:444-446`），
> 必须手工删目录才能重试。这就是"zip 装了不生效/像没解压"的完整因果。

其它未处理项：无 `requirements.txt` 安装/提示；无 zip 体积/文件数上限；无符号链接防护；覆盖安装明确不支持（只能先软卸载再装，且软卸载路径会**复用旧文件不覆盖**，`registry.py:447-449`）。

### 1.4 卸载 / 恢复 / 启停

- **软卸载** `uninstall_module()`（`registry.py:472-497`）：只写 `uninstalled_modules.json` + 卸载实例，**文件全留**。内置模块同样可被软卸载（`can_uninstall` 恒 True），即用户能把框架级 LLM 模块从 UI 上"藏掉"。
- **恢复安装** `reinstall_module()`（`registry.py:499-517`）：清状态 + 重新加载；加载失败会再写回软卸载状态。
- **没有硬删除**：外部插件装坏了、不想要了，UI 无法删目录，只能手工去 `data/module/plugins/` 删。
- **启停**：`ModuleAuthority.enabled` 按 `(module, account)` 存 DB（`app/plugin/base.py:139-200`），与来源无关；禁用会联动停定时任务/后台任务（`app/webui/api/modules.py:150-224`）。
- **加载失败可观测性差**：`load_single()` 内部 `except` 只打日志返回 False（`registry.py:288-290`），加载错误的文本**不会出现在模块列表 API 里**，UI 看不到"这个插件为什么没生效"。

### 1.5 前端现状

- `app/webui/dashboard/src/views/ModulesPage.vue`
  - 有：搜索、分类分组、启停、**"安装插件"上传 zip**（151-166）、每卡菜单「重载 / 卸载」（236-238、294-297）。
  - 没有：来源徽标（内置/第三方）、版本、加载失败状态、删除、更新/覆盖安装、安装预览、URL 安装、错误详情。
- `stores/modules.ts:108-136`：`loadUninstalled` / `installZip` / `uninstall` / `install`（恢复）已就绪，接口层不用大改。
- `SettingsPage.vue:65-69`：软卸载列表在"设置"里恢复——**入口过深**，装坏的插件用户基本找不到。

### 1.6 文档与实现的不一致（这次"听说"的根源）

| 文档 | 写了什么 | 实际 |
|---|---|---|
| `docs/ARCHITECTURE.md:92` | `plugins/`：`builtin/` 内置 + `installed/` 外部 zip 安装 | 从未实现；代码是 `app/plugins` + `data/module/plugins` |
| `docs/ARCHITECTURE.md:355` | `data/module/` 存"zip 安装" | 对，但没说要解压成 `<name>/` 根、不能嵌套 |
| `docs/MODULE_DEV.md:7-30` | 只讲 `app/plugins/<name>/` 结构与 `main.py` | 未定义外部插件的 `module.json` 必填项、安装/升级/删除契约 |

### 1.7 缺陷清单（按严重度）

| 编号 | 严重度 | 缺陷 | 证据 | 触发条件 |
|---|---|---|---|---|
| P0-1 | 🔴 | zip 顶层目录容错缺失 → 装成嵌套目录，加载失败且留坏目录 | `registry.py:436-443,452-455,460-470`；实测附录 A | GitHub `repo-main`、mac `__MACOSX`、多层包装 |
| P0-2 | 🔴 | 外部模块自定义页永远 404：`module_page_path` → `_resolve_module_path` → 只查内置根 | `registry.py:155-161,207-209` | 外部插件带 `pages/index.html` |
| P0-3 | 🟠 | 同名冲突静默：内置赢，外部被忽略；配置/数据又按模块名共享 | `registry.py:169-177,302-337`；`binding_store.py:368-433` | 外部插件与内置同名 |
| P0-4 | 🟠 | 安装非事务：落盘后才校验加载，失败不回滚 | `registry.py:452-458,460-470` | 任何安装期异常 |
| P0-5 | 🟠 | 无法删除外部插件（无硬删除 API）；装坏后必须手删目录 | `modules.py:367-399` | 装坏/不想要 |
| P1-1 | 🟡 | 内置模块 10/23 缺 `module.json`，版本恒 `0.0.0`，清单不可信 | 目录实测；`loader.py:106` | 统一展示/校验 |
| P1-2 | 🟡 | GBK/无 UTF-8 标志的 zip 文件名乱码（Windows 自制 zip 常见） | `registry.py:533`（`extractall` 无 fallback） | Windows 打的中文名 zip |
| P1-3 | 🟡 | `_find_manifest_entry` 与 `_find_manifest` 判定口径不一致（任意层级 vs 一层） | `registry.py:537-542` vs `544-560` | zip 里有 docs/module.json 等 |
| P1-4 | 🟡 | 加载失败原因不进 API/UI，用户只看到"模块不见了" | `registry.py:288-290`、`bot_service.py:225-281` | 任何插件异常 |
| P1-5 | 🟡 | 无 zip 体积/文件数/符号链接防护；无依赖安装提示 | `registry.py:519-535` | 恶意或巨型 zip |
| P2-1 | 🟢 | 无覆盖安装/更新；软卸载状态下重装复用旧文件 | `registry.py:444-449` | 插件升级 |
| P2-2 | 🟢 | 内置模块也能"软卸载"，可把框架功能藏掉 | `loader.py:108` | 误操作 |
| P2-3 | 🟢 | 文档三处与实现不一致 | §1.6 | 新人/二次开发 |

---

## 2. AstrBot 参考实现（事实 + 对照）

> 事实来源：AstrBot `master` 仓库目录结构与源码文件（链接见附录 B）。本节只写能在仓库里看到的东西；
> 具体函数名以源码为准，个别流程细节标注"约定"。

### 2.1 布局：AstrBot 也不是"一个目录"

```
AstrBot/
├── astrbot/builtin_stars/        ← 内置插件（随 Python 包发布，只读语义）
│   ├── astrbot/
│   └── builtin_commands/
│       ├── main.py
│       ├── metadata.yaml         ← 唯一元数据
│       └── .astrbot-plugin/      ← 插件标记目录
└── (运行时) data/plugins/<name>/  ← 第三方插件（用户安装，写语义）
```

- 内置与第三方**物理上仍是两个位置**（包内 vs `data/`），与当前项目 `app/plugins` + `data/module/plugins` 同构。
- 差别在于：AstrBot 的内置插件集合很小（master 上 `builtin_stars` 只有 `astrbot`、`builtin_commands` 两个），
  大量能力在核心，所以"内置/第三方同构"的成本极低；本项目有 **23 个业务模块**是产品主体，不能简单照搬"全搬进 data"。

### 2.2 清单与目录约定

| 项 | AstrBot | 本项目现状 |
|---|---|---|
| 元数据 | 每个插件目录 `metadata.yaml`（name/desc/version/author/repo…），**内置与第三方同一格式** | `module.json`，但**只有 13/24 个内置模块有**，且字段没有规范 |
| 入口 | `main.py`，导出 Star 子类 | `main.py`（兼容 `module.py`），导出 `Module(BaseModule)`；等价 |
| 配置 schema | `_conf_schema.json` → dashboard 自动渲染 | `config_schema`（Python dict）→ dashboard 自动渲染；等价 |
| 依赖 | 插件目录 `requirements.txt` | 无插件级依赖机制（只有 `requires` 做框架能力自检，`app/plugin/requirements.py`） |
| 插件配置 | `data/config/<name>_config.json` | SQLite `bindings`（按账号）；更先进，无需改 |
| 插件数据 | `data/plugin_data/<name>/` | `data/module/data/<name>/`；等价 |

### 2.3 安装 / 更新 / 删除（AstrBot 的做法）

- 安装入口在 dashboard 的 plugin 路由；上传 zip 后**解压到 `data/plugins/<plugin_name>/`**，
  同时支持从插件仓库（`astrbot/core/repository.py`）按名称/URL 安装。
- 解压后的目录**刷成"插件根"**：插件根下必须直接是 `main.py` / `metadata.yaml`（不允许嵌套），
  这是"安装成功"的判据——**不是**"zip 解完了就算成功"。
- 更新：重新下载/覆盖 `data/plugins/<name>/`，配置与 `plugin_data` 不动（因为不在插件目录里）。
- 删除：删 `data/plugins/<name>/` 目录；配置与数据保留（可手动清理）。
- 加载：`astrbot/core/star/star_manager.py` 的 PluginManager 统一扫描内置 + `data/plugins`，
  以 `metadata.yaml` 为准生成 StarMetadata，失败插件记录错误而**不阻断**其它插件。

### 2.4 对照结论（可移植的部分）

| 能力 | AstrBot | 本项目 | 结论 |
|---|---|---|---|
| 内置/第三方同格式清单 | ✅ metadata.yaml | ⚠️ 一半模块没有 | **必须补齐** |
| 安装后"插件根"归一 | ✅ | ❌ 只 strip 同名单层 | **必须修** |
| 安装事务/回滚 | ✅（先落临时目录再换） | ❌ | **必须修** |
| 更新/删除 | ✅ | ❌ | **补齐** |
| 同名冲突 | 以插件名唯一 | 静默忽略 | **必须显式化** |
| 统一扫描/展示 | ✅ | 扫两个根但语义分裂 | **补齐编目层** |
| 配置/数据与源码目录分离 | ✅ | ✅（更彻底，在 DB） | 保持 |
| 物理单目录 | ❌ | ❌ | **不必强求** |

---

## 3. 目标设计

### 3.1 设计决策

| 决策 | 结论 | 理由 |
|---|---|---|
| **D1 物理布局** | **保持双根**：`app/plugins/`（内置，随包、只读语义）+ `data/module/plugins/`（第三方，可写语义）。**不做物理合并。** | ① 23 个内置模块是产品主体，必须随版本升级、被 git/CI 管理、被 `pyproject` 打包（`pyproject.toml:46-54`）；搬进 `data/` 会引入"首启拷贝/升级同步/用户改动冲突"三类新问题。② AstrBot 也是双根（§2.1）。③ 用户真正痛的是"管理语义不统一"，不是路径本身。 |
| **D2 单一编目** | 新增 `ModuleCatalog`：把两个根合成**一个逻辑列表**，每条带 `source / name / path / manifest / version / status / error / has_page / installed_at`。扫描/展示/安装/卸载/加载**一律先过编目**。 | 消除 `_iter_module_roots` 两个调用点各写一套的重复，以及"UI 只有已加载实例、看不到失败/未加载模块"的盲区。 |
| **D3 清单唯一** | `module.json` 升级为 **v2**，成为**所有**模块（含内置）的唯一元数据；内置缺的 10 个一次性补齐；加载期做校验（缺失：内置 WARN + 兜底，第三方拒绝安装）。 | 统一 `name/display_name/version`，版本可展示、可比较，为"更新"提供依据。 |
| **D4 身份与冲突** | 模块 id = 目录名 = `module.json.name`，**全局唯一**。同名：内置优先**但必须显式告警并进编目 `conflict`**；安装时与内置同名 → **默认拒绝**（第三方不可覆盖内置）；第三方之间同名 → 允许覆盖安装（先备份）。 | 配置/数据都按模块名寻址（`binding_store.py:368-433`），同名必然串数据，必须硬性隔离。 |
| **D5 状态机** | `builtin` 只允许 启用/禁用（+ 历史软卸载记录的"恢复"）；`installed` 允许 启用/禁用/更新/删除；加载失败 → `error`（附错误文本，UI 可见），**不影响其它模块**。 | 让"装坏了"可看见、可修复。 |
| **D6 事务安装** | 解压到 `data/module/.staging/<uuid>/` → 归一插件根 → 校验 →（若有旧版本：移到 `.backup/<name>-<ts>/`）→ `os.replace()` 原子换入 → 失败清理 staging、保留旧目录。 | 杜绝"坏目录已落盘 → 以后永远装不上"（P0-4）。 |
| **D7 布局容错** | 解压后**循环剥单层包装目录**，直到目录下直接出现 `module.json`/`main.py`；出现 0 个或 ≥2 个候选根 → 报错并提示（`__MACOSX/`、`docs/` 之类视为噪声丢弃）。 | 修 P0-1；GitHub/mac zip 一次装好。 |
| **D8 安全与体验** | 拒绝绝对路径/`..`/符号链接；限制解压后总大小（如 200 MB）与文件数（如 5000）；GBK 文件名回退解码；`requirements.txt` 只提示不自动装（避免供应链风险，后续可加开关）；安装前可"预检"（dry-run）返回将落到的目录/名字/版本/冲突。 | 修 P1-2/P1-5，把错误提前暴露。 |

### 3.2 目标目录树

```
QQbotNext6/
├── app/plugins/<name>/                 # 内置模块（原样保留；补全 module.json）
│   ├── module.json                     # v2 清单（新增：author/requires/entry/api_version）
│   ├── main.py
│   └── pages/index.html
├── data/module/
│   ├── plugins/<name>/                 # 第三方插件（插件根直接含 main.py + module.json）
│   ├── .staging/<uuid>/                # 安装中（结束后必清理；git 忽略）
│   ├── .backup/<name>-<ts>/            # 覆盖安装前备份（保留最近 N 份）
│   ├── data/<name>/                    # 模块运行时数据（现状不变）
│   └── uninstalled_modules.json        # 软卸载 tombstone（现状不变）
└── docs/module-management-redesign.md  # 本文
```

### 3.3 `module.json` v2 规范

```jsonc
{
  "name": "bilibili_parser",        // 必填：全局唯一 id，须 == 目录名
  "display_name": "B站视频解析",      // 必填：UI 标题
  "version": "1.1.0",               // 必填：semver（用于更新比较/展示）
  "description": "……",              // 可选
  "author": "tendokei521",          // 可选（第三方建议填）
  "entry": "main.py",               // 可选，缺省 main.py
  "api_version": 1,                 // 必填：框架插件 API 版本，不兼容则拒绝加载
  "requires": ["llm"],              // 可选：框架能力自检（复用 app/plugin/requirements.py）
  "requirements": "requirements.txt"// 可选：第三方依赖清单（只提示）
}
```

加载期规则：

- 内置模块缺清单 → `WARNING` + 用类属性兜底（`version="0.0.0"`），并在编目标 `manifest_missing`；
- 第三方模块缺清单/缺 `name`/`name != 目录名`/`api_version` 不兼容 → **拒绝加载**，编目 `error`；
- 清单里的 `display_name` 优先于类属性 `name` 用于 UI（类属性保留给代码内引用）。

### 3.4 状态机

```
                       ┌─────────────── builtin ───────────────┐
未安装 ──install──> installed ──disable──> disabled ──enable──> installed
                       │  │                                        │
                       │  └──load 失败──> error ──修复/更新──> installed
                       │
                       ├──软卸载(兼容旧记录)──> tombstone(隐藏，文件保留) ──恢复──> installed
                       └──delete(仅第三方)──> 未安装（配置/数据默认保留，可选清理）
```

### 3.5 统一编目数据结构

```python
# app/plugin/catalog.py（新增）
@dataclass(frozen=True)
class ModuleEntry:
    name: str
    source: Literal["builtin", "installed"]
    path: Path                 # 插件根目录
    manifest: dict
    version: str
    display_name: str
    status: Literal["ok", "error", "uninstalled", "conflict", "manifest_missing"]
    error: str                 # status=error 时的用户可读原因
    has_page: bool
    builtin: bool
    installed_at: float | None

class ModuleCatalog:
    def scan(self) -> list[ModuleEntry]: ...
    def get(self, name: str) -> ModuleEntry | None: ...
    def resolve_path(self, name: str, *, source: str | None = None) -> Path | None: ...  # 修 P0-2
    def conflicts(self) -> list[str]: ...
    def refresh(self) -> None: ...
```

`ModuleRegistry` 保留现有对外方法（`load_single/unload/reload*`），内部改为：

- `discover()`/`load_all()` → 消费 `catalog.scan()`，不再自己 `iterdir`；
- `_module_source()`/`_resolve_module_dir()` → 委托 `catalog.resolve_path()`；
- `module_page_path()` → `catalog.resolve_path(name)/pages/index.html`（修 P0-2）；
- 新增 `self._load_errors: dict[str, str]`，`load_single()` 失败时写入，编目读取 → API 暴露（修 P1-4）。

### 3.6 安装管道（新模块 `app/plugin/installer.py`）

```python
async def install_zip(zip_path, *, target_root, force=False, dry_run=False) -> InstallResult:
    1. 打开 zip：遍历条目做安全检查（绝对路径 / '..' / 符号链接 / 体积 / 文件数）
    2. 解压到 .staging/<uuid>/（自定义提取器：UTF-8 标志缺失时 cp437→gbk/gb18030 回退）
    3. normalize_plugin_root(staging)：
         · 丢弃噪声目录（__MACOSX, .git, .idea, __pycache__）
         · while 只有一个子目录 and 子目录下无 main.py/module.json: 下钻
         · 要求最终根直接含 module.json（且 entry 文件存在）
    4. 读清单校验（name == 目录名允许重命名、api_version、必填字段）
    5. 冲突检查：与内置同名 → 拒绝；与已装第三方同名 → 需 force
    6. dry_run: 返回 {name, version, target, conflict, files} 给前端预览
    7. 旧版本存在 → 移到 .backup/<name>-<ts>/（保留最近 3 份，超出清理）
    8. os.replace(staging_root, target) 原子换入；清理 staging
    9. 返回 InstallResult（含落盘路径 / 是否覆盖 / 备份路径）
```

任何一步失败：清理 `.staging`、恢复 `.backup`（如已移动）、**原目录保持可用**。

---

## 4. 分步修改方案（按提交）

> 每个提交独立可回滚；顺序即依赖顺序。P0 两个提交完成后，**"上传 zip 装不上/装坏"的核心症状即消失**。

### C1 `fix(plugin): resolve module paths per source and serve installed pages`

改动：

| 文件 | 改动 |
|---|---|
| `app/plugin/catalog.py`（新建） | 先只放 `ModuleEntry` + `ModuleCatalog.scan()` + `resolve_path()`（不接线其它模块） |
| `app/plugin/registry.py` | `_module_source()` / `_resolve_module_dir()` / `_resolve_module_path()` / `module_page_path()` 改为走 catalog；保留旧签名（`source=None` 时按"实际存在优先，内置优先"解析） |
| `tests/plugin/test_plugin_page.py` | 新增用例：外部目录模块带 `pages/index.html` → `module_has_page() == True`、`GET /api/module/<name>/page` 200 |
| `tests/plugin/test_module_catalog.py`（新建） | 扫描两根、同名冲突、缺失清单三种用例 |

验收：`pytest tests/plugin -q` 全绿；P0-2 修复。

### C2 `feat(plugin): transactional, layout-tolerant zip installer`

改动：

| 文件 | 改动 |
|---|---|
| `app/plugin/installer.py`（新建） | §3.6 全部逻辑；`InstallResult` dataclass；`normalize_plugin_root()` 单独可测 |
| `app/plugin/registry.py` | `install_from_zip()` 变成薄包装：`installer.install_zip(...)` → `load_single()`；失败不再留坏目录；新增 `update_from_zip()`、`delete_module()`（仅 `source=installed`） |
| `app/webui/api/modules.py` | `/modules/install-zip` 增加 `force: bool = Form(False)`；新增 `POST /modules/validate-zip`（dry-run，返回预览）；新增 `DELETE /modules/{name}`；`ValueError` 统一映射 400 并带结构化 detail |
| `tests/plugin/test_module_install.py` | 覆盖 A/B/C/D 四种布局 + `__MACOSX` + 路径穿越 + 符号链接 + GBK 文件名 + 覆盖安装备份 + 失败回滚（原目录仍可加载） |

验收：`pytest tests/plugin -q`；手工：拿 GitHub 仓库 zip 直传，装完 `data/module/plugins/<name>/main.py` 存在、模块出现在列表。

### C3 `feat(plugin): unify module catalog and surface load errors`

改动：

| 文件 | 改动 |
|---|---|
| `app/plugin/registry.py` | `discover()/load_all()` 改为消费 `catalog.scan()`；`_load_errors` 收集；同名冲突写 WARNING 并进编目 |
| `app/application/bot_service.py` | `get_modules_data()` 合并"已加载实例 + 编目条目"：新增 `status/error/path/builtin/manifest_missing`；未加载/加载失败的模块也出现在列表（虚拟 agent 逻辑不变） |
| `app/webui/api/modules.py` | `GET /modules` 透出新字段；`GET /modules/uninstalled` 保持兼容 |
| `tests/plugin/test_module_catalog.py` | 加载失败模块出现在 API 且带 error 文本 |

验收：故意写坏一个插件（`main.py` 抛异常），启动后列表仍显示它 + 错误原因，其它模块不受影响。

### C4 `feat(plugin): module.json v2 for every module (builtin backfill + validation)`

改动：

| 文件 | 改动 |
|---|---|
| `app/plugins/*/module.json` | 补齐 10 个缺失清单（`scripts/gen_module_manifests.py` 生成初稿，人工校对 display_name/version） |
| `app/plugin/manifest.py`（新建） | `load_manifest(path) -> Manifest`、`validate(manifest, *, source) -> list[str]`；`loader.py:106` 改用 |
| `app/plugin/loader.py` | 版本/显示名来自 manifest；第三方缺清单直接失败；内置缺清单 WARN + 兜底 |
| `docs/MODULE_DEV.md` §1 | 补 `module.json` v2 字段表与"外部插件必须含清单" |
| `tests/plugin/test_manifest.py`（新建） | 字段校验、api_version 不兼容、name != 目录名 |

验收：所有 23 个内置模块在 UI 有正确版本号；第三方缺清单被拒且错误可读。

> 说明：C4 不阻塞 C1-C3；如果想尽快发补丁，C1+C2 可以单独发一版。

### C5 `feat(webui): module management API closure`

- `POST /api/modules/{name}/update`（multipart zip）：等价 `install_zip(force=True)`，保留配置/数据；
- `DELETE /api/modules/{name}`：仅 `source=installed`；`?purge_data=true` 时同时删 `data/module/data/<name>`（默认 false）；
- `POST /api/modules/uninstall` 与 `/install` 语义收敛：本项目的"软卸载"保留（兼容），UI 文案改为「禁用并隐藏」；
- 所有写接口返回统一 `{status, message, plugin:{...}}`，错误带 `hint` 字段。

### C6 `feat(dashboard): module cards, install dialog and restore entry`

- `ModulesPage.vue`
  - 卡片：来源徽标（内置/第三方）、`v{version}`、`status=error` 红色标记 + 悬浮错误文本；
  - 菜单按来源渲染：内置 = 重载 / 禁用并隐藏；第三方 = 重载 / 更新… / 删除…；
  - 「安装插件」→ 对话框：拖拽 + 选择文件 → 先调 `validate-zip` 展示"名称/版本/目标目录/是否覆盖" → 确认再装；失败展示 `hint`；
  - 页面顶部加「已卸载/装坏」提示条，链接到恢复区；
- `SettingsPage.vue`：恢复区保留；删除确认文案说明"配置与数据默认保留"；
- `stores/modules.ts`：新增 `validateZip/updateZip/deleteModule`；
- 前端构建：`npm run build`（产物入 `app/webui/dashboard/dist`，按仓库习惯提交）。

### C7 `docs(plugin): align architecture docs + legacy install repair script`

- 更新 `docs/ARCHITECTURE.md:92,355`（删掉从未实现的 `builtin/ + installed/` 描述，改为双根 + 统一编目）；
- 新增 `scripts/fix_module_layout.py`：扫描 `data/module/plugins/*/`，对"根下只有一个子目录且子目录含 `main.py`"的旧坏装**自动上提一层**（幂等、先备份、打印清单）；
  同一逻辑可在启动时以 WARN 提示（不自动改，避免启动期写盘）。

---

## 5. 兼容与迁移

| 现状 | 新方案 | 迁移动作 |
|---|---|---|
| `data/module/uninstalled_modules.json` | 保留字段，新增可选 `reason` | 无 |
| 外部插件旧坏装（嵌套目录） | C7 提供修复脚本；C3 编目对嵌套目录标 `error` 并给 hint | 提示用户跑一次脚本 |
| API `/modules/install-zip` | 语义不变，新增可选 `force`；返回体新增 `plugin.path` | 兼容 |
| API `/modules/uninstalled`、`/modules/{name}/install` | 保留 | 兼容 |
| `MODULE_DIR` / `PLUGINS_DIR` 环境变量 | 不变 | 无 |
| 内置模块无 `module.json` | C4 补齐；加载期兜底 | 自动 |
| 同名冲突（历史遗留） | 编目 `conflict` + WARNING；不自动删任何文件 | 人工处理（脚本可列清单） |

---

## 6. 测试计划

**单元/集成（pytest）**

| 文件 | 覆盖 |
|---|---|
| `tests/plugin/test_module_install.py` | 4 种 zip 布局、`__MACOSX` 噪声、覆盖安装备份、失败回滚、删除、GBK 名、路径穿越/符号链接拒绝 |
| `tests/plugin/test_module_catalog.py` | 双根扫描、同名冲突、缺失清单、加载失败状态、`resolve_path` 来源优先级 |
| `tests/plugin/test_plugin_page.py` | 内置 + 外部模块的 `pages` 均可达 |
| `tests/plugin/test_manifest.py` | v2 字段校验、`api_version` 不兼容、name≠目录名 |
| `tests/core/test_settings_paths.py` | 新增 `.staging/.backup` 路径断言 |

**必须保持不改红的既有测试**：`tests/plugin/test_module_install.py`、`tests/plugin/test_plugin_page.py`、`tests/plugins/test_astrbot_alignment_plugin.py`、`tests/plugins/test_douyin_service.py`（后两者手工构造 `ModuleRegistry(plugins_dir=..., zip_plugins_dir=...)`，C1 若改构造签名需保持向后兼容）。

**手工验收清单**

1. GitHub 仓库下载 zip → 上传安装 → 模块出现、版本正确、入口在 `<name>/main.py`；
2. 带 `pages/index.html` 的第三方插件 → 模块配置页正常渲染；
3. 覆盖安装新版本 → 配置/权限/数据不丢，旧版本在 `.backup`；
4. 故意上传坏 zip（无 `module.json` / 入口缺失 / 与内置同名）→ 明确报错、目标目录无残留；
5. 删除第三方插件 → 目录消失、配置默认保留、UI 立即更新；
6. 内置模块 → 无删除入口；禁用后定时任务确实停止；
7. 旧坏装目录（嵌套一层）→ 跑修复脚本后模块可加载。

**验证档位（按 AGENTS.md）**：C1-C4 属"局部逻辑改动"，跑相关测试文件；C5/C6 收尾 + C7 发版前跑全量 `pytest -q` + `ruff check .`（无新增）。

---

## 7. 风险与回滚

| 风险 | 影响 | 对策 |
|---|---|---|
| C1 改路径解析后，历史"同名外部插件被忽略"的部署突然出现冲突 | 中 | 冲突只告警、不改变加载结果（仍内置优先），UI 明示；脚本列出清单 |
| C2 事务安装改变现有软卸载-重装语义 | 中 | 保留 `uninstalled_modules.json` 路径；恢复安装仍走 `reinstall_module()` |
| C3 让"未加载模块"进入列表，前端假设所有条目都有实例 | 中 | `get_modules_data` 先合并再统一填充默认字段；前端用可选链，`status/error` 缺省 `ok` |
| C4 补清单改到业务模块元数据 | 低 | 只加文件，不改代码逻辑；版本号人工核对 |
| 磁盘：`.backup` 无限增长 | 低 | 只保留最近 3 份 + 总大小上限；`.gitignore` 已覆盖 `data/` |
| 第三方插件任意代码执行（本质风险） | 高 | 安装接口需 WebUI 鉴权（沿用现有中间件）；README 明示"只装可信来源"；不自动 pip 安装 |

回滚：每个提交独立；`app/plugin/catalog.py`、`installer.py`、`manifest.py` 都是新增文件，回滚只需恢复 `registry.py`/`loader.py`/API 三处。

---

## 附录 A：zip 布局实测（本机真实运行）

探针脚本直接调用 `ModuleRegistry.install_from_zip()`（把 `load_single` 打桩为成功，以单独观察"解压 + 顶层处理"这一段），
`zip_plugins_dir` 指向临时目录。输出：

```
===== A_标准_顶层同名目录 =====
   OK  -> {'module_name': 'demo', 'display_name': 'demo', 'version': '1.0.0', 'source': 'zip'}
   plugin-root 文件树: ['demo', 'demo/__init__.py', 'demo/main.py', 'demo/module.json']
   入口在 demo/main.py ? True

===== B_GitHub式_顶层repo-main =====
   OK  -> {... 'module_name': 'demo' ...}
   plugin-root 文件树: ['demo', 'demo/repo-main', 'demo/repo-main/__init__.py',
                        'demo/repo-main/main.py', 'demo/repo-main/module.json']
   入口在 demo/main.py ? False        ← 嵌套，真实链路会"安装后加载失败"且留下坏目录

===== C_无顶层目录 =====
   OK  -> ...
   入口在 demo/main.py ? True

===== D_两个顶层目录 =====
   OK  -> ...
   plugin-root 文件树: ['demo', 'demo/demo', 'demo/demo/main.py', 'demo/demo/module.json',
                        'demo/docs', 'demo/docs/readme.md']
   入口在 demo/main.py ? False        ← 同上；mac 的 __MACOSX/ 也会落到这一类
```

基线测试：`venv\Scripts\python.exe -m pytest tests/plugin/test_module_install.py -q` → `3 passed`（说明现有用例只覆盖布局 A）。

## 附录 B：参考链接

- AstrBot 仓库：<https://github.com/AstrBotDevs/AstrBot>
- 内置插件目录（`metadata.yaml` / `main.py` / `.astrbot-plugin`）：<https://github.com/AstrBotDevs/AstrBot/tree/master/astrbot/builtin_stars>
- 插件框架核心（PluginManager / Star / 注册）：<https://github.com/AstrBotDevs/AstrBot/tree/master/astrbot/core/star>
- 插件管理器：<https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/star/star_manager.py>
- 插件更新器：<https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/star/updater.py>
- 插件仓库索引客户端：<https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/repository.py>
- dashboard 后端（插件路由）：<https://github.com/AstrBotDevs/AstrBot/tree/master/astrbot/dashboard>
