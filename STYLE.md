# QQBot Next 代码风格规范（STYLE.md）

> 适用版本：2.0（分层 + 插件架构）。所有新增/修改代码必须遵循本规范。
> 本规范是对代码库**既有事实风格**的固化，不是另起炉灶——写新代码前先看同目录旧代码。

---

## 1. 总则

- **一致性优先**：与同目录、同层的既有代码保持一致，优先于个人偏好。
- **可读性优先于技巧**：代码是写给下一个维护者（很可能是你自己）看的。
- **中文注释**：docstring 与注释一律中文；变量/函数/类名一律英文。
- **解释"为什么"**：注释写设计原因、边界条件、踩过的坑，不写"这行做了什么"。

## 2. 命名规范

| 对象 | 规则 | 示例 |
|---|---|---|
| 文件/包 | `snake_case` | `config_service.py` |
| 类 | `PascalCase` | `ModuleRegistry`、`TaskScheduler` |
| 函数/方法 | `snake_case`，动词开头 | `load_all`、`check_permission` |
| 常量 | `UPPER_SNAKE` | `DEFAULT_LLM_CONFIG`、`ACCESS_TOKEN_MASK` |
| 私有成员 | `_` 前缀 | `_tasks`、`_purge_module_cache` |
| 接口 | `I` 前缀 | `IBot` |
| 模块 sign（业务标识） | `CamelCase` | `Delta_Password`、`BilibiliParser` |

- 方法命名统一动词开头：`get/set/save/load/register/cancel/has/is`。
- 布尔属性用 `is_`/`has_`/`can_` 前缀（如 `is_alive`、`can_reply`）。

## 3. 格式规范

- **双引号为主**：字符串字面量用双引号 `"..."`（全库约 99.5% 一致）；仅在字符串内含双引号时用单引号。
- **行长 ≤ 120 列**（`pyproject.toml` 已配置 `[tool.ruff] line-length=120`）。
- 4 空格缩进；类间 2 空行、方法间 1 空行（PEP 8）。
- 长参数列表换行：每参数一行、4 空格缩进，如：

```python
async def add_bot(
    self, ws_url: str, owner_id: int | None, auto_connect: bool = False, index: int | None = None
) -> int:
```

- **区域分隔注释**：一个类内按功能分区时，用等号分隔线：

```python
# ==================== 连接管理 ====================
```

- 不强制分号结尾、不用多语句单行。

## 4. 类型注解（强制）

所有公开函数必须有参数与返回注解；局部变量可不注解。

- **统一用 PEP 604 联合写法**：`X | None`，禁止 `Optional[X]`（全库已迁移）。
- **统一用内置泛型**：`dict[...]`、`list[...]`、`tuple[...]`、`set[...]`，禁止 `Dict[...]`、`List[...]`（全库已迁移）。
- `Union[A, B]` → `A | B`。
- 保留 `typing` 导入的名字（无内置替代）：`Any`、`Callable`、`Iterable`、`Sequence`、`Awaitable`、`Coroutine`、`TypeVar`、`Generic`、`cast`。
- **不留未使用导入**：`from typing import X` 中 X 若未被使用必须删除。
- 返回自身类名等前向引用时用字符串注解：`-> "MessageSegment"`。

## 5. 注释与文档

- 每个模块文件顶部必须有中文 docstring：职责说明 + 演进溯源（"替代原 xxx"），如：

```python
"""配置中心：所有配置的单一入口。

- SQLite 落盘 + 内存缓存 + 变更通知（替代原 watchdog 文件监听）；
- 首次启动自动从旧 JSON 文件迁移。
"""
```

- 类 docstring 说明设计意图与责任边界；关键算法必须写边界条件（见 `gateway.py::_wait_for_message` 的注释风格）。
- 移植/化用外部项目（如 AstrBot）的代码，必须在 docstring 或注释中标注来源。
- 禁止废话注释（`# 加1` 之类）；`# noqa`、`# pragma: no cover` 需说明理由或由工具生成。

## 6. 代码组织

- **分层依赖只向下**：`core → domain → infrastructure → modules/services → webui`；装配只在 `app/bootstrap.py`。
  > ⚠️ `app/llm/` **目前不在这条链上**：它被 `services`/`modules`/`webui` 依赖，又反过来依赖 `modules`，
  > 并与另外 23 个文件互为循环依赖（见 §12.1）。新增代码不要加深这个环。
- **单文件单职责**：一个文件只做一件事。判据不是行数，而是"改它时你是否被迫同时读几个无关文件"。
- **函数内惰性导入是最后手段，不是惯例**：仅当**确实无法**通过构造函数/参数注入解决时才用，
  且在 import 行上方注释说明"为什么必须在这里"。它会让依赖图在静态分析中不可见
  （全库现存 315 处），是循环依赖的**症状**而非解法——治因不治症。
  模块级导入仍按 标准库 → 三方 → 本地 排序。
  > 修订说明：原文写的是"允许（且鼓励）"。把缺陷写成风格，会让新代码持续复制它。
- **函数行数分档**：新增函数 ≤ 50 行；修改存量函数时若它已 > 80 行，先把你要改的部分拆出来再动。
  > 原文为"函数尽量 ≤ 50 行"——规定与实际相差太远（全库 41 个函数 ≥ 80 行，最大 378 行），
  > 无人执行的规范等于没有规范。改为分档后至少**新增**部分是能守住的（见 §12.1 验收指标）。
- **业务插件目录**（细节以 `docs/MODULE_DEV.md §1` 为准，本文件不重复定义）：
  `module.py` **必须**；`service.py`、`config_schema.py`、`pages/` **可选**；
  业务一多就把 `service.py` 升级为 `service/` 子包（如 `df_password`、`recall_back`、`time_sign_in`）。
  模块内部用**相对导入**。
  > 原文写"目录结构固定：`{module.py, service.py, config_schema.py}`"——实际 13 个插件有 7 个不符
  > （4 个没有 `service.py`，3 个用 `service/` 子包）。规范应向已存在的合理做法靠拢，而不是反过来。
- **三个 "module" 概念不要混**（命名撞车的根源，动手前先确认自己在说哪一个）：
  `app/modules/` = 插件**框架**（`BaseModule`/注册表/权限/hook）；
  `module/modules/` = 业务插件**实现**；
  `module/plugins/` = zip 安装的**外部**插件。

## 7. 错误处理与日志

- **异常永不裸吞**：`except` 必须记录日志或显式处理；禁止 `except: pass`。
  本规则现已由测试 `tests/test_no_silent_except.py` **自动强制**（AST 扫描全库，
  禁止"异常体只有 pass/continue/break/return None"的处理块）。确需静默的位置
  （如"写日志本身失败"的兜底）必须加入该测试的白名单并注明理由。
- **按严重度选级别**：降级/可忽略用 `debug`；可能影响用户可感知行为用 `warning`
  （例如记忆蒸馏调度失败——静默会让"记忆永不再更新"毫无迹象）；
  未预期故障用 `exception` 保留堆栈。
- **统一用 `logger.exception(...)` 记录带堆栈的异常**（在 except 块内），替代 `traceback.print_exc()` + `logger.error()` 组合：

```python
except Exception as e:
    self.log.exception(f"[Module] {name} 加载失败: {e}")
```

- 日志前缀链式构造：`logger.add_info(f"#{index}").add_info(module.name).info(...)`；不要拼接裸字符串前缀。
- 跨组件状态用 `TaskManager` 管理后台任务（可追踪、级联取消），不要散落 `asyncio.create_task`。

## 8. 设计模式惯例

- **门面**：包装外部服务（ConfigService）的读写，如 `ModuleConfig`、`AgentConfig`——**业务代码永远不直接碰 SQLite**。
  新增持久化一律走 `app/infrastructure/persistence/` 的 `Database` / `BaseRepository`。
  > ⚠️ 现状有 10 处例外：`llm/history.py`、`llm/memory/store.py`、`llm/knowledge/store.py`、
  > `llm/knowledge/vector.py` 直连 `sqlite3`，而 `BaseRepository` 至今零引用（见 §12.6）。
  > 那是待还的债，**不要照抄**。
- **依赖注入**：一切组件通过容器装配（`bootstrap.py`），构造函数收依赖。
  **禁止**在业务代码里直调 `get_container()` 或 `from app.bootstrap import ...`——那是服务定位器反模式，
  会把模块焊进 `bootstrap` 所在的循环依赖（现状 71 处，见 §12.2）。
  > 原豁免条款「桥接函数 `app.core.container.service()` 除外」**已废止**：该函数全库零引用，
  > 唯一作用是留下一条 `container → bootstrap` 的反向依赖边，把 DI 容器本身拖进了循环。
- **节点链**：一切会触碰消息的能力实现 `MessageNode.process(ctx, next_)`；拦截=不调 `next_()`。
- **事件驱动**：跨切面变化走 `EventBus` / `ConfigService.on_change`，不轮询。
- **模块权限**：所有业务模块继承 `BaseModule`，声明式元数据，业务里不重复实现权限判断。

## 9. 测试要求

- 新增/修改功能必须配套测试，位于 `tests/`，命名 `test_*.py`（用例可与功能一起留到该功能收尾时再跑全量）。
- **验证按改动规模分层**（细则以 `AGENTS.md`「验证规则」为准，本节不重复定义）：
  - 小改动（文档/注释/文案/重命名/格式化）→ **不跑测试直接提交**；
  - 局部逻辑改动 → 只跑相关测试文件（`pytest -q tests/test_x.py`）；
  - 大工程收尾 / 发版前 → 全量 `venv\Scripts\python.exe -m pytest -q`（**全绿无失败**）
    + `ruff check .` 无新增违规；基线用例数不写死，随迭代增长，需要时用
    `pytest --collect-only -q | tail -2` 取当前值。
- CI（`.github/workflows/ci.yml`）在每次 push/PR 跑全量测试，是本地豁免全量后的安全网。
- 任何档位都**不许带着已知失败提交**：跑了且失败，就先修或先说明。
- 测试用 `pytest-asyncio`（`asyncio_mode = "auto"`，无需 `@pytest.mark.asyncio`）。

## 10. 工具链现状

### 10.1 已启用

- **ruff**（`pyproject.toml` 的 `[tool.ruff]`）：`line-length=120`、`target-version="py310"`，
  规则集 `select = ["E", "F", "I", "UP", "B"]`（对应 STYLE.md 各项约定：未使用导入、
  旧式注解 `Optional`/`Dict`、导入顺序、常见 bugbear 陷阱）。
- **pytest-cov**：`[tool.coverage.*]` 已配置统计范围（`app` + `module`，排除 `tests/`、
  `scripts/`、`deprecated/`）。
- **CI**（`.github/workflows/ci.yml`）：push/PR 自动跑测试 + 依赖元数据校验 + 前端构建。

> ⚠️ **当前 ruff 与覆盖率均为"报告不阻断"**：CI 里 ruff 用 `--exit-zero`，
> 覆盖率未设 `--cov-fail-under`。原因是存量违规尚未清理，先拿基线再收紧。
> **收紧方式**：删掉 CI 中的 `--exit-zero`、给 pytest 加 `--cov-fail-under=<略高于当前值>`。

**但"全库不阻断"不等于"增量可以脏"**——这两件事必须分开看，否则 `--exit-zero` 会变成永久的免责声明：

- **增量零容忍**：改动涉及的**文件**，ruff 违规数不得增加。局部/大工程档位都要跑
  `ruff check <本次改过的文件>`（不是全库）。新增代码应当零违规。
- **存量单调递减**：清理可以慢，但只能往下走。做大工程收尾时顺手清一批，
  并在 `MAINTENANCE.md §CI` 里更新一次基线数字（当前见该文件）。
- **阈值棘轮**：`--cov-fail-under` 一旦加上，只允许往上调，不允许为了过 CI 而调低。

### 10.2 本地用法

```bash
venv\Scripts\activate.bat
pip install -e ".[dev]"          # 一次性，装齐 ruff / pytest-cov

ruff check .                     # 静态检查（清理存量时用 --statistics 看分布）
ruff check --fix .               # 自动修可修项（I/UP/F401 等）
pytest -q                        # 日常：快速跑测试（不统计覆盖率）
pytest -q --cov --cov-report=term-missing   # 需要覆盖率时
```

### 10.3 提交前自查清单

> 按 `AGENTS.md` 的验证分档执行：小改动档位只走下面这些"看代码"的条目，
> 带 🔬 的两条留到大工程收尾 / 局部逻辑改动时执行。

- [ ] 无未使用的 import
- [ ] 注解用 `X | None` / 内置泛型
- [ ] 异常用 `logger.exception` 记录；**禁止 `except: pass`**（由 `tests/test_no_silent_except.py` 强制）
- [ ] 中文注释解释了"为什么"
- [ ] **没有新增** `get_container()` 直调 / `from app.bootstrap import ...`（§8）
- [ ] **没有新增**函数内惰性 import；确需新增的已在 import 上方注明原因（§6）
- [ ] 新增函数 ≤ 50 行（改存量长函数时，先拆你要动的那部分）
- [ ] 涉及 `app/webui/api/` 或 §12 列出的区域时，确认没有加深循环依赖
- [ ] 新增依赖只改 `pyproject.toml` 一处：**不要**在 `requirements.txt` 里重复列举
      （该文件已改为指向 `pyproject.toml` 的纯指针，CI 会校验）
- [ ] 🔬 改动相关测试全绿（小改动档位可不跑；大工程收尾时改为 `pytest -q` 全量全绿）
- [ ] 🔬 `ruff check <本次改过的文件>` 无**新增**违规（不是全库；存量按批次清理，见 §10.1）

## 11. 提交规范（Commit）

> 本节于 2025 年修订：原先规定「提交信息 = 时间戳」「工作区改动一次性提交」，
> 两者与 `AGENTS.md` 的提交规则（Conventional Commits + 一步一提交）直接冲突，
> 且仓库历史中两套风格并存。现统一为 **Conventional Commits + 一步一提交**，
> `AGENTS.md` 为准一来源；本节如与 `AGENTS.md` 不一致，以 `AGENTS.md` 为准。
> 提交前要跑多少测试同样以 `AGENTS.md`「验证规则」为准（小改动不必跑全量）。

- **格式**：`<type>(<scope>): <subject>`

  ```
  feat(dashboard): add PageSectionNav component
  fix(llm): remove schedule fallback
  docs: add agent commit rules
  refactor(provider): normalize api_base endpoints
  ```

- **type 取值**：`feat` / `fix` / `docs` / `refactor` / `test` / `chore` / `perf` / `build` / `ci` / `style`。
- **scope**：可选，取受影响的分层或组件名（`llm`/`gateway`/`config`/`dashboard`/`deps`…）。
- **subject**：祈使句、小写开头、不加句号，说明"做了什么"（英文；遵守本仓库注释用中文的约定时，
  可写中文 subject，但 type/scope 仍用英文）。
- **一步一提交**：每完成一个可独立提交的步骤立即提交，保持"小而完整"、一个提交只做一件事，
  便于回滚与审查；**不要**攒到最后一次性提交。
- **不混入无关改动**：工作区若有与当前步骤无关的改动，只 `git add` 相关文件，不要顺手带进当前提交。
- **不提交运行时产物**：`data/`、`logs/`、`__pycache__`、`.env` 等（见 `.gitignore`）。
- **提交前自查**：按 `AGENTS.md`「验证规则」分档执行——小改动直接提交（不跑测试），
  局部逻辑改动跑相关测试文件，大工程收尾时 `venv\Scripts\python.exe -m pytest -q` 全量全绿。
- **模板**：仓库根目录 `.gitmessage` 提供 Conventional Commits 预填模板，执行
  `git config commit.template .gitmessage` 后 `git commit`（不带 `-m`）即可启用。

## 12. 已知架构债（可量化，改动前必读）

> 本节数字为 **AST 解析全库 import 图实测**得出，不是估计。动手前先读这里：
> **不要以现状为范例**。债还掉一部分后请把新数字更新回本节（这是唯一的进度记录）。
>
> 口径：行数=文件总行数（含空行与注释）；**数字会随代码变动漂移**，复核时请重算，不要照抄。
> 重算方法：解析每个 `.py` 的 `Import`/`ImportFrom`（`ast.walk` 会连函数内惰性 import 一起覆盖），
> 建图后求「前向可达 ∩ 反向可达」即得强连通块；闭包大小 = 前向可达集合的行数合计。

### 12.1 一个 24 文件 / 12,112 行的强连通块

`app/llm/chat.py` 所在的一块里有 24 个文件**互为递归依赖**，任何一处都无法单独理解：
`chat`(2192) `context_tools`(1059) `scheduler`(967) `assembly`(790) `image`(623)
`image_media`(617) `pipeline`(582) `session_history_service`(557) `webui/api/modules`(539)
`features`(538) `proactive`(520) `webui/api/agent`(435) `manager`(433) `bootstrap`(416)
`webui/api/sessions`(378) `webui/app`(375) `referent`(314) `initiative_stream`(196)
`webui/api/media`(132) `system_tools`(126) `app/modules`(116) `core/container`(97)
`modules/api`(81) `webui/api/framework`(29)。

后果：只改 `chat.py` 一处，静态可达 **143 个文件 / 42,082 行**——而 `app/` 全部才 166 文件 /
46,658 行（且这个闭包还包含部分 `module/` 下的文件）。等于改一行就要理解整个应用。

**验收指标：任何入口文件的可达闭包 ≤ 8,000 行。** 现状 chat.py = 42,000+。
自检办法：对目标文件做一次函数级 import 闭包统计（含函数内 import），别只看文件顶部。

### 12.2 服务定位器 71 处

`get_container()` 被直调 **71 次 / 14 个文件**，其中 **65 次在 `app/webui/api/*`**——
这是 webui 那 6 个文件被焊进 §12.1 循环的直接原因。
目标：全部改为构造函数注入（`app/webui/api/deps.py` 已是 FastAPI 依赖注入入口，应统一走那里）。

### 12.3 两套同名调度器

`app/services/scheduler.py::SchedulerService`（203 行，模块 `SCHEDULES=` 声明式定点任务）
与 `app/llm/scheduler.py::TaskScheduler`（834 行，LLM 语义定时，落盘 `tasks_data.json`）
在时间解析、到点触发、取 bot 连接上**零复用**。改任一处前先确认改的是哪一个。

### 12.4 双前端

`dashboard/`（Vue3 + Vuetify）与 `app/webui/static/`（手写 `main.js` 74KB + `widgets.js` 36KB +
`index.html` 46KB + `style.css` 37KB ≈ 195KB）功能重叠：同一张 Agent 配置表单实现了两遍
（`DynamicWidget.vue` vs `widgets.js`）。`app/webui/app.py` 保留 `/legacy` 回退。
**schema 每加一个字段要改两处，第二处极易漏且没有测试会红。**

### 12.5 巨型数据伪装成代码

`app/llm/onebot_tools/manifest.py` **4,789 行**（纯 `ONEBOT_TOOLS` 字面量）、
`app/llm/config_schema.py` **1,037 行**（纯 `SCHEMA` 字面量）。改一条工具描述会淹没在 4789 行的
diff 里。目标：迁成 `data/schemas/*.json` + pydantic 校验 + 生成脚本。

### 12.6 领域层空心

`app/domain/` 仅 **459 行 / 4 文件**，全是 DTO，没有业务规则。真正该属于领域的判断
（"何时该主动说话""下次触发怎么算""人格规则怎么裁"）散在 `llm/proactive.py`、
`llm/scheduler.py`、`llm/persona_rules.py` 里，与 LLM 调用、JSON 落盘混居。
同时 `BaseRepository` 零引用（见 §8 门面）。

---

*本规范依据代码库实际风格整理，2025 年定稿、2025 年按实测复核修订（§6 / §8 / §10.1 / §12）。
新增约定请先与本文件核对，必要时经讨论后更新。*

*遇到规范与代码不一致时，先判断哪一边是对的——**既不要把既成事实直接写成规范，
也不要把无人执行的理想一直留在文档里。** 这两种做法都会让下一个人（或 Agent）照着错的做。*
