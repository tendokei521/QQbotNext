# AstrBot 插件本地数据管理实现调研（移植参考）

> 调研对象：GitHub [`AstrBotDevs/AstrBot`](https://github.com/AstrBotDevs/AstrBot) 默认分支 `master`
> 调研时间：2026-10-26
> 方法：直接读取仓库源码（GitHub API / raw 文件）+ 官方文档站点，未依赖二手博客。
> 标注约定：**【事实】** = 源码或文档原文可确认；**【推断】** = 由代码路径推导、未逐行验证。

---

## 0. 结论速览

| 问题 | 结论（一句话） |
|---|---|
| 目录布局 | 第三方插件在 `<root>/data/plugins/<dir>/`；内置插件在**源码树** `<repo>/astrbot/builtin_stars/<dir>/`。两者**不是**同一目录树，但**由同一个 `PluginManager` 统一扫描**。`astrbot/builtin_plugins/` 与 `packages/` 在 master 上**不存在**。 |
| 安装流程 | 上传 zip → 存 `data/temp/plugin_upload_*.zip` → 系统临时目录 `<tmp>/.astrbot/.plugin-upload-*/plugin/` 解压（**剥离唯一顶层目录**）→ 校验 `metadata.yaml` 必填字段 → 目标目录名 = metadata `name` → 移动到 `data/plugins/<name>/` → 装 `requirements.txt` → `load(specified_dir_name=...)` |
| 唯一标识 | 落地目录名与配置文件名用 `metadata.name`（必须是合法 Python 标识符）；`StarMetadata.plugin_id = "author/name"` 仅用于 KV 存储等；Dashboard 的 `plugin_id` 请求参数实际就是 `name`。 |
| 启用/禁用 | 状态不落在插件目录，而是 `data/shared_preferences.json` 的 `inactivated_plugins: list[module_path]`。 |
| 配置 | `data/config/<name>_config.json`（schema 来自插件目录内 `_conf_schema.json`）。 |
| 数据 | `data/plugin_data/<metadata.name>/`，官方入口 `StarTools.get_data_dir()`。 |
| 加载 | 扫描两目录 → 拼模块路径 `data.plugins.<dir>.main` / `astrbot.builtin_stars.<dir>.main` → 直接 `__import__`（**不改 sys.path**，靠 `main.py` 启动时把项目根塞进 `sys.path`）→ 单插件失败仅记录到 `failed_plugin_dict` 并继续。 |
| 卸载 | 硬删源码目录；配置/数据/ KV **默认保留**，只有显式 `delete_config` / `delete_data` 才清。内置插件拒绝卸载与更新。 |

---

## 1. 目录布局

### 1.1 路径函数（全部来自 `astrbot/core/utils/astrbot_path.py`）【事实】

```python
# astrbot/core/utils/astrbot_path.py
def get_astrbot_path() -> str:
    """AstrBot 源码树根目录（由本文件位置反推 ../../../）"""
    return os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../"))

def get_astrbot_root() -> str:
    """运行根：ASTRBOT_ROOT 环境变量 > 桌面打包版 ~/.astrbot > 当前工作目录"""
    ...

def get_astrbot_data_path() -> str:            # <root>/data
def get_astrbot_config_path() -> str:          # <root>/data/config
def get_astrbot_plugin_path() -> str:          # <root>/data/plugins        ← 第三方插件
def get_astrbot_builtin_plugin_path() -> str:  # <源码树>/astrbot/builtin_stars ← 内置插件
def get_astrbot_plugin_data_path() -> str:     # <root>/data/plugin_data
def get_astrbot_temp_path() -> str:            # <root>/data/temp
def get_astrbot_site_packages_path() -> str:   # <root>/data/site-packages
def get_astrbot_system_tmp_path() -> str:      # <系统临时目录>/.astrbot（安装暂存用）
def get_astrbot_backups_path() -> str:         # <root>/data/backups
```

> 关键设计：**代码根（source root）与数据根（root）分离**。数据根可用 `ASTRBOT_ROOT` 重定向，而内置插件路径永远指向源码树。桌面打包版数据根固定 `~/.astrbot`。

### 1.2 `PluginManager` 持有的三个路径【事实】

```python
# astrbot/core/star/star_manager.py :: class PluginManager
self.plugin_store_path   = get_astrbot_plugin_path()      # data/plugins
self.plugin_config_path  = get_astrbot_config_path()      # data/config
self.reserved_plugin_path = os.path.join(
    get_astrbot_path(), "astrbot", "builtin_stars"
)                                                         # <源码树>/astrbot/builtin_stars
self.conf_schema_fname = "_conf_schema.json"
self.logo_fname = "logo.png"
```

### 1.3 目录树（照抄用）

```text
<ASTRBOT_ROOT>/                          # 默认 = 启动时 cwd；可用 ASTRBOT_ROOT 覆盖
├── main.py                              # 入口：把 <root> 追加进 sys.path，makedirs 各目录
├── astrbot/                             # 源码树（get_astrbot_path()）
│   └── builtin_stars/                   # ★ 内置插件，无 __init__.py（命名空间包）
│       ├── astrbot/                     # 目录名 == metadata.name
│       │   ├── metadata.yaml
│       │   ├── main.py
│       │   ├── .astrbot-plugin/i18n/*.json
│       │   └── skills/                  # 可选：随插件分发的 Skills
│       └── builtin_commands/
└── data/                                # 数据根
    ├── plugins/                         # ★ 第三方插件（运行时 makedirs 创建，无 __init__.py）
    │   └── astrbot_plugin_xxx/          # 目录名 == metadata.name（安装时由 zip 内 name 决定）
    │       ├── metadata.yaml            # 必需：name/desc/version/author
    │       ├── main.py                  # 必需（或 <dir>/<dir>.py）
    │       ├── requirements.txt         # 可选
    │       ├── _conf_schema.json        # 可选：有它才会生成 config json
    │       ├── logo.png                 # 可选
    │       ├── README.md
    │       └── .astrbot-plugin/i18n/*.json
    ├── config/
    │   └── astrbot_plugin_xxx_config.json   # ★ 插件配置（键 = 目录名）
    ├── plugin_data/
    │   └── astrbot_plugin_xxx/          # ★ 插件私有数据（键 = metadata.name）
    ├── plugins_data/                    # 历史遗留目录名，卸载时一并清理（兼容）
    ├── temp/plugin_upload_*.zip         # 上传落盘位置
    ├── site-packages/                   # 插件 pip 安装目标
    ├── backups/
    └── shared_preferences.json          # inactivated_plugins / inactivated_llm_tools / alter_cmd
```

**关于 `astrbot/builtin_plugins/` 与 `packages/`**：在 `master` 分支的 `astrbot/` 目录下只有 `api/ builtin_stars/ cli/ core/ dashboard/ utils/`，仓库根目录也没有 `packages/`。题干中的这两个路径**在当前版本不存在**；内置插件目录名是 `builtin_stars`（术语 "Star" = 插件）。{**事实**（依据 `contents/astrbot` 列表）} 更早版本曾用 `packages/` 组织，但本次未取证，不做断言。{**推断**}

---

## 2. 安装流程

### 2.1 上传入口链路【事实】

```
POST /api/plugin/install-upload            (旧版兼容路由，dashboard)
POST /plugins/install/upload               (新版 REST)
  → astrbot/dashboard/api/plugins.py :: install_plugin_from_upload
  → _install_plugin_upload() → multipart_parts(request) 取 files["file"]
  → astrbot/dashboard/services/plugin_service.py :: PluginService.install_plugin_upload
  → PluginManager.install_plugin_from_file(file_path, ignore_version_check)
```

`PluginService.install_plugin_upload` 原文（落盘位置）：

```python
filename = str(upload_file.filename or "plugin.zip").replace("\\", "/")
file_path = os.path.join(
    get_astrbot_temp_path(),
    f"plugin_upload_{os.path.basename(filename) or 'plugin.zip'}",
)                       # → data/temp/plugin_upload_<basename>
await upload_file.save(file_path)
plugin_info = await self.plugin_manager.install_plugin_from_file(
    file_path, ignore_version_check=ignore_version_check
)
```

### 2.2 `install_plugin_from_file` → 解压 → 安装【事实】

```python
# astrbot/core/star/star_manager.py
async def install_plugin_from_file(self, zip_file_path, ignore_version_check=False):
    async with self._pm_lock:                                  # 全局互斥
        temp_root = Path(get_astrbot_system_tmp_path())        # <tmp>/.astrbot
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root, prefix=".plugin-upload-") as staging_dir:
            plugin_path = Path(staging_dir) / "plugin"
            self._updater._extract_plugin_archive(zip_file_path, str(plugin_path))
            plugin_info = await self._install_plugin_from_directory(
                str(plugin_path), ignore_version_check=ignore_version_check
            )
        Path(zip_file_path).unlink(missing_ok=True)            # 删掉 data/temp 里的 zip
        return plugin_info
```

要点：
- 解压目标**不是** `data/plugins`，而是系统临时目录下的 staging（`.plugin-upload-xxxx/plugin`）。
- 安装/更新全过程持 `asyncio.Lock`（`_pm_lock`），串行化。
- 上传的 zip 在安装结束后删除（即使失败也走 `unlink(missing_ok=True)`）。

### 2.3 解压时是否剥离顶层目录：**是，剥离唯一顶层目录**【事实】

核心在 `astrbot/core/zip_updater.py`：

```python
class _RepoZipUpdater:
    @staticmethod
    def _resolve_archive_root_dir(entries: list[str]) -> str:
        """用所有条目求公共根前缀；单文件条目取父目录"""
        ...  # 返回 "" 表示没有统一顶层目录（散装 zip）

    def _finalize_extracted_archive(self, zip_path, target_dir, update_dir) -> None:
        """把解压出来的唯一顶层目录里的内容，逐个 move 到 target_dir"""
        update_root = _join_under_root(target_root, update_dir)   # 防目录穿越
        for f in os.listdir(update_root):
            ... shutil.move(update_item_path, target_root)
        shutil.rmtree(update_root); os.remove(zip_path)
```

`_PluginUpdater._extract_plugin_archive`（`astrbot/core/star/updater.py`）在此之上加了两件事：

```python
def _extract_plugin_archive(self, zip_path: str, target_dir: str) -> None:
    self.validate_plugin_archive(zip_path)            # ① 先校验 metadata 存在
    ensure_dir(target_dir)
    with zipfile.ZipFile(zip_path, "r") as z:
        update_dir = self._resolve_archive_root_dir(z.namelist())
        root_parts = update_dir.replace("\\", "/").split("/")
        archive_root = root_parts[0]
        match = re.fullmatch(r"(.+)-([0-9a-fA-F]{40})", archive_root)
        if match and all(name == archive_root or name.startswith(archive_root + "/")
                         for name in z.namelist()):
            # ② GitHub zipball 顶层目录形如 `repo-<40位sha>`，
            #    先缩短成 repo-<sha前8位> 再解压（规避 Windows 长路径）
            short_root = f"{match[1]}-{match[2][:8]}"
            for member in z.infolist():
                member = copy(member)
                member.filename = short_root + member.filename[len(archive_root):]
                z.extract(member, target_dir)
            update_dir = str(Path(short_root).joinpath(*root_parts[1:]))
        else:
            z.extractall(target_dir)
    self._finalize_extracted_archive(zip_path, target_dir, update_dir)
```

结论（可直接照抄的行为）：
- `repo-main/`、`astrbot_plugin_xxx-main/`、`xxx-v1.2.3/` 这类**单顶层目录**会被压平，内容直接落到 `plugin/`。
- 顶层目录是 GitHub zipball 的 `name-<sha40>` 时会额外改短，这是为了 Windows 260 字符路径限制，移植到 Linux 可省。
- 若 zip 内**没有**统一顶层目录（文件直接散在根），`_resolve_archive_root_dir` 返回 `""`，则退化为「原地使用」，不压平。
- 所有 move 都过 `_join_under_root()`，用 `os.path.commonpath` 校验不逃出目标根 —— **Zip Slip 防护**。

### 2.4 校验规则清单【事实】

| 校验项 | 位置 | 规则 / 报错 |
|---|---|---|
| 元数据文件存在 | `_RepoZipUpdater.find_plugin_metadata_entry` / `inspect_plugin_archive` | `metadata.yaml` 或 `metadata.yml`，位于 zip 根或**单层子目录**内；否则 `"压缩包不是合规的 AstrBot 插件：未找到 metadata.yaml 或 metadata.yml。"` |
| 大小上限 | `PLUGIN_METADATA_MAX_BYTES = 1024 * 1024` | metadata > 1MB 直接拒绝 |
| 编码 | `yaml.safe_load` 前 `decode("utf-8")` | 非 UTF-8 → `"... 必须使用 UTF-8 编码。"` |
| YAML 合法性 | `yaml.safe_load` | `YAMLError` → `"{file} 格式错误。"` |
| 必填字段 | `PLUGIN_METADATA_REQUIRED_FIELDS = ("name", "desc", "version", "author")` | 缺失 → `"{file} 中缺少必需字段：..."`；非字符串/空白 → `"必须是非空字符串。"` |
| `desc` 别名 | `validate_plugin_metadata` | 只有 `description` 时自动映射为 `desc` |
| 目录名合法性 | `PluginManager._validate_importable_name` | 含 `/` `\` → `"metadata 文件中 name 含有路径分隔符..."`；非标识符或是关键字 → `"name 不是合法的模块名称（应为合法 Python 标识符且非关键字）。"` |
| AstrBot 版本区间 | `_validate_astrbot_version_specifier`（`packaging.specifiers.SpecifierSet` vs `VERSION`） | 非法 spec → `"Invalid astrbot_version. Use a PEP 440 range, e.g. >=4.16,<5."`；不满足 → 抛 `PluginVersionUnsupportedError`，Dashboard 可用 `ignore_version_check=true` 绕过（前端"忽略警告继续安装"） |
| `_conf_schema.json` | `_load_plugin_config_schema` | `utf-8-sig` 读取；解码失败/JSON 错误 → `"插件配置 schema 必须使用 UTF-8 编码"` / `"不是有效的 JSON"` |
| `requirements.txt` | `_ensure_plugin_requirements` + `plan_missing_requirements_install` | 存在才装；先做缺失预检，能裁剪就只装缺的，冲突抛 `DependencyConflictError`，其他抛 `PluginDependencyInstallError` |
| `main.py` | **不在安装阶段校验** | 由加载阶段 `_get_modules` 决定（无 `main.py` 且无同名 py 则跳过） |

> ⚠️ **【推断】一个真实歧义点**：安装完成后 `_install_plugin_from_directory` 会调 `load(specified_dir_name=dir_name)`；若目录缺 `main.py`，`_get_modules` 直接跳过该目录，`load()` 因「没有匹配到任何模块」返回 `(True, None)`，于是**安装会被判定成功、但插件根本没注册**（`plugin_info` 为 `None`，返回 `{}` + "安装成功。"）。**移植建议：自己在安装阶段补一条 `main.py` 存在性校验。**

---

## 3. 插件标识与重名处理

### 3.1 三层标识【事实】

| 标识 | 来源 | 用途 |
|---|---|---|
| `root_dir_name` | 目录名 | 定位源码目录、`<name>_config.json`、`plugin_data/<name>` 清理、失败插件记录键 |
| `name` | `metadata.yaml: name` | 安装时决定落地目录名（**两者安装后恒等**）；`get_registered_star(name)`；Dashboard 的 `plugin_id` 参数 |
| `plugin_id` | `StarMetadata.plugin_id` 属性 = `f"{author}/{name}".lower().replace("/", "_")` | 插件 KV 存储命名空间（`sp` scope=`plugin`）；卸载时清 KV |

```python
# astrbot/core/star/star.py :: class StarMetadata
@property
def plugin_id(self) -> str:
    p_name = (self.name or "unknown").lower().replace("/", "_")
    p_author = (self.author or "unknown").lower().replace("/", "_")
    return f"{p_author}/{p_name}"
```

> 注意：`plugin_id` **不是**目录名，也不是 `name`。Dashboard 里 `plugin_id` 这个 query 参数经 `find_plugin_by_name()` 解析，实际匹配的是 `name` 或 `root_dir_name`。

### 3.2 重名 / 覆盖规则【事实】

`_install_plugin_from_directory` 的判定顺序：

```python
metadata_dir_name = self._get_plugin_dir_name_from_metadata(staging_dir)   # = metadata.name
plugin = next((s for s in self.context.get_all_stars() if s.name == metadata_dir_name), None)
target = Path(self.plugin_store_path) / (plugin.root_dir_name if plugin and plugin.root_dir_name
                                         else metadata_dir_name)
if plugin and plugin.reserved:
    raise Exception("该插件是 AstrBot 保留插件，无法更新。")     # ← 内置插件不可被第三方覆盖
if target.exists():
    if (target.is_symlink()
        or not target.is_dir()
        or self._get_plugin_dir_name_from_metadata(str(target)) != metadata_dir_name):
        raise Exception(f"安装失败：目录 {target.name} 已存在。")   # ← 同名目录但不是同一插件
    # 同一插件 ⇒ 走「更新」：备份 → 终止 → 删旧 → 移动新 → load → 失败回滚
else:
    shutil.move(plugin_path, target)                            # 首次安装
```

- **内置插件优先**：第三方 zip 的 `name` 与内置同名时，`reserved=True` 命中直接拒绝。
- **目录已被占用但不是同一 identity**（例如手工塞的目录、metadata 被改过）→ 直接拒绝，不覆盖、不合并。
- **是同一 identity** → 视为升级，带完整备份/回滚（见 §6）。
- 由于 `_get_plugin_modules()` **先扫 `data/plugins` 再扫 `builtin_stars`**，【推断】若第三方与内置真的同名共存（绕过安装校验手工放入），`star_registry` 中会出现两条同名记录，`get_registered_star(name)` 取先注册的那条（第三方），存在二义性风险 —— 移植时建议对 `name` 做全局唯一性校验。

---

## 4. 状态 / 配置 / 数据

### 4.1 启用禁用：存在共享偏好里，不在插件目录【事实】

```python
# turn_off_plugin
inactivated_plugins: list = await sp.global_get("inactivated_plugins", [])
inactivated_plugins.append(plugin.module_path)      # 存的是 "data.plugins.<dir>.main"
await sp.global_put("inactivated_plugins", inactivated_plugins)
plugin.activated = False

# load() 中
metadata.activated = metadata.module_path not in inactivated_plugins
if path not in inactivated_plugins:
    metadata.star_cls = metadata.star_cls_type(context=..., config=...)   # 禁用则不实例化
else:
    metadata.star_cls = None
```

- 落盘：`data/shared_preferences.json`（源码 docstring 原文："将插件的 module_path 加入到 data/shared_preferences.json 的 inactivated_plugins 列表中"）。
- 粒度是**模块路径**而非插件名 ⇒ 内置与第三方同名也能区分。
- 另有 `inactivated_llm_tools`（按工具名禁用 LLM tool）与 `alter_cmd`（指令权限覆盖）。

### 4.2 配置【事实】

```python
plugin_schema_path = os.path.join(plugin_dir_path, "_conf_schema.json")
if os.path.exists(plugin_schema_path):
    plugin_config = AstrBotConfig(
        config_path=os.path.join(self.plugin_config_path, f"{root_dir_name}_config.json"),
        schema=self._load_plugin_config_schema(plugin_schema_path),
    )
```

- 配置文件：`data/config/<root_dir_name>_config.json`。
- **只有存在 `_conf_schema.json` 才会创建/加载配置**；无 schema 的插件拿到 `config=None`。
- 配置与源码目录**完全解耦**（升级/重装不丢）。
- 实例化时优先 `plugin_cls(context=..., config=plugin_config)`，`TypeError` 回退到 `plugin_cls(context=...)`（兼容老式无 config 构造函数）。

### 4.3 数据目录【事实】

```python
# astrbot/core/star/star_tools.py :: class StarTools
@classmethod
def get_data_dir(cls, plugin_name: str | None = None) -> Path:
    if not plugin_name:                      # 自动识别调用者插件
        module = inspect.getmodule(inspect.currentframe().f_back)
        metadata = star_map.get(module.__name__)
        plugin_name = metadata.name
    data_dir = Path(os.path.join(get_astrbot_data_path(), "plugin_data", plugin_name))
    ensure_dir(data_dir)
    return data_dir.resolve()
```

- 路径：`data/plugin_data/<metadata.name>/`，自动 `mkdir`。
- 官方文档也给出等价写法（`docs/en/dev/star/guides/storage.html`）：`Path(get_astrbot_data_path()) / "plugin_data" / self.name`。
- 轻量 KV：`PluginKVStoreMixin.put_kv_data / get_kv_data / delete_kv_data`（v4.9.2+），底层 `sp.put_async("plugin", self.plugin_id, key, value)`。
- ⚠️ **【事实 + 推断】键不一致隐患**：写入用 `metadata.name`，而卸载清理用 `root_dir_name`，并额外扫 `plugin_data/` 与 `plugins_data/` 两个目录名。安装后二者恒等，但若用户手工改过目录名/`metadata.name`，就会出现「数据清不掉」。移植时应统一成一个 key（建议就用目录名）。

---

## 5. 加载方式

### 5.1 扫描【事实】

```python
@staticmethod
def _get_modules(path):
    modules = []
    for d in os.listdir(path):
        # 暂存/回滚目录不是插件
        if d.startswith((".plugin-install-", ".plugin-upload-", ".plugin-backup-")):
            continue
        if os.path.isdir(os.path.join(path, d)):
            if os.path.exists(os.path.join(path, d, "main.py")):
                module_str = "main"
            elif os.path.exists(os.path.join(path, d, d + ".py")):
                module_str = d
            else:
                logger.info(f"Plugin {d} has neither main.py nor {d}.py; skipping it.")
                continue
            modules.append({"pname": d, "module": module_str,
                            "module_path": os.path.join(path, d, module_str)})
    return modules

def _get_plugin_modules(self):
    plugins = []
    if os.path.exists(self.plugin_store_path):
        plugins.extend(self._get_modules(self.plugin_store_path))
    if os.path.exists(self.reserved_plugin_path):
        _p = self._get_modules(self.reserved_plugin_path)
        for p in _p:
            p["reserved"] = True
        plugins.extend(_p)
    return plugins          # ← 统一列表，一行一个插件 dict
```

- **统一扫描**：两个目录拼成同一个列表，仅用 `reserved` 标记区分。差异只体现在 `requirements` 安装策略、`initialize` 依赖安装回滚、以及禁止卸载/更新。
- 入口文件名只有两种：`main.py`（首选）或 `<目录名>.py`。
- 无 `__init__.py` 要求 —— 目录即命名空间包。

### 5.2 import 方式：拼模块路径 + `__import__`，**不动 sys.path**【事实】

```python
path = "data.plugins." if not reserved else "astrbot.builtin_stars."
path += root_dir_name + "." + module_str          # 例：data.plugins.foo.main
module = await self._import_plugin_with_dependency_recovery(path=path, module_str=module_str, ...)
# 内部核心就一句：
module = __import__(path, fromlist=[module_str])
```

为什么能 import 成功（**没有任何 `sys.path.insert(0, data/plugins)`**）：`main.py` 启动时已经做了

```python
# main.py
sys.path.append(Path(__file__).parent.as_posix())      # 项目根
# check_env():
astrbot_root = get_astrbot_root()
if astrbot_root not in sys.path:
    sys.path.insert(0, astrbot_root)
site_packages_path = get_astrbot_site_packages_path()
sys.path.append(site_packages_path)                    # data/site-packages（插件 pip 目标）
os.makedirs(get_astrbot_config_path(), exist_ok=True)
os.makedirs(get_astrbot_plugin_path(), exist_ok=True)
os.makedirs(get_astrbot_temp_path(), exist_ok=True)
os.makedirs(get_astrbot_knowledge_base_path(), exist_ok=True)
os.makedirs(site_packages_path, exist_ok=True)
```

于是 `data/` 与 `data/plugins/`（运行时 `makedirs` 创建的普通目录，**无 `__init__.py`**）以 **PEP 420 隐式命名空间包**身份被解析，`data.plugins.<dir>.main` 自然可 import。`astrbot/builtin_stars/` 同样无 `__init__.py`（已核实目录列表只有 `astrbot/`、`builtin_commands/` 两个子目录），在常规包 `astrbot` 下作为命名空间分支工作。

> 移植到 Plan B 时这是最需要注意的一条：**要么保证进程 cwd/根在 `sys.path` 且 plugins 目录无 `__init__.py`（命名空间包），要么用 `importlib.util.spec_from_file_location` 显式按文件加载**。后者更可控，推荐。

### 5.3 加载单个插件的完整步骤【事实】

`PluginManager.load(specified_module_path=None, specified_dir_name=None)`：

1. 读 `inactivated_plugins` / `inactivated_llm_tools` / `alter_cmd`。
2. 遍历 `_get_plugin_modules()`，按 `specified_*` 过滤。
3. `__import__`（带依赖恢复：`_import_plugin_with_dependency_recovery` → 失败时 `pip_installer.prefer_installed_dependencies` 重试 → 再失败 `_check_plugin_dept_update` 装 requirements 后重试）。
4. 若 `path in star_map`（插件类已通过 `Star.__init_subclass__` 自注册）→ 取该 metadata，再用 `metadata.yaml` **覆盖** name/author/desc/version/repo/display_name/support_platforms/astrbot_version/views/i18n（YAML 优先）。
5. 版本区间校验（`PluginVersionUnsupportedError`）。
6. 注入类属性 `name/author/plugin_id`，再实例化（未禁用时）。
7. 设置 `metadata.module / root_dir_name / reserved / activated / logo_path`。
8. 重新绑定 handler（`functools.partial(raw_handler, star_cls)`）与 LLM tool。
9. `await star_cls.initialize()`；触发 `OnPluginLoadedEvent` 钩子。
10. 兜底：若 `path not in star_map`，走**旧版类扫描** `_get_classes()`（类名小写以 `plugin` 结尾或等于 `main`）→ 实例化 → 读 metadata → 入注册表。

失败处理（两层 `except`）：

```python
except Exception as e:                      # import 阶段失败
    has_load_error = True
    self.failed_plugin_dict[root_dir_name] = self._build_failed_plugin_record(...)
    self._cleanup_plugin_state(root_dir_name, reserved)
    continue                                # ← 不中断其它插件
except BaseException as e:                  # 其余任意阶段失败（含版本不支持）
    ... 同上 ...
```

- `_build_failed_plugin_record` 会尽力从 `metadata.yaml` 补全 name/author/version/`plugin_id`，供前端"加载失败插件"列表展示。
- `_cleanup_plugin_state` 清 `sys.modules` 前缀、`star_map`、`star_registry`、`star_handlers_registry`、`llm_tools.func_list`、平台适配器。
- 全部跑完后 `if has_load_error: return False, self.failed_plugin_info`（聚合文本），否则 `(True, None)`。
- 失败插件可单独救活：`reload_failed_plugin(dir_name)`（先 `_cleanup_plugin_state` → 装依赖 → `load(specified_dir_name=...)`）。

---

## 6. 更新 / 删除 / 卸载

### 6.1 更新（`update_plugin`）【事实】

```python
plugin = context.get_registered_star(name)
if plugin.reserved: raise Exception("该插件是 AstrBot 保留插件，无法更新。")
await self._updater.update(plugin, proxy=..., download_url=..., repo_url=...)
await self._ensure_plugin_requirements(<data/plugins>/<dir>, name)
await self.reload(name)
```

`_PluginUpdater.update()` 三种来源：
- `download_url` → 下 zip 到 `<plugin_path>.zip`；
- git 传输 → `tempfile.TemporaryDirectory(prefix=".plugin-update-", dir=plugin_store_path)` 浅克隆 → 校验 → `remove_dir(老目录)` → `shutil.move`；
- 否则按 repo 下 archive zip → `remove_dir(老目录)`（失败则打日志"使用覆盖安装"）→ `_extract_plugin_archive`。

「上传新版本 zip 覆盖」走的是 `_install_plugin_from_directory` 的同名分支，带**事务式备份回滚**：

```python
backup_dir  = Path(tempfile.mkdtemp(dir=get_astrbot_system_tmp_path(), prefix=".plugin-backup-"))
backup_path = backup_dir / dir_name
await self._terminate_plugin(plugin)         # 先停旧实例
self._cleanup_plugin_state(dir_name)         # 清注册表/sys.modules
shutil.copytree(target_plugin_path, backup_path, symlinks=True)   # 备份（跨盘安全，先复制后删）
remove_dir(target_plugin_path)
shutil.move(desti_dir, target_plugin_path)
success, err = await self.load(specified_dir_name=dir_name, ignore_version_check=...)
if not success: raise Exception(err or f"更新插件 {dir_name} 失败。")
# except BaseException：删新目录 → copytree 回滚 → load(ignore_version_check=True) → 清理 failed 记录
```

### 6.2 卸载（`uninstall_plugin`）【事实】

```python
plugin = context.get_registered_star(name)
if not plugin:              raise Exception("插件不存在。")
if plugin.reserved:         raise Exception("该插件是 AstrBot 保留插件，无法卸载。")
await self._terminate_plugin(plugin)                       # terminate() 或 __del__
await self._unbind_plugin(name, plugin.module_path)         # 摘注册表/handler/tool/adapter + 清 sys.modules
remove_dir(os.path.join(self.plugin_store_path, root_dir_name))   # 硬删源码目录
await self._cleanup_plugin_optional_artifacts(
    root_dir_name=root_dir_name, plugin_label=name, plugin_id=plugin.plugin_id,
    delete_config=delete_config, delete_data=delete_data,   # 两者默认 False
)
```

`_cleanup_plugin_optional_artifacts` 明细：

| 开关 | 动作 |
|---|---|
| `delete_config=True` | 删 `data/config/<root_dir_name>_config.json` |
| `delete_data=True` | 删 `data/plugin_data/<root_dir_name>/` **和** `data/plugins_data/<root_dir_name>/`（历史名兼容），并 `await sp.clear_async("plugin", plugin_id)` 清 KV |
| 两者皆 False（默认） | 只删源码，配置与数据**全部保留**（重装可续用） |

`_unbind_plugin` 额外做的事：`del star_map[module_path]`、从 `star_registry` 删除、`star_handlers_registry` 按模块移除、`llm_tools.func_list` 移除该插件工具（跳过 `astrbot.builtin_stars` / `data.plugins` 前缀本身）、`unregister_platform_adapters_by_module(前缀)`、最后 `_purge_modules()` 清 `sys.modules`（热重载的关键）。

加载失败插件的卸载：`uninstall_failed_plugin(dir_name, ...)`，先清状态再删目录，失败记录也一并 pop。

> ⚠️ **事实**：`uninstall_plugin` 删除目录失败时的报错文案仍写 `"位于 addons/plugins/ 下"`（旧版目录名遗留），不要被它误导为现代路径。

---

## 7. 关键源码索引（全部为 master 上真实存在）

| 文件 | 关键类 / 函数 | URL |
|---|---|---|
| `astrbot/core/star/star_manager.py` | `PluginManager`：`_get_modules` / `_get_plugin_modules` / `load` / `reload` / `reload_failed_plugin` / `install_plugin` / `install_plugin_from_file` / `_install_plugin_from_directory` / `uninstall_plugin` / `uninstall_failed_plugin` / `update_plugin` / `turn_on_plugin` / `turn_off_plugin` / `_unbind_plugin` / `_purge_modules` / `_cleanup_plugin_state` / `_cleanup_plugin_optional_artifacts` / `_load_plugin_metadata` / `_load_plugin_config_schema` / `_get_plugin_dir_name_from_metadata` / `_validate_importable_name` / `_validate_astrbot_version_specifier`；异常 `PluginVersionUnsupportedError` / `PluginDependencyInstallError` | [链接](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/star/star_manager.py) |
| `astrbot/core/star/updater.py` | `_PluginUpdater(_RepoZipUpdater)`：`install` / `update` / `inspect_repository` / `_extract_plugin_archive` / `validate_plugin_metadata` / `inspect_plugin_archive` / `validate_plugin_archive` / `find_plugin_metadata_entry` / `_clone_repository`；常量 `PLUGIN_METADATA_FILENAMES = ("metadata.yaml","metadata.yml")`、`PLUGIN_METADATA_REQUIRED_FIELDS = ("name","desc","version","author")`、`PLUGIN_METADATA_MAX_BYTES` | [链接](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/star/updater.py) |
| `astrbot/core/zip_updater.py` | `_RepoZipUpdater`：`_extract_archive` / `_resolve_archive_root_dir` / `_finalize_extracted_archive` / `_download_file` / `_download_repository` / `_format_name`（`name.replace("-","_").lower()`）；`ReleaseInfo` | [链接](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/zip_updater.py) |
| `astrbot/core/star/star.py` | `StarMetadata`（`root_dir_name` / `reserved` / `activated` / `module_path` / `config` / `plugin_id` 属性）、全局 `star_registry: list`、`star_map: dict[module_path, StarMetadata]` | [链接](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/star/star.py) |
| `astrbot/core/star/base.py` | `Star`（`__init_subclass__` 自注册、`initialize` / `terminate` / `__del__`） | [链接](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/star/base.py) |
| `astrbot/core/star/star_tools.py` | `StarTools.get_data_dir` / `send_message` / `register_llm_tool` … | [链接](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/star/star_tools.py) |
| `astrbot/core/utils/astrbot_path.py` | 全部 `get_astrbot_*_path()` | [链接](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/utils/astrbot_path.py) |
| `astrbot/core/utils/plugin_kv_store.py` | `PluginKVStoreMixin`（put/get/delete_kv_data） | [链接](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/utils/plugin_kv_store.py) |
| `astrbot/dashboard/api/plugins.py` | FastAPI 路由：`install_plugin_from_upload`、`install_plugin*`、`uninstall_plugin`、`update_plugin`、`set_plugin_enabled`、`reload_plugin`、`reload_failed_plugin`、`_install_plugin_upload`；旧版 `/api/plugin/*` 兼容路由 | [链接](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/dashboard/api/plugins.py) |
| `astrbot/dashboard/services/plugin_service.py` | `PluginService`：`install_plugin_upload` / `install_plugin` / `uninstall_plugin` / `update_plugin` / `set_plugin_enabled` / `resolve_plugin_dir` / `list_plugins`；`PluginServiceError` / `PluginServiceWarning` | [链接](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/dashboard/services/plugin_service.py) |
| `main.py` | `check_env()`（sys.path + makedirs）、`main_async()` | [链接](https://github.com/AstrBotDevs/AstrBot/blob/master/main.py) |
| `astrbot/builtin_stars/astrbot/metadata.yaml` | 内置插件元数据真实样例 | [链接](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/builtin_stars/astrbot/metadata.yaml) |

> 题干中提到的 `astrbot/core/plugin_manager.py` 与 `astrbot/dashboard/routes/plugin.py` 在 master 上**均不存在**：管理器在 `astrbot/core/star/star_manager.py`（类名 `PluginManager`），路由已重构为 FastAPI 的 `astrbot/dashboard/api/plugins.py`（旧 `routes/` 目录已无）。

### metadata.yaml 真实样例（内置插件）

```yaml
name: astrbot
desc: AstrBot's internal plugin, providing some basic capabilities.
author: AstrBot Team
version: 4.4.0
```

---

## 8. 可照抄的最小实现（Python 伪代码）

### 8.1 路径层

```python
# plugins/paths.py
import os, tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]      # 仓库根

def data_dir()   -> Path: return Path(os.environ.get("BOT_ROOT", PROJECT_ROOT)) / "data"
def plugin_dir() -> Path: return data_dir() / "plugins"          # 第三方
def config_dir() -> Path: return data_dir() / "config"
def plugin_data_dir() -> Path: return data_dir() / "plugin_data"
def temp_dir()   -> Path: return data_dir() / "temp"
def system_tmp() -> Path: return Path(tempfile.gettempdir()) / ".qqbot"
def builtin_plugin_dir() -> Path: return PROJECT_ROOT / "app" / "builtin_plugins"   # 内置

def ensure_layout() -> None:
    for p in (plugin_dir(), config_dir(), plugin_data_dir(), temp_dir(), system_tmp()):
        p.mkdir(parents=True, exist_ok=True)
    # 注意：plugins/ 与 plugin_data/ 下不要放 __init__.py，
    # 保持 PEP 420 命名空间包语义（若沿用字符串 import 方案）
```

### 8.2 扫描（两目录统一）

```python
SKIP_PREFIXES = (".plugin-install-", ".plugin-upload-", ".plugin-backup-")

def scan_plugins() -> list[dict]:
    out = []
    for base, reserved in ((plugin_dir(), False), (builtin_plugin_dir(), True)):
        if not base.is_dir():
            continue
        for d in sorted(os.listdir(base)):
            if d.startswith(SKIP_PREFIXES):
                continue
            root = base / d
            if not root.is_dir():
                continue
            if (root / "main.py").is_file():
                module_str = "main"
            elif (root / f"{d}.py").is_file():
                module_str = d
            else:
                logger.info("plugin %s 缺少 main.py / %s.py，跳过", d, d)
                continue
            out.append({"pname": d, "module": module_str,
                        "path": root, "reserved": reserved})
    return out
```

### 8.3 zip 安装：解压 + 剥离顶层目录 + Zip Slip 防护

```python
import shutil, zipfile
from pathlib import Path

def _join_under_root(root: Path, *parts: str) -> Path:
    p = (root / Path(*parts)).resolve()
    if not str(p).startswith(str(root.resolve()) + os.sep) and p != root.resolve():
        raise ValueError("path escapes root directory")
    return p

def resolve_archive_root_dir(entries: list[str]) -> str:
    """求出 zip 的唯一顶层目录；无统一顶层则返回 ''。"""
    norm = [os.path.normpath(e) for e in entries]
    port = [e.replace("\\", "/") for e in norm]
    cands: list[str] = []
    for raw, p in zip(entries, port):
        has_children = any(o != p and o.startswith(p + "/") for o in port)
        if raw.endswith(("/", "\\")) or has_children:
            cands.append(p)
            continue
        parent = p.rpartition("/")[0]
        if not parent:
            return ""                      # 有文件直接在根 ⇒ 不剥离
        cands.append(parent)
    if not cands:
        return ""
    return os.path.commonpath(cands)

def extract_plugin_zip(zip_path: Path, target: Path) -> Path:
    """校验 → 解压到 target → 若存在唯一顶层目录则压平。返回插件根目录。"""
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        # ① 先定位 metadata（根 或 单层子目录）
        top = resolve_archive_root_dir(names)
        candidates = [f"{top}/{n}" for n in ("metadata.yaml", "metadata.yml")] if top \
                     else ["metadata.yaml", "metadata.yml"]
        if not any(c in names for c in candidates):
            raise ValueError("压缩包不是合规插件：未找到 metadata.yaml / metadata.yml")
        # ② 解压（逐条 extract，天然带 Zip Slip 防护）
        z.extractall(target)
    if not top:
        return target
    update_root = _join_under_root(target, top)
    for item in update_root.iterdir():
        dest = _join_under_root(target, item.name)
        if dest.exists():
            shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
        shutil.move(str(item), str(target))
    shutil.rmtree(update_root, ignore_errors=True)
    return target
```

> `zipfile.ZipFile.extractall` 自 Python 3.6 起已做路径清洗；上面的 `_join_under_root` 是给「解压后 move」这一步用的（对齐 AstrBot 的 `_join_under_root`）。

### 8.4 元数据校验 + 目录名归一

```python
import keyword, yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version

REQUIRED = ("name", "desc", "version", "author")
METADATA_FILES = ("metadata.yaml", "metadata.yml")

def load_metadata(plugin_root: Path) -> dict:
    md_path = next((plugin_root / f for f in METADATA_FILES if (plugin_root / f).is_file()), None)
    if md_path is None:
        raise ValueError("未找到 metadata.yaml / metadata.yml")
    if md_path.stat().st_size > 1024 * 1024:
        raise ValueError(f"{md_path.name} 超过 1MB")
    meta = yaml.safe_load(md_path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict):
        raise ValueError(f"{md_path.name} 格式错误")
    if "desc" not in meta and "description" in meta:
        meta["desc"] = meta["description"]
    missing = [f for f in REQUIRED if f not in meta]
    if missing:
        raise ValueError(f"{md_path.name} 缺少必需字段：{', '.join(missing)}")
    bad = [f for f in REQUIRED if not isinstance(meta[f], str) or not meta[f].strip()]
    if bad:
        raise ValueError(f"{md_path.name} 中字段 {', '.join(bad)} 必须是非空字符串")
    return meta

def plugin_dir_name(meta: dict) -> str:
    name = meta["name"].strip()
    if "/" in name or "\\" in name:
        raise ValueError("metadata 的 name 含路径分隔符")
    if not name.isidentifier() or keyword.iskeyword(name):
        raise ValueError("metadata 的 name 不是合法 Python 标识符/模块名")
    return name

def check_version(meta: dict, current: str, ignore: bool = False) -> None:
    spec = (meta.get("astrbot_version") or "").strip()
    if not spec or ignore:
        return
    if Version(current) not in SpecifierSet(spec):
        raise RuntimeError(f"插件要求 AstrBot {spec}，当前 {current}")
```

### 8.5 安装（staging → 校验 → 原子落地 → 加载）

```python
async def install_from_zip(zip_path: Path, *, ignore_version_check=False) -> dict:
    async with PM_LOCK:                                   # 全局串行
        with tempfile.TemporaryDirectory(dir=system_tmp(), prefix=".plugin-upload-") as stage:
            staging = Path(stage) / "plugin"
            extract_plugin_zip(zip_path, staging)         # 已剥顶层目录
            return await install_from_directory(staging, ignore_version_check=ignore_version_check)

async def install_from_directory(src: Path, *, ignore_version_check=False) -> dict:
    meta   = load_metadata(src)
    name   = plugin_dir_name(meta)                        # ← 目录名由 metadata 决定
    existing = registry.get_by_name(name)

    if existing and existing.reserved:
        raise RuntimeError("该插件是内置保留插件，无法覆盖")
    target = plugin_dir() / (existing.root_dir_name if existing else name)

    if target.exists():
        # 目录已存在：必须是"同一插件"才允许升级
        if target.is_symlink() or not target.is_dir() \
           or load_metadata(target)["name"] != name:
            raise RuntimeError(f"安装失败：目录 {target.name} 已存在")
        check_version(meta, VERSION, ignore_version_check)
        await install_requirements(src)
        backup = Path(tempfile.mkdtemp(dir=system_tmp(), prefix=".plugin-backup-")) / name
        try:
            await terminate_plugin(name)
            registry.cleanup(name)
            shutil.copytree(target, backup, symlinks=True)   # 先完成备份
            shutil.rmtree(target)
            shutil.move(str(src), str(target))
            ok, err = await load(specified_dir_name=target.name)
            if not ok:
                raise RuntimeError(err or "更新失败")
        except BaseException:
            registry.cleanup(name)
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(backup, target, symlinks=True)   # 回滚
            await load(specified_dir_name=target.name, ignore_version_check=True)
            raise
        finally:
            shutil.rmtree(backup.parent, ignore_errors=True)
    else:
        shutil.move(str(src), str(target))
        await install_requirements(target)
        ok, err = await load(specified_dir_name=target.name,
                             ignore_version_check=ignore_version_check)
        if not ok:
            raise RuntimeError(err or "安装失败，请检查依赖/兼容性")

    return {"name": name, "dir": target.name, "readme": read_readme(target)}
```

**务必补一条 AstrBot 没有的校验**（针对 §2.4 的歧义）：

```python
if not (src / "main.py").is_file():
    raise ValueError("插件缺少 main.py，安装后将无法被加载")
```

### 8.6 启停 / 卸载

```python
# 状态：data/shared_preferences.json
# { "inactivated_plugins": ["plugins.foo.main"], "inactivated_llm_tools": [] }

async def disable(name: str) -> None:
    p = registry.get(name)
    await terminate_plugin(p)                                 # 调 terminate() / __del__
    keys = await prefs.get("inactivated_plugins", [])
    if p.module_path not in keys:
        keys.append(p.module_path); await prefs.put("inactivated_plugins", keys)
    for t in llm_tools.of_module(p.module_path):
        t.active = False
    p.activated = False

async def enable(name: str) -> None:
    p = registry.get(name)
    keys = await prefs.get("inactivated_plugins", [])
    if p.module_path in keys:
        keys.remove(p.module_path); await prefs.put("inactivated_plugins", keys)
    ok, err = await reload(name)
    if not ok:
        raise RuntimeError(err)

async def uninstall(name: str, *, delete_config=False, delete_data=False) -> None:
    p = registry.get(name)
    if p.reserved:
        raise RuntimeError("内置插件不可卸载")
    await terminate_plugin(p)
    await unbind(p)                                            # 摘注册表/handler/tool + 清 sys.modules
    shutil.rmtree(plugin_dir() / p.root_dir_name)              # 只删源码
    if delete_config:
        (config_dir() / f"{p.root_dir_name}_config.json").unlink(missing_ok=True)
    if delete_data:
        for d in ("plugin_data", "plugins_data"):              # 兼容旧目录名
            shutil.rmtree(data_dir() / d / p.root_dir_name, ignore_errors=True)
        await kv.clear_scope("plugin", p.plugin_id)
```

### 8.7 加载：推荐的显式文件加载（比字符串 import 稳）

```python
import importlib.util, sys, uuid

def import_plugin(entry: dict):
    """entry: {"pname","module","path","reserved"}"""
    root_dir_name = entry["pname"]
    file_path = entry["path"] / f"{entry['module']}.py"
    # 模块名带随机后缀，避免同名冲突与热重载缓存
    mod_name = f"qqbot_plugins.{root_dir_name}"
    spec = importlib.util.spec_from_file_location(mod_name, file_path,
                                                  submodule_search_locations=[str(entry["path"])])
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module            # 必须在 exec 前注册，供插件内相对 import
    spec.loader.exec_module(module)           # 抛异常 ⇒ 该插件失败，不中断其它
    return module
```

若坚持 AstrBot 的字符串 import 风格，则必须满足：
1. 启动时 `sys.path.insert(0, PROJECT_ROOT)`；
2. `data/plugins/` 与 `data/plugin_data/` **不放** `__init__.py`；
3. 插件模块路径 = `data.plugins.<dir>.main`（这条字符串会进 `sys.modules` 与配置文件，改动代价高）。

---

## 9. 移植实施清单（Checklist）

- [ ] 引入 `PROJECT_ROOT / data / temp / system_tmp` 四层路径抽象，代码根与数据根分离。
- [ ] 内置插件放在**包内**（`app/builtin_plugins/`），第三方放 `data/plugins/`；同一个 `scan_plugins()` 统一扫描，用 `reserved` 布尔标记。
- [ ] 插件目录名 **= metadata.name**，并在安装时校验「合法 Python 标识符且非关键字」；不要用仓库名/zip 名做目录名。
- [ ] zip 解压到 **staging（系统临时目录）**，校验通过后再 move 进 `plugins/`；绝不直接解压到 plugins（避免半成品被扫描到）。
- [ ] 解压支持**唯一顶层目录压平**（`repo-main/` → 内容上移），并做 Zip Slip 校验。
- [ ] 安装期额外校验 `main.py` 存在（补 AstrBot 的缺口）。
- [ ] 三条独立命名空间：源码 `plugins/<dir>/`、配置 `config/<dir>_config.json`、数据 `plugin_data/<dir>/`；重装/升级不动后两者。
- [ ] 启停状态存共享偏好 JSON，键用 **module_path**（而非插件名）。
- [ ] 卸载默认只删源码，配置/数据/KV 靠 `delete_config` / `delete_data` 显式开关。
- [ ] 升级走「备份 → 换目录 → 加载 → 失败回滚」事务；内置插件拒绝升级/卸载。
- [ ] 单插件加载失败 → 记入 `failed_plugins` 并继续，前端提供"重载失败插件"。
- [ ] 清理统一：`sys.modules` 前缀清理 + handler/tool/适配器注销（热重载必需）。
- [ ] 统一 `plugin_data` 的 key（目录名），避免 AstrBot 里「写用 name、删用 root_dir_name」的错位。

---

## 10. 事实 / 推断 分界

**源码/文档确认（事实）**
- 全部路径函数、`PluginManager` 的三条路径字段、`data/plugins` 与 `astrbot/builtin_stars` 双目录统一扫描、`reserved` 标记。
- 模块路径拼接方式 `"data.plugins." + dir + "." + module` / `"astrbot.builtin_stars."`，以及 `__import__(path, fromlist=[module])`。
- 上传落盘 `data/temp/plugin_upload_<basename>`；staging 在系统临时目录 `.plugin-upload-*`；解压剥离唯一顶层目录；GitHub zipball 顶层 `repo-<sha40>` 缩短。
- metadata 校验集合（文件名/大小/编码/必填字段/desc 别名/name 合法性/`astrbot_version` PEP 440）。
- 目录名 = `metadata.name`；目录已存在且 identity 不同则拒绝；内置插件拒绝覆盖/更新/卸载。
- 配置 `data/config/<dir>_config.json` + `_conf_schema.json`；数据 `data/plugin_data/<name>`（`StarTools.get_data_dir`）；KV 用 `plugin_id = author/name`。
- 启停状态在 `shared_preferences.json` 的 `inactivated_plugins`（module_path 列表）。
- 卸载默认保留配置与数据；`delete_data` 会清 `plugin_data/` 与 `plugins_data/`。
- 失败处理：`failed_plugin_dict` + `_cleanup_plugin_state` + `continue`，返回聚合 `failed_plugin_info`。
- `main.py` 中 `sys.path.append(项目根)`、`check_env()` 里 `insert(0, astrbot_root)` 与 `append(data/site-packages)`、makedirs 各数据目录。
- `astrbot/builtin_stars/` 无 `__init__.py`，仅含 `astrbot/`、`builtin_commands/`；`astrbot/dashboard/` 下无 `routes/`，路由在 `api/plugins.py`。

**推断（未逐行验证，投影响应自行验证）**
- 缺 `main.py` 的 zip 会被判「安装成功但未注册」（由 `_get_modules` 跳过 + `load()` 返回 True 的路径推导）。
- 第三方与内置 `name` 手工同名共存时，`star_registry` 存在两条同名记录与 `get_registered_star` 取首条的二义性。
- 若目录名与 `metadata.name` 被手工改成不一致，`plugin_data` 清理会漏删（写入用 name、清理用 root_dir_name）。
- 早期版本用 `packages/` 组织内置插件 —— 本次未取证。

---

## 11. 参考链接

- 仓库主页：https://github.com/AstrBotDevs/AstrBot
- 插件管理器：https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/star/star_manager.py
- 安装/更新器：https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/star/updater.py
- zip 解压/顶层目录压平：https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/zip_updater.py
- 元数据结构：https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/star/star.py
- 路径函数：https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/utils/astrbot_path.py
- 数据目录 API：https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/star/star_tools.py
- KV 存储：https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/utils/plugin_kv_store.py
- Dashboard 插件路由：https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/dashboard/api/plugins.py
- Dashboard 插件服务：https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/dashboard/services/plugin_service.py
- 启动入口：https://github.com/AstrBotDevs/AstrBot/blob/master/main.py
- 官方文档 · 插件开发入门（含 `data/plugins` 约定、`astrbot_version`）：https://docs.astrbot.app/en/dev/star/plugin-new.html
- 官方文档 · 插件存储（`data/plugin_data/{plugin_name}` 约定）：https://docs.astrbot.app/en/dev/star/guides/storage.html
