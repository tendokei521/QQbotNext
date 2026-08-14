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
- **单文件单职责**：一个文件只做一件事（参考 `app/llm/` 的拆分）。
- **函数内惰性导入是惯例**：为规避循环依赖与支持热重载，允许（且鼓励）在函数内 `from .service import handle`、`from app.bootstrap import get_container`；模块级导入仍按 标准库 → 三方 → 本地 排序。
- 函数尽量 ≤ 50 行；超过说明该拆了。
- 业务插件目录结构固定：`module/modules/<name>/{module.py, service.py, config_schema.py}`，模块内部用**相对导入**。

## 7. 错误处理与日志

- **异常永不裸吞**：`except` 必须记录日志或显式处理；禁止 `except: pass`。
- **统一用 `logger.exception(...)` 记录带堆栈的异常**（在 except 块内），替代 `traceback.print_exc()` + `logger.error()` 组合：

```python
except Exception as e:
    self.log.exception(f"[Module] {name} 加载失败: {e}")
```

- 日志前缀链式构造：`logger.add_info(f"#{index}").add_info(module.name).info(...)`；不要拼接裸字符串前缀。
- 跨组件状态用 `TaskManager` 管理后台任务（可追踪、级联取消），不要散落 `asyncio.create_task`。

## 8. 设计模式惯例

- **门面**：包装外部服务（ConfigService）的读写，如 `ModuleConfig`、`AgentConfig`——模块永远不直接碰 SQLite。
- **依赖注入**：一切组件通过容器装配（`bootstrap.py`），构造函数收依赖，不内部 import 单例（桥接函数 `app.core.container.service()` 除外）。
- **节点链**：一切会触碰消息的能力实现 `MessageNode.process(ctx, next_)`；拦截=不调 `next_()`。
- **事件驱动**：跨切面变化走 `EventBus` / `ConfigService.on_change`，不轮询。
- **模块权限**：所有业务模块继承 `BaseModule`，声明式元数据，业务里不重复实现权限判断。

## 9. 测试要求

- 新增/修改功能必须配套测试，位于 `tests/`，命名 `test_*.py`。
- 提交前必须全量通过：`venv\Scripts\python.exe -m pytest -q`（当前基线 142 个用例全绿）。
- 测试用 `pytest-asyncio`（`asyncio_mode = "auto"`，无需 `@pytest.mark.asyncio`）。

## 10. 工具链现状与建议

- 已配置：`pyproject.toml` 中 `[tool.ruff] line-length=120, target-version="py310"`、`[tool.pytest.ini_options]`。
- 建议（暂未启用）：安装 ruff 后启用规则集（`select = ["E", "F", "I", "UP", "B"]`），可自动拦截：未使用导入、旧式注解（`Optional`/`Dict`）、`print` 调试残留。
- 提交前自查清单：
  - [ ] 无未使用的 import
  - [ ] 注解用 `X | None` / 内置泛型
  - [ ] 异常用 `logger.exception` 记录
  - [ ] 中文注释解释了"为什么"
  - [ ] `pytest -q` 全绿

## 11. 提交规范（Commit）

- **提交信息格式**：`YYYY.M.D HH:MM`（本地时间，如 `2026.8.14 16:10`）。
  每次 commit / push 的信息就是当时的日期时间，不写功能描述。
- **生成方式**：`git commit -m "$(Get-Date -Format 'yyyy.M.d HH:mm')"`（Windows PowerShell）。
- **模板文件**：仓库根目录 `.gitmessage` 已提供模板；执行
  `git config commit.template .gitmessage` 后，`git commit`（不带 -m）会预填该格式。
- **一次性提交**：工作区的所有改动一次性 commit + push，不拆碎提交。
- **提交前自查**：`pytest -q` 全绿；不提交 `data/` 运行时数据之外的临时文件。

---

*本规范依据代码库实际风格整理，2025 年定稿。新增约定请先与本文件核对，必要时经讨论后更新。*
