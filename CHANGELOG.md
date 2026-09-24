# Changelog

## [Unreleased]

### 新增：表情回应词表改为 QQ 系统表情全量映射

此前 `set_msg_emoji_like` 的 `reaction` 只有十来个手写的常见表情，模型想表达别的只能猜数字
（贴错**不可撤回**）。现在：

- 新增生成物 `app/llm/qq_faces.py`：QQ 客户端下发的系统表情表（`face_config.sysface`，
  296 个名字 → `QSid`）。它是 OneBot `face` 段 id 与表情回应 `emoji_id` 的同一空间；
- 新增 `scripts/export_napcat_faces.py`：从本机 NapCat 包导出/核对这张表
  （`--write` 重新生成、`--check` 升级后比对），**不再手抄网上的列表**——
  流传最广的那份把「微笑」记成 1，QQ 实际是 `撇嘴=1、微笑=14、爱心=66、赞=76`；
- `emoji_lexicon`：`DEFAULT_TAGS` 扩到全量表 + 口语别名（点个赞/狗头/问号/无语/加油/
  强→赞/弱→踩/大兵→悠闲），修正了 `吃瓜 4→271`、`比心 307→319`、`问号 263→32`
  这类错 id；单字名（困/茶/哦…）只认精确匹配（"很困难"不再被当成贴「困」）；
  数字名字（客户端表里的「666」）与真 id 区分开；
- `LARGE_FACES` 标出 `QSid>=222` 或带 `AniStickerType` 的大表情/动态表情
  （捂脸/吃瓜/比心/打call…），这批 id 在表情回应上的形态仍待真机校准；
- `set_msg_emoji_like` 的提示词（工具说明与 `reaction` 参数）改为全量词表的用法；
- 测试：`tests/test_onebot_emoji_like.py` 增加全量映射、真实 id 抽查、别名桥接、
  单字名精确匹配、数字名字、大表情标记等用例。

### 新增：显示名映射（句子型昵称不再进上下文）+ 一次判定缓存

线上案例：群成员把昵称写成「老师，今年的学费也是一次性交吗」，`expand_image` 的工具结果
直接把这整句话当人名回灌给模型（`sender.card or nickname` 没有过脱敏）。修法不是逐个调用点
打补丁，而是把「用户 id → 显示名」收成一个出口：

- 新增 `app/llm/display_names.py`：确定性层（明显句子/明显名字，零调用）+ 灰区一次廉价判定
  （只要 0/1）+ verdict 缓存（按 `(qq, 名字)` 键控，**改名即重判**）+ 单 bot 每小时 30 次闸门
  （超限**静默脱敏**）+ 后台预热（渲染同步、模型调用不进请求路径）
- 接入工具结果与引用行：`context_tools`（expand_image / expand_message / expand_recent /
  expand_user / 转发节点）、`enhance` 引用行；**底层 OneBot 工具一行不改**
- 失败一律倒向脱敏（无 key / 超时 / 回包不可用 / 闸门超限）→ 只会更还原，不会更不安全
- 测试：`tests/test_display_names.py` 19 条 + `test_llm_context_tools.py` 7 条回归
  （含线上那条原样昵称的用例）

### 改名：NapCat 工具 → OneBot 工具（旧配置键仍生效）

「OneBot」是协议名，NapCat 只是本项目使用的协议端实现之一，因此把四处对外命名统一到协议名：

- 包路径 `app/llm/napcat/` → `app/llm/onebot_tools/`；
  符号 `NAP_CAT_TOOLS` / `build_napcat_tools` → `ONEBOT_TOOLS` / `build_onebot_tools`；
  工具来源标识 `spec.source` 由 `"napcat"` 改为 `"onebot"`
- 配置键 `napcat_tools_*` / `napcat_tool_overrides` → `onebot_tools_*` / `onebot_tool_overrides`；
  **旧键仍会被读取**（新键优先，只做内存适配、不改写用户数据）——否则升级后
  `onebot_tools_enable` 回落默认 `False`，等于把用户已打开的开关静默关掉（见
  `tests/test_llm_config_legacy_keys.py`）
- Dashboard：路由 `/tools/napcat` → `/tools/onebot`、页面 `OnebotToolsPage.vue`、
  tab id 与 localStorage 字段名同步；展示文案 "NapCat Tools" → "OneBot Tools"
- 上游 API 文档地址（`napcat.apifox.cn`）**保持不变**：那是三方协议端的真实文档站，改了就是死链

### 重构：会话历史「基础信息 + 轮次信息」，查看过的内容留在历史里

历史此前是「一条冻结文本」：`enhance` 把「时间/群号/发送者/发送了」拼成字符串整条落库，
渲染有两套实现（会话历史 / 在线背景），且只有在线背景接「已展开登记」——第 1 轮展开展开的
被引用消息，第 2 轮又变回 `[引用456]` 占位。

- 新增 `app/llm/history_model.py`：`BaseInfo`（时间/发送者/群号/正文/原始段/附注行）
  + `TurnInfo`（轮次内取回的内容）+ `HistoryEntry` + **唯一渲染器** `render_history_entry`；
  四条渲染路径（会话历史、群聊背景、私聊背景、工具回读）收敛到它
- 写侧结构化：历史条目存 `base` 与原始消息段，正文与「时间/群号/发送者」分离，
  由渲染器按当前配置重排；保留 `message_id` 作为补全锚点；旧数据仍按原样渲染
- 新增 `app/llm/history_enrich.py`：持久补全登记（`data/llm/history_enrich.json`）。
  工具取回内容 → 本轮记账 → 请求收尾落库 → 下一轮渲染时把
  `[引用456]` / `【未展开:合并转发…】` 升级为 `【已展开:… → 摘要】`；
  工具取回的单条消息作为**附加块**排在对应历史之后（不混进用户正文）
- 历史图片不再只有占位：渲染为可展开标记 `[图片#1003]`，新增 `expand_image` 工具按需取回，
  取回的图作为「补全材料」随下一轮直接传给视觉模型；讨论焦点那张仍与既有路径一样直接同传
- 新配置：`history_background_enable`（默认开；关掉后会话历史成为唯一历史来源）
- 提示词补充 `【已展开】` / `[图片#id]` 的语义说明

### 重构：LLM 消息组装收敛为「块表 + 一次装配」

同一类请求此前在四处各自组装 messages（`chat.generate_response`、`chat.stream_response`、
`scheduler._build_messages`、主动消息内联），块顺序与取值口径各写一遍。现在：

- 新增 `app/llm/assembly.py`：`PromptRequest`（一次请求的全部输入）+ 声明式块表
  `BLOCKS`（人设 / 定时协议 / 主动性 / 格式说明 / 技能 / 记忆 / 背景 / 历史 / 本轮）
  + `PromptAssembler`。**想改顺序只改块表**，想加块只加一个 `build_xxx(req)` 函数
- 取值收敛为唯一入口 `chat.prepare_prompt`：会话、历史去重与渲染、上下文压缩、
  工具与技能、记忆召回、指代块、图片解析都在此完成；`generate_response` 与
  `stream_response` 只差 provider 调用方式
- 图片归位（`place_images`）移到清洗之前，模态清洗恢复为**最后一层兜底**；
  文本模型仍是 `[图片]` 占位，声明 `image` 模态的模型直接收到图块
- 统一三处不一致：记忆检索与意图判定一律用**用户原始正文**；历史去重与
  "刚追加的 user 消息"同源比较；上下文压缩在四条路径上一致生效
- 主动消息与定时任务获得与普通回复相同的块结构（此前缺「消息格式说明」），
  焦点行从手工拼接收进 `referent` 块
- 删除不可达的旧回复路径 `call_llm_and_reply` / `handle_group` / `handle_private`
  （约 150 行）、重复的 `_message_meta_instruction`、死参数 `schedule_nudge`
  与常量 `RECENT_SCHEDULE_NUDGE`；`chat.py` 1562 → 1285 行
- 新增 `describe(req)`：输出 `[{block, role, chars}]`，不开 debug 也能看清本轮发了什么

### 稳定性修复

- 修复 OneBot API 超时一律 10s 导致的假失败：改为按 action 分级（合并转发 60s、
  `get_forward_msg`/`get_image`/`get_record` 30s、群列表与历史 20s），
  且超时后迟到响应改为留痕日志（服务端可能已执行），不再静默丢弃
- 新增空回复重试 `empty_reply_retries`（默认 1 次，0 = 关闭）：请求成功结束却
  既无文本也无工具调用时同 provider 重试，避免偶现空回复把「暂时无法回答」发给用户；
  已有文本/工具产出时绝不重试（防止重复发送与工具重复执行），流式、非流式与
  主动消息/定时任务共用同一开关

### 工具主动性与聊天记录

- 新增系统工具 `get_chat_history`（取代 `get_session_history`）：默认零参数、自动定位当前会话，
  本地记录不足时自动补拉 QQ 聊天记录（`scope=auto/local/qq`），取不到时给明确路标
- `get_chat_history` 支持跨会话：私聊里用 `group_name`/`group_id` 查“发起人自己也是成员”的群
  （结果只回给发起人，`history_cross_query_enable` 可关）；群聊里查别的群、查别人的私聊一律拒绝
- 新增输出侧动作通道：模型可用 `[reply]` 引用当前消息、`[@QQ]` 真实 @ 某人
  （`outbound_directive_enable`，默认开启；仅本轮首句生效，指令不会漏给用户）
- 新增唯一一块「主动性」system 提示：按本轮可用工具裁剪，只讲时机不讲参数；
  命中历史意图时在同一块内补强「必须先查记录」
- 新增戳一戳节流 `poke_cooldown_seconds`（默认 20s，同会话同一人），失败不占冷却
- 修复 `fetch_*_online_history` 未解包 OneBot 响应信封（`data.messages`）导致
  群聊环境背景 / 私聊前历史 / 主动发言群背景长期静默失效的问题
- 修复工具以 `error:` 文本报错时被记为 `success=True`（1404 等接口错误此前不可见）
- 修复工具名与 OneBot action 不一致：删除伪工具 `get_msg_history`，
  为 `.ocr_image` / `.handle_quick_operation` 补 action 别名

### LLM 可扩展性优化（P0/P1）

- 新增 LLM 可观测性：`/agent/telemetry` 记录延迟 / token / provider / model / 工具 / 钩子耗时
- 会话历史从 JSON 目录扫描迁移到 SQLite 索引存储，旧 JSON 自动导入
- 新增会话级异步锁与 LlmPool 默认串行化，避免同会话并发请求/写历史
- Provider 增加能力协商与运行期注册；新增 Anthropic / Gemini 原生适配器
- 工具增加权限与作用域：`@tool(permission=..., scopes=[...])`
- 知识库检索可选启用 sqlite-vec ANN 后端，未安装时自动回退 SQLite 余弦扫描

### Agent 配置前端重构

- Agent 配置从通用 schema 表单升级为领域化页面
- 新增：概览 / 基础配置 / 模型 / 对话行为 / 流式回复 / 权限 / 知识库 / MCP / 定时任务与主动消息
- 新增共享 `agentConfig` Store，统一跨页草稿与自动保存
- 流式回复新增三档预设：快速（500–1000ms）、正常（1000–2000ms）、偏慢（3000–4000ms）

### 日志导出

- 设置页新增“导出日志”弹窗
- 日志列表放入固定滚动框，按时间段默认折叠展示
- 支持当前轮次与历史归档日志选择，并打包 ZIP 下载

### NapCat 工具

- 新增 `IBot.call_api` 通用 API 入口
- 新增数据驱动的 NapCat/OneBot 工具包：`app/llm/napcat/`
- Agent 新增 NapCat 工具配置页，可按风险等级/权限/作用域开关工具

## [2.0.0] - 2026-08-17

QQBot Next 2.0 首个正式 Release：基于 OneBot 协议的多账号 QQ 机器人框架，采用分层 + 插件架构。

### 架构与框架

- 核心重组为 `core → domain → infrastructure → modules/services → webui` 单向依赖分层
- 统一 DI 容器装配，生命周期集中在 `app/bootstrap.py`
- 类型化事件总线与统一后台任务管理（可追踪、级联取消）
- pydantic-settings 配置中心，SQLite 持久化并自动迁移旧 JSON 配置

### 全新 Dashboard

- Vue 3 + Vuetify 3 新版管理后台，`dashboard/dist` 已纳入版本库
- 模块分类 / 搜索 / 折叠 / 网格视图
- Agent 独立入口，账号管理与配置表单实时保存
- 日志面板支持简洁 / 原始双模式，与控制台输出同步
- 旧版 UI 保留为 `/legacy` 回退入口

### LLM Agent

- 模块可为 LLM 注册工具（`@tool`）与技能（`@skill`），携带 `ToolContext`
- 模块流水线钩子与 LLM 流水线钩子装饰器
- 流式输出、句子级发送、带 tools 的多轮工具循环
- 主动消息 / 定时任务流式发送支持
- 用户信息感知、回复打断、防抖合并等能力并入 `llm_enhance` 模块

### 插件与模块

- 新增群申请管理、戳一戳回复、今天吃什么等插件
- 防撤回模块按事件语义拆分入口，一事件一处理函数
- 权限系统重构为语义化角色 + 模块级过滤

### 稳定性与修复

- WebSocket 连接级失败断开处理
- 配置组件渲染问题修复，动态 / 重复列表支持 `string_list`
- 日志轮转与双视图稳定

### 测试

- 全量测试基线：`168 passed`（该数字为**当次发布时点**的快照，后续版本会增长，不代表当前基线；
  当前基线以 `pytest --collect-only -q` 实际输出为准）