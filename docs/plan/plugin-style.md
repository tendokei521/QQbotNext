# 插件代码结构与风格规范（提案与实施记录）

> **状态（2026-10-29）**：B0 已实施，规范正文见 [`docs/PLUGIN_STYLE.md`](../PLUGIN_STYLE.md)；
> 闸门 `tests/architecture/test_plugin_layout.py`、检查/迁移工具 `scripts/plugin_layout.py`、
> 模板 `docs/plugin-template/` 已就位；第一个迁移样例是 `app/plugins/llm_knowledge/`
> （5 个根级实现文件 → `src/`，测试导入同步改为 `...src.xxx`）。
>
> 注：本地插件现在安装在 `data/module/plugins/<name>/`（运行时目录、不进仓库），
> 其源码仓库沿用同一份规范；`python scripts/plugin_layout.py --check` 会把已安装插件
> 一起列出来（只提示、不阻断，因为它们不在本仓库）。

> 范围：两批 Python 插件 —— ①`app/plugins/` 内置插件（现 10 个 LLM 插件）；
> ②本地插件（原 `app/plugins/` 里的业务插件：bilibili_parser / douyin_parser /
> df_password / emoji_reply / group_log / group_request / log_sender / poke_reply /
> recall_back / time_sign_in / what_to_eat / example；安装形态在 `data/module/plugins/<name>/`）。
>
> 目标不是"重写一遍"，而是把现在**已经做得好的习惯**固化成规则，把**位置不统一**的地方统一，
> 让新插件有模板、让结构问题能被自动检查。

---

## 0. 现状盘点（为什么要做）

| 现象 | 证据 |
|---|---|
| 工具函数位置不统一 | 本地插件里既有**根级模块**（`bilibili_api.py` 448 行、`wbi.py` 79 行、`abogus.py`、`sm3.py`、`xbogus.py`、`service.py`），也有 **`service/` 子包**（df_password、recall_back、time_sign_in），还有单文件 `service.py`（emoji_reply、group_request…） |
| `main.py` 体量跨度大 | 31 行（example）～ 195 行（group_log）；内置插件 35～191 行（llm_proactive 191、llm_output 170、llm_context 155） |
| `main.py` 里混入非编排逻辑 | example 的 `default_config` 与 `config_schema.py` 字段不一致；部分插件在 main 里直接写正则/HTTP/格式化 |
| 根目录"什么都能放" | 插件根同时出现实现代码、README、pages/、manifest；新人无法判断新文件该放哪 |
| 文档与实现脱节 | `docs/MODULE_DEV.md §1` 只写了 `main.py + module.json + service.py`，没有 `src/`、没有写作规则、没有行数/职责边界 |
| 检查缺位 | `ruff` 只开 `E/F/I/UP/B` 且 CI `--exit-zero`（只报告）；没有插件结构闸门；`example`（唯一的示例插件）本身就不符合规范 |

同时也要肯定现状里值得保留的东西：多数插件已有**中文文件头 docstring 说明"为什么"**、
纯函数与副作用分离（如 `forward.py`、`wbi.py`）、模块边界写在注释里。规范应把这些保留下来。

---

## 1. 三条原则

1. **入口稳定**：`module.json` + `main.py` 是框架契约，永远只在插件根，路径与文件名不动。
2. **实现可导航**：读代码的人只需要知道两类问题——"这个插件对外接了什么"（`main.py`）、
   "某个具体算法/请求/解析在哪"（`src/` 下按领域命名的文件）。
3. **规则可检查**：能用脚本判定的（目录、行数、导入方向）都写成闸门；不能判定的（命名好坏、
   注释质量）写进本文档与模板，不搞机械拆分。

---

## 2. 目录规范

### 2.1 标准骨架

```text
<插件根>/                     # app/plugins/<name>/ 或 data/module/plugins/<name>/
├── module.json               # 必须：清单（框架契约）
├── main.py                   # 必须：Module 类 + 生命周期 + 编排（唯一入口）
├── __init__.py               # 必须：包标记（插件按包加载，相对导入依赖它）
├── config_schema.py          # 可选：WebUI 配置表单（框架契约）
├── src/                      # 可选：实现细节（工具函数、领域逻辑、协议/算法）
│   ├── __init__.py           # 必须（有 src/ 时）
│   ├── api.py                # 按领域命名，不用 utils.py / helpers.py
│   ├── parsing.py
│   ├── store.py
│   └── crypto.py
├── pages/index.html          # 可选：插件自带设置页
├── README.md                 # 推荐：做什么 / 怎么配 / 数据存哪
└── requirements.txt          # 可选：第三方依赖清单
```

### 2.2 根目录白名单

**框架认识或给人看的文件**才能放根：`module.json`、`main.py`、`__init__.py`、
`config_schema.py`、`README.md`、`requirements.txt`、`pages/`、`src/`。

"根目录也可以开别的文件"——允许，但需满足**同时**以下条件，否则进 `src/`：

- 只有一个文件、且 ≤100 行；
- 不与其它根级 `.py` 互相 import（只被 `main.py` import）；
- 在 README 的「文件说明」里写明它是什么。

例：`wbi.py`（79 行纯签名算法）可以留根；`bilibili_api.py`（448 行）必须进 `src/`。

### 2.3 `src/` 规则

- `src/` 是插件内部实现包：只能被本插件的 `main.py` / `config_schema.py` / `src/*` 导入，
  **禁止反向 import main**（否则循环依赖）。
- 文件按**领域**命名：`api.py` / `parsing.py` / `store.py` / `format.py` / `crypto.py` /
  `consts.py`；禁止 `utils.py`、`helpers.py`、`misc.py` 这类垃圾桶（找不到好名字说明还没想清职责）。
- 单个文件建议 ≤200 行、硬上限 300 行；超过时按子域拆；**同一子域 ≥4 个文件**才升级成
  `src/<子域>/` 子包（避免每个插件都长出一棵树）。
- 纯函数优先：`src/` 的函数接收参数、返回结果，不读 `self`、不读全局配置、不直接 `self.send_to`。
  需要配置就由 `main.py` 取好传进来（这样能单测）。

### 2.4 `main.py` 写什么

允许（且鼓励）留在 `main.py`：

- 类属性：`name / sign / description / permission / category / pinned / subscribe /
  supports_single_service / order`；
- 配置：`default_config`（与 `config_schema.py` 的键**逐一对一**）与 `config_schema = SCHEMA`；
- 生命周期：`on_load / on_unload / on_online / on_offline`；
- hook：`@module_hook` / `@llm_hook` / `@send_hook` 注册的处理函数；
- **编排**：读开关 → 取上下文 → 调 `src/` 的函数 → 产生副作用（发消息 / 写日志 / 建任务）；
- 面板/命令注册之类"接线"代码。

禁止留在 `main.py`：

- 正则表、HTML/JSON 解析细节、加解密、签名算法；
- HTTP 请求与重试细节（放 `src/api.py`）；
- SQL / 存储实现（放 `src/store.py`）；
- 长常量表（放 `src/consts.py`）；
- 与框架无关的通用文本/时间处理。

体量：建议 ≤150 行，硬上限 220 行；**超过 220 行必须拆**。反过来，行数只是触发器——
即使不到 220 行，只要出现上面"禁止"清单里的内容，也应抽到 `src/`。

每个 hook 建议 ≤30 行，形态固定：

```python
@module_hook("message_group", order=10)
async def handle_message(self, event):
    if not self.config.get("enable", True):
        return
    text = await extract_link(event.message)
    if text is None:
        return
    await self.send_to(event.session_id, build_reply(text))
```

### 2.5 其它根目录文件的写法

- `config_schema.py`：只有 `SCHEMA = {...}`，键与 `default_config` 一一对应；label/description
  面向用户（≤40 汉字、禁内部术语，已有闸门 `tests/architecture/test_ui_copy.py`）。
- `README.md`：三段——做什么 / 配置项含义 / 数据与权限（数据写哪、要不要 owner）。
- `module.json`：由 `scripts/gen_module_manifests.py` 生成/校验，手改前先看生成规则。

---

## 3. 写作风格

| 维度 | 规则 |
|---|---|
| 文件头 | 一行 docstring 说明"这个文件负责什么"；复杂文件补 3-5 行"为什么这样拆"（保留现有好习惯） |
| 注释 | 解释**为什么**，不复述代码；中文 |
| 命名 | 函数/变量 `snake_case`；常量 `UPPER_SNAKE`（集中放 `src/consts.py` 或 main 顶部）；私有 `_helper`；除 `ctx` / `config` 外不缩写 |
| 类型 | `src/` 导出的函数与 `main.py` 的私有方法必须有参数与返回注解；模块级 `from __future__ import annotations` |
| 导入顺序 | 标准库 → 框架（`app.*`）→ 本地（`.config_schema` / `.src.*`），组间空行（ruff `I` 自动排） |
| 日志 | 用框架 logger：`logger.add_info(account_label(self.bot_id))`；禁止 `print`；失败分支必须带原因 |
| 异常 | hook 不吞异常（让框架记堆栈）；只有"预期会失败且可降级"才捕获，并 `warning/debug` 留痕 |
| 异步 | 网络用 async 客户端；同步库用 `asyncio.to_thread`；禁止 `time.sleep`；后台任务在 `on_unload` 取消/关闭 |
| 配置 | 只在 `main.py` 读 `self.config.get(key, default)`，默认值与 schema 一致；`src/` 不读全局 |
| 副作用 | `src/` 不发消息、不写日志以外的东西；发送统一回 `main.py` |

---

## 4. 标准模板（`example` 改造后）

`example` 作为唯一官方模板，改成"一个开关 + 一个关键词回复"的最小闭环：

```text
example/
├── module.json
├── __init__.py
├── main.py                 # ≈45 行：属性 + 生命周期 + 1 个 hook
├── config_schema.py
├── src/
│   ├── __init__.py
│   └── reply.py            # 纯函数：命中判断 + 文案拼装
└── README.md               # 三段：做什么 / 配置 / 数据
```

`main.py`：

```python
"""模块声明：示例插件（新插件的起点）。"""

from __future__ import annotations

from app.plugin import BaseModule, module_hook

from .config_schema import SCHEMA
from .src.reply import build_reply, hit_keyword


class Module(BaseModule):
    name = "示例插件"
    sign = "Example"
    description = "示例插件，用来照着写自己的插件"
    permission = "everyone"
    category = "消息"
    default_config = {"enable": False, "keywords": ["hello"]}
    config_schema = SCHEMA

    @module_hook("message_group", order=10)
    async def handle(self, event):
        if not self.config.get("enable", False):
            return
        if not hit_keyword(event.message, self.config.get("keywords", [])):
            return
        await self.send_to(event.session_id, build_reply(event))
```

`src/reply.py`：只有两个纯函数，无框架依赖，可直接单测。

---

## 5. 落地方式（检查 + 迁移）

### 5.1 新增两道闸门（Batch 0，无业务改动）

1. `tests/architecture/test_plugin_layout.py`：
   - 根目录白名单（见 §2.2），根级非白名单 `.py` 必须 ≤100 行且不被其它根级 py import；
   - `main.py` ≤220 行（现有超标插件先进 allowlist，改完即删）；
   - `src/` 存在时必须有 `__init__.py`，且 `src/**` 不得 import `..main`；
   - 每个插件必须有 `__init__.py`、`module.json`；README 缺失只告警（不阻断）。
2. `scripts/plugin_layout.py`：按同一套规则做**修复建议**（`--check` 输出"建议移动到 src/ 的文件"），
   可选 `--apply` 做机械移动 + import 改写，便于批量迁移。

### 5.2 ruff 收紧（Batch 0 同步）

- 维持 `E/F/I/UP/B`，CI 从 `--exit-zero` 改为**阻断**（先只对 `app/plugins/**` 生效，
  全仓随后）；
- 引入 `ruff format --check`（格式化）作为**新增文件**门槛，存量一次性 `ruff format app/plugins`
  单独一个"纯格式化"提交，便于 review；
- 不引入 mypy（类型注解按 §3 人工要求 + 单测覆盖，先看收益再上）。

### 5.3 分批迁移（每批一个 PR，可单独回滚）

| 批次 | 范围 | 内容 | 验证 |
|---|---|---|---|
| B0 | 规范与工具 | 本文档、`MODULE_DEV.md §1` 重写、结构闸门、ruff 收紧、`example` 模板 | 闸门全绿；模板一个新插件从 0 能跑 |
| B1 | 本地插件（12 个） | 根级实现文件 → `src/`；`service/` → `src/`；group_log 的 main 保留（全是编排）但 README 补三段；统一 `__init__.py` | 每个插件单测 + zip 安装冒烟（安装→启用→触发→卸载） |
| B2 | 内置插件（10 个） | 同上；`bilibili_api.py` 这类 448 行文件进 `src/`；llm_proactive/llm_output/llm_context/llm_memory 的 main 抽到 ≤150 行 | 全量 `pytest -q`；内置清单生成 `--check` |
| B3 | 收口 | 删除 allowlist、放开全仓阻断、CI 加 plugin-layout job | 全绿 |

测试导入迁移：内置插件的 tests 里有约 40 处 `app.plugins.<x>.<mod>` 深层导入，
B2 用一次性 codemod（`scripts/plugin_layout.py --apply --tests`）改为 `app.plugins.<x>.src.<mod>`，
与移动在同一个提交里，全量测试兜底。

### 5.4 边界与风险

- **外部已安装插件**（用户 `data/module/plugins/`）不强制迁移；新规范只约束仓库里的两批，
  以及"新安装/更新"的模板产出。
- **没有 `__init__.py` 的极简插件**：loader 目前兼容，但 `src/` 相对导入依赖包语义 →
  新规范要求插件根必须有 `__init__.py`（结构闸门检查）。
- **不做兼容 shim**：`service.py` 迁到 `src/service.py` 后不保留旧路径 re-export（一次性改完）；
  旧文档/外部引用在 B0 的 MODULE_DEV.md 里写清迁移说明。
- **不为了行数机械拆分**：行数是触发器，职责才是依据；`group_log` 195 行全是生命周期与接线，
  允许保留。

---

## 6. 验收标准

1. 结构闸门全绿：根白名单、`main.py` ≤220、`src/` 方向、必需文件齐全。
2. 两批插件：无一实现文件留在根（除 §2.2 例外）；每个插件有 README 三段。
3. `example` 模板：新建插件按模板 10 分钟内能跑通（安装 → 启用 → 触发 → 卸载）。
4. `ruff format --check` 与 `ruff check`（阻断）通过。
5. 全量 `pytest -q` 通过；内置清单 `gen_module_manifests.py --check` 通过。

---

## 7. 待你拍板的三个点

1. **`src/` 是否作为强制目录**（无 `src/` 时全部逻辑写 main 是否可接受）——
   建议：插件总实现 >200 行或有 ≥2 个实现文件时**必须**有 `src/`，否则可不建。
2. **`main.py` 硬上限**：建议 220 行；若你觉得该更宽/更严，我按你的数落闸门。
3. **ruff 是否现在就阻断**（会要求先做一次全仓格式化提交）：建议是，格式单独一个提交，
   逻辑迁移分开提交，review 更清楚。
