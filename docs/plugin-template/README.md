# 示例插件

> 复制本目录到 `data/module/plugins/<name>/`（或 `app/plugins/<name>/`）后改名开始写插件。
> 完整规范见 [`docs/PLUGIN_STYLE.md`](../PLUGIN_STYLE.md)。

## 做什么

群聊/私聊消息命中关键词时回一句。演示「main.py 只接线、逻辑放 src/」的最小结构。

## 怎么配

| 配置项 | 说明 |
|---|---|
| 启用插件 | 关掉后不响应任何消息 |
| 触发关键词 | 消息里出现任一个就回复（忽略大小写） |

## 数据与权限

- 不落盘、不读外部数据；`permission = "everyone"`，任何成员都能触发。
- 若要写入数据，放 `data/module/data/<name>/`，并在启动时按需创建目录。

## 文件说明

- `main.py`：身份、配置、hook 接线与编排（≤150 行目标）。
- `config_schema.py`：WebUI 配置表单，键与 `default_config` 一一对应。
- `src/reply.py`：命中判断与文案拼装，纯函数，可单测。
