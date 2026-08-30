# 全局能力注册表（FeatureRegistry）

> 适用版本：2.0（分层 + 插件架构）
> 本文档面向插件开发者，说明如何声明“我接管某个框架能力”，以及框架如何自动恢复。

---

## 1. 解决的问题

框架内置了一些“同类能力”，例如：

- 框架主动消息：`proactive`
- 框架定时任务：`schedule`
- 长期记忆：`memory`
- 知识库：`knowledge`
- NapCat Tools：`napcat_tools`
- 框架级 Agent 整体启停：`agent`

当第三方插件也实现同类能力时（例如“新的主动回复插件”），需要有一个统一机制让插件：

1. 声明自己接管该能力；
2. 接管期间暂停框架内置实现；
3. 插件卸载/禁用后自动恢复；
4. 多个插件同时想接管时不会互相覆盖。

`FeatureRegistry` 就是为此提供的全局能力注册表。

---

## 2. 核心概念

| 概念 | 说明 |
|---|---|
| `FeatureController` | 框架能力的控制器，提供 `suppress()` / `restore()` / `status()` |
| `FeatureRegistry` | 全局注册表，维护控制器与插件“租约” |
| `provides` | `BaseModule` 类属性：本插件提供的能力 ID（信息性） |
| `supersedes` | `BaseModule` 类属性：本插件启用时自动接管的能力 ID |
| 租约（Lease） | 一个插件对某能力在某 Bot 实例上的接管记录 |

---

## 3. 插件声明式接管（推荐）

```python
# module.py
from app.modules import BaseModule

class Module(BaseModule):
    name = "我的主动回复"
    sign = "MyProactive"
    description = "替代框架原生主动消息"

    provides = ("proactive",)      # 我提供主动消息能力
    supersedes = ("proactive",)    # 我启用时接管框架主动消息
```

只需要这两行，框架会自动：

- 插件加载/启用时：调用 `FeatureRegistry.acquire_module(self)`
- 插件卸载/禁用时：调用 `FeatureRegistry.release_module(self)`
- 接管期间：框架内置 `proactive` 停止发主动消息
- 插件离开后：如果没有其他接管者，恢复 `proactive` 原本配置与运行状态

---

## 4. 手动控制

如果不想用声明式，也可以在 `on_load` / `on_unload` 里手动操作：

```python
from app.modules import BaseModule

class Module(BaseModule):
    async def on_load(self):
        await self.ctx.services.features.suppress("proactive", self, self.bot_id)

    async def on_unload(self):
        await self.ctx.services.features.release("proactive", self, self.bot_id)
```

查询状态：

```python
# 单个能力
self.ctx.services.features.query("proactive", self.bot_id)

# 当前 Bot 的全部能力状态
self.ctx.services.features.status(self.bot_id)

# 或使用插件 API
from app.modules import get_features
print(get_features(bot_id=self.bot_id))
```

---

## 5. 已注册框架能力

| feature_id | 接管时行为 | 恢复时行为 |
|---|---|---|
| `proactive` | 关闭 `proactive_friend_enable` / `proactive_group_enable`，停止主动消息计时器 | 还原配置，重新武装计时器 |
| `schedule` | 关闭 `schedule_enable`，停止定时任务计时器 | 还原配置，重新武装任务 |
| `memory` | 关闭 `memory_enable` | 还原配置 |
| `knowledge` | 关闭 `knowledge_enable` | 还原配置 |
| `napcat_tools` | 关闭 `napcat_tools_enable` | 还原配置 |
| `agent` | 关闭 `AgentRuntime.config.enabled`，停止主动消息/定时任务计时器 | 恢复启用状态，重新武装计时器 |

这些控制器由框架在启动时注册，插件无需手动注册。

---

## 6. 多租约行为

同一能力可以被多个插件同时声明接管：

```text
插件 A 接管 proactive  → proactive 暂停
插件 B 接管 proactive  → proactive 继续暂停（B 只是加租约）
插件 A 卸载             → proactive 仍暂停（B 还在）
插件 B 卸载             → proactive 恢复
```

规则：

- 只要还有任一租约持有者，能力就保持禁用；
- 最后一个租约释放时，恢复“第一个租约接管前”的状态；
- 因此两个主动回复插件不会互相覆盖；
- 后启用者退出后，仍会回到前启用者或框架原始状态。

---

## 7. 使用建议

1. 优先使用 `supersedes` 声明式接管，少写生命周期代码；
2. 不要直接硬编码别的模块名/内部配置 key；
3. 如果只是“临时暂停”，请用 `features.suppress()` / `features.release()`；
4. 插件作者不应直接调用 `FeatureRegistry.register()`，框架能力由框架注册；
5. WebUI 模块卡片会显示“接管: xxx”，用于识别插件声明的能力冲突关系。

---

## 8. 配置与权限

- 接管只影响运行时行为，不修改插件的 `authority.enabled`；
- 接管会修改框架能力对应的配置值（如 `proactive_friend_enable`），但会保存快照并在释放时恢复；
- 已禁用插件不会在启动时误接管框架能力。

---

## 9. 其他参考

- 完整开发文档：[docs/MODULE_DEV.md](MODULE_DEV.md)
- 架构说明：[README.md](../README.md)
