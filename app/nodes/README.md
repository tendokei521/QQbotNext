> 本文只负责：说明 `app/nodes/` 放什么、不放什么。上层目录总览见 `docs/ARCHITECTURE.md`。

# `app/nodes/` —— 消息节点链

**一句话：消息进出框架的「管道」，一切皆节点。**

## 包含

| 文件 | 职责 |
|---|---|
| `base.py` | `MessageNode` / `MessageContext` / `NodeRunner` 核心概念 |
| `registry.py` | 节点注册表：入站链 / 出站链的注册、替换、查询 |
| `outbound.py` | 出站消息管道：Bot 发送前经过的节点链（拦截 / 改写 / 记录），末端是内置 `SendNode` |

## 两条链

- **入站链**：消息进入插件前的处理（权限、限流…），末端由 `ModuleInvokeNode` 派发业务；
- **出站链**：`bot.send_*` 之前的处理，末端是真正发出去的 `SendNode`。

内置业务节点（router / permission / invoke）在 `app/plugin/nodes.py`，不在这里。

## 边界

- 这里只有**框架机制**，不含任何具体业务判断。
- 具体插件想插入自己的节点 → 用 `NodeRegistry` 注册，不要改本目录。

> P5 计划把本目录并入 `app/plugin/`（它服务于插件派发），届时本 README 一并迁移。
