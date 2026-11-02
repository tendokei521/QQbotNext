# QQ-agent（1/QQ-agent-main）工具系统 · 配置全貌 · 记忆/人格管理 调研报告

> 调研对象：`C:\Users\Admin\Desktop\QQbotNext6\1\QQ-agent-main`（Node/Electron，纯 JS，ESM）。
> 调研方式：只读代码 + 文档，逐行核对。所有结论均给出 `文件:行号`。
> 版本特征：代码内含 2026-09-17 ~ 2026-09-26 的多次改版注释，本报告以**当前磁盘上的代码**为准；
> `doc/` 下部分文档（尤其 `prompt-pipeline.md`）已与代码漂移，末尾第 9 章列出。

---

## 0. 一句话总览

- 工具是「注册表 + 唯一可用性口径」架构：全部内置工具在 `src/tools.js` 里用 `registerTool()` 注册，可用性一律由 `src/tool-registry.js` 的 `getToolAvailability()` 计算（7 层），`orchestrator` 只问结论、不再自己拼条件。
- 提示词是「静态系统提示 + 每次全新用户消息」的无状态模型，**不携带任何 LLM 对话历史**；模型正文输出永不发到 QQ，发言必须走 `send_message` 等工具。
- 人格文本（`persona.roleText`）**不进系统提示**，而是拼进用户消息的【角色设定】段；系统提示只放行为准则 + `botName` + 参与度 + `customRules`。
- 记忆只有一层：**按群友的「印象」**（每会话一目录、每人一 JSON），无「话题/关系」分层。
- 本仓库**不含** `skills/` 与 `plugins/` 目录（`src/plugin-loader.js:41-42` 指向的目录都不存在），所以所有 Skill 能力钩子都是空操作 —— 这一点直接决定了「回复抢救」在本仓库里实际不生效（见 §8.1）。

---

## 1. 工具清单（28 个）

### 1.1 注册与可用性机制

`registerTool(def)` 会做参数校验并冻结默认值（`src/tool-registry.js:34-57`）：

```js
// src/tool-registry.js:40-42
if (!/^[a-zA-Z0-9_-]{1,64}$/.test(String(def.id))) {
  throw new Error(`工具 id 不符合 OpenAI 函数名规范 [a-zA-Z0-9_-]{1,64}：${def.id}`);
}
```

默认值：`category:'system'`, `icon:'🔧'`, `defaultEnabled:true`, `requiresVision:false`, `requiresSearch:false`, `skillId:null`, `requires:[]`（`src/tool-registry.js:43-56`）。

**"是否总是注册"的准确口径**：28 个内置工具全部**无条件注册**进 registry（`src/tools.js:157 registerAllTools()` 一次跑完，`src/tools.js:139-145 buildToolDefs()`）。只有 1 处 `defaultEnabled` 显式声明（`send_to`，值仍是 `true`，`src/tools.js:209`），其余全靠默认 `true`。**没有任何工具是"条件注册"的**——能不能用全在可用性层决定。

`getToolAvailability()` 的判定顺序（`src/tool-registry.js:126-212`，注释在 104-124）：

| 层 | 判据 | 代码 |
|---|---|---|
| 1 | `tools.enabled === false` → 拒绝 `tools-disabled` | `:149-151` |
| 2 | 所属 Skill 未生效 → `skillId` 拒绝 | `:154-159` |
| 3 | `requires` 声明的能力缺失 → `capability-missing` | `:162-170` |
| 4 | 分类开关 `tools.categories[cat] === false` → `category-disabled` | `:173-175` |
| 5 | 单工具 `tools.overrides[id] === false` → `tool-disabled` | `:178-182` |
| 5.5 | **`send_to` 专属**：`tools.crossChatSend !== true` → 直接不可见 | `:186-188` |
| 6 | `requiresVision && !visionEnabled` → `no-vision`；`requiresSearch && !searchEnabled` → `no-search` | `:191-196` |
| 7 | Skill `tool.guard` 附加否决 → `skill-guard` | `:199-209` |

运行时依赖的实参来自 orchestrator（`src/orchestrator.js:758-767`）：

```js
// src/orchestrator.js:758-760
const visionEnabled = cfg.api.vision !== false
  && modelImageVerdict(cfg.api.provider, cfg.api.model) !== 'no-vision';
const searchEnabled = cfg.webSearch?.enabled !== false;
```

即「勾了图片输入」AND「能力探测没判死」才放行看图工具。

### 1.2 工具总表

失败文案统一由 `err()` 生成 `错误：<msg>` + `isError:true`（`src/tools.js:100-102`）；成功由 `ok()` 把字符串或 `JSON.stringify(payload, null, 1)` 包成 `{content}`（`src/tools.js:96-98`）。

| # | id | name | 分类 | 参数（schema 要点） | 关键返回/失败文案 | 行号 |
|---|---|---|---|---|---|---|
| 1 | `send_message` | 发送消息 | messaging | `messages`(string\|string[]，必填)、`replyToMessageId`、`atUserId` | 成功 `{sent, messageIds, note}`；失败 `错误：消息内容为空`；note 内嵌「已发送。不要输出"已发送"类汇报，继续思考下一步或直接结束。」 | `tools.js:159-191` |
| 2 | `send_to` | 发送到指定会话 | messaging | `targetChatKey`(`group:123`/`private:456`，必填)、`messages` | 未开启：`错误：跨会话发送未开启（管理员可在 设置 → 工具与技能 里打开）。`；格式错：`targetChatKey 格式应为 group:群号 或 private:QQ号`；与当前会话相同：`目标 X 就是当前会话，直接用 send_message 即可。`；白名单外：`目标 X 不在白名单内，不能发送。` | `tools.js:193-254` |
| 3 | `send_sticker` | 发送表情 | messaging | `stickerId`(必填)、`replyToMessageId`、`atUserId` | `找不到表情 X，请先用 list_stickers 获取有效 id` / `表情 X 没有可发送的图片地址` / `表情 X 的本地图片已丢失（受控收藏目录里找不到它），已拒绝发送` / `表情 X 的图片地址不合法，已拒绝发送：…` | `tools.js:256-311` |
| 4 | `list_stickers` | 查看表情库 | sticker | `query`、`limit`(默认 24，钳到 1..100) | `{total, matched, truncated, stickers:[{id,desc,localNote,tags,useCount}]}` | `tools.js:313-334` |
| 5 | `get_sticker_image` | 查看表情图片 | sticker | `stickerId`，**requiresVision** | 多模态 parts；`找不到表情 X` / `该表情没有图片地址` | `tools.js:336-363` |
| 6 | `sticker_note` | 备注表情 | sticker | `stickerId`(必填)、`note`、`tags[]`、`usage` | `{updated, id, localNote, tags}` / `找不到表情 X` | `tools.js:365-390` |
| 7 | `collect_sticker` | 收藏表情 | sticker | `messageId`(必填)、`note` | `{collected, id, note, emojiId, qqSaved, localSaved, warning?}`；`在当前会话找不到消息 X。<midHint>` / `该消息没有可收藏的图片` | `tools.js:392-434` |
| 8 | `send_poke` | 拍一拍 | messaging | `targetUserId`(integer\|string) | 群聊缺参：`群聊拍一拍必须传 targetUserId（数字 QQ 号）。<memberHint>`；非数字：`targetUserId 必须是正整数的 QQ 号（收到：…）` | `tools.js:436-466` |
| 9 | `get_recent_messages` | 查看历史消息 | query | `limit`(默认 30/最大 100)、`offset` | `{count, messages:[{messageId,time,sender,senderId,text}]}`；offset 自动加 `session.pastStateCount` 补偿已读 | `tools.js:468-497` |
| 10 | `read_forward` | 展开转发消息 | query | `messageId`(必填) | 已是展开存档直接回；`转发内容为空或已被 QQ 服务端丢弃（发送时间太久）`；`展开失败：…` | `tools.js:499-531` |
| 11 | `get_active_members` | 查看活跃成员 | query | `limit`(默认 10/最大 20) | `{members:[{userId,name,lastSeen,recentCount}]}` | `tools.js:533-554` |
| 12 | `get_chats` | 查看可用会话 | query | `limit`(默认 20/最大 50) | `{count, chats:[{chatKey,lastActive,recentText(≤30)}]}` | `tools.js:556-581` |
| 13 | `get_message_detail` | 查看消息详情 | query | `messageId`(必填) | `当前会话找不到消息 X。<midHint>` | `tools.js:583-606` |
| 14 | `get_message_images` | 查看消息图片 | query | `messageId`，**requiresVision** | 多模态 parts；`图片获取失败：…`；无图时**成功**返回 `消息 X 没有可查看的图片` | `tools.js:608-646` |
| 15 | `memory_append` | 记录印象 | memory | `category`(enum `memberImpression`)、`userId`、`target`、`content` | `{saved:true, entry}`；`userId 必须是数字 QQ 号（收到：…）。先用 get_active_members 查准确 QQ 号再记。` | `tools.js:648-675` |
| 16 | `memory_query` | 查询印象 | memory | `userId`(可选) | `{memberImpression:[…]}` | `tools.js:677-697` |
| 17 | `memory_remove` | 删除印象 | memory | `category`(必填)、`userId`、`target`、`content` | `{removed:boolean}` | `tools.js:699-723` |
| 18 | `report_feedback` | 反馈问题 | system | `level`(enum info/warning/error)、`message`(必填) | `{reported:true}`（message 截 500 字） | `tools.js:725-745` |
| 19 | `web_search` | 联网搜索 | web，**requiresSearch** | `query` | 空结果**成功**返回 `{results:[], note:'没有搜到结果，试试换关键词或更具体的说法。'}`；异常 `搜索失败：…` | `tools.js:747-770` |
| 20 | `web_fetch` | 抓取网页 | web，**requiresSearch** | `url` | `{url,statusCode,truncated,content(≤20000 字)}`；`抓取失败：…` | `tools.js:772-800` |
| 21 | `finish` | 结束会话 | system | `summary`(必填)、`activeTopic`(可选) | `{finished:true}`；写 `session.finishReason`(≤300) 与 `session.activeTopic`(≤60) | `tools.js:802-824` |
| 22 | `set_reminder` | 设置提醒 | system | `text`(必填)、`delayMinutes`、`atTime` | `提醒内容为空` / `时间格式不对：用 "HH:MM"…` / `这个时间已经过了，请给个未来的时间` / `请提供 delayMinutes…或 atTime…之一` / `提醒服务未启用` | `tools.js:827-861` |
| 23 | `list_reminders` | 查看提醒 | system | 无参数 | `{count:0, note:'当前没有待触发的提醒。'}` | `tools.js:863-881` |
| 24 | `cancel_reminder` | 取消提醒 | system | `id`(必填) | `没找到提醒 X（可能已触发或 id 不对，用 list_reminders 查一下）` | `tools.js:883-901` |
| 25 | `check_holiday` | 查询节日 | system | `days`(默认 7，0=只看今天) | `{today,upcoming,note}`，note 如 `今天是X！可以自然地送上祝福（参考：…）。` | `tools.js:904-936` |
| 26 | `send_image` | 发送网图 | media | `url`(必填)、`preview`、`note`、`replyToMessageId`、`atUserId` | 见 §1.3 | `tools.js:944-1065` |
| 27 | `search_images` | 搜索网图 | web，**requiresSearch** | `query`(必填)、`limit`(默认 8/最大 12) | `{query,results:[{title,url}],note}`；空结果 note：`没搜到图。换个更具体的关键词再试一次（最多搜 3 次）。` | `tools.js:1067-1113` |
| 28 | `read_video` | 读取视频 | query | `messageId`(必填)、`frames`(1~12) | `该消息没有视频（媒体里没有 video 段）` / `视频读取服务未启用`；返回按 `info.route` 分 native(video_url) / frames(image_url×N) / 仅元信息 | `tools.js:1116-1186` |

分类元数据（设置页用，8 类）：`messaging / sticker / query / memory / web / knowledge / media / system`（`src/tool-registry.js:235-244`）。注意 `knowledge` 类**当前没有任何内置工具**（知识库 Skill 未随仓库分发）。

### 1.3 「发言」相关工具的细节（重点）

**`send_message`**（`src/tools.js:159-191`）—— 唯一的正常发言出口：

```js
// src/tools.js:176-186
const messages = normalizeMessageList(args.messages);
if (!messages.length) return err('消息内容为空');
const result = await ctx.sender.sendTextBatch(ctx.chatKey, messages, {
  replyToMessageId: args.replyToMessageId ?? null,
  atUserId: args.atUserId ?? null
});
ctx.session.sent.push(...result.sent.map((s) => ({ type: 'text', text: s.text, at: s.at })));
ctx.emit('session-update', ctx.session.id);
const note = ['已发送。不要输出"已发送"类汇报，继续思考下一步或直接结束。'];
```

- 数组 = 分多条发送；发送侧会先跑 `mdToPlain` 再按 `send.hardSplitAt` 切分（`src/sender.js:141-151`）。
- 部分失败不抛错，而是把失败原因拼进 `note`（`src/tools.js:185`）。
- 发送侧还有去重窗口 8s（`src/sender.js:88-120`，`send.dedupeWindowMs`），重复文本会被静默跳过并返回 `deduped:true`。

**`send_to`**（跨会话发言）——「描述动态化 + 可用性硬拦 + 三道硬校验」：

```js
// src/tools.js:221-241（execute 内）
if (getConfig().tools?.crossChatSend !== true) return err('跨会话发送未开启（管理员可在 设置 → 工具与技能 里打开）。');
const wantTarget = String(args.targetChatKey ?? '').trim();
if (!/^(group|private):\d+$/.test(wantTarget)) return err('targetChatKey 格式应为 group:群号 或 private:QQ号');
if (wantTarget === ctx.chatKey) return err(`目标 ${wantTarget} 就是当前会话，直接用 send_message 即可。`);
...
const allowedAll = allowList.length === 0 && getConfig().allowAllWhenEmpty === true;
if (!(allowList.map(String).includes(tId) || allowedAll)) return err(`目标 ${wantTarget} 不在白名单内，不能发送。`);
```

注意描述本身也随开关变（`src/tools.js:198-204`），关闭时明确写「当前未开启」；但真正的边界在 `getToolAvailability` 第 5.5 层（`src/tool-registry.js:186-188`）——**关闭时模型连工具都看不见**。

**`send_sticker`** —— 一条消息只能一张、不能带文字（描述 `src/tools.js:259`）；发送前做「可发地址预检」`ensureSendable`（`src/tools.js:278-280`）与本地路径白名单校验（`src/tools.js:284-298`）。

**`finish`（结束）**：

```js
// src/tools.js:816-823
async execute(ctx, args) {
  ctx.session.finishReason = String(args.summary ?? '').slice(0, 300);
  const topic = String(args.activeTopic ?? '').trim();
  if (topic) ctx.session.activeTopic = topic.slice(0, 60);
  return ok({ finished: true });
}
```

描述里写死了两条语义：不说话时调它（summary 写理由）；活跃期判断话题结束也调它（reason 填"话题结束"）。真正的"结束本轮"由 orchestrator 侧把 `finish = true` 置位并处理活跃期状态机（`src/orchestrator.js:1193-1221`）。**不调 `finish` 也能结束**：模型没有工具调用就跳出循环（`src/orchestrator.js:1086-1094`，注释原话「模型真的说完了（文本只是思考，按设计不发 QQ）→ 正常结束」）。

### 1.4 工具执行入口

```js
// src/tools.js:1229-1245
export async function executeTool(defs, ctx, name, argsJson) {
  const def = defs.find((d) => d.id === name || d.name === name);
  if (!def) return { content: `错误：未知工具 ${name}`, isError: true };
  ...
  try { args = typeof raw === 'string' ? JSON.parse(raw) : raw; }
  catch { return { content: `错误：工具 ${name} 的参数不是合法 JSON：${String(raw).slice(0, 200)}`, isError: true }; }
```

工具名兼容 `id`（英文）与中文 `name` 两种写法；参数 JSON 解析失败不抛错，回一条错误文案给模型。

---

## 2. 工具调用的协议细节

### 2.1 结果怎么回填给模型：`role:"tool"`，媒体另走一条 `role:"user"`

```js
// src/orchestrator.js:1163-1174
let contentStr = '';
let media = [];
if (Array.isArray(result.content)) {
  contentStr = result.content.filter((p) => p.type === 'text').map((p) => p.text).join('\n');
  media = result.content.filter((p) => p.type === 'image_url' || p.type === 'video_url');
} else {
  contentStr = String(result.content);
}
toolResults.push({ role: 'tool', tool_call_id: call.id, name, content: contentStr, isError: !!result.isError });
session.messages.push({ toolCall: { name, args: safeParse(argsRaw), result: contentStr.slice(0, 2000), isError: !!result.isError } });
```

- **文本**：拼文本进 `role:"tool"` 消息，**不追加任何"下一步指令"**（`isError` 只用于 UI/日志，不改变发给模型的 content）。
- **媒体**：绝不塞进 tool 消息，改发一条 `role:"user"` 多模态消息（`src/orchestrator.js:1175-1190`）：

```js
// src/orchestrator.js:1182-1188
imageUserMessages.push({
  role: 'user',
  content: [
    { type: 'text', text: `[系统：以下是工具 ${name} 返回的 ${what}，请直接"看"了回应]` },
    ...media
  ]
});
```

- 顺序：先批量 push 全部 tool 结果，再 push 媒体 user 消息（`src/orchestrator.js:1252-1254`），注释说明「OpenAI 校验要求每个 tool_call 都有对应 tool 消息」。
- **只有这一处**会给对话里追加"系统提醒"式指令：最后 3 轮还没发过言时（见 §2.4）。

### 2.2 单轮上限、循环与截断

- 轮数上限：`const maxRounds = Math.max(1, Number(cfg.api.maxRounds) || 12);`（`src/orchestrator.js:923`），默认 `12`（`src/config.js:64`），UI 范围 1~40（`ui/app/06-settings-render.js:845`）。
- 循环：`for (let round = 0; round < maxRounds && !finish; round++)`（`src/orchestrator.js:933`）。
- 一批工具调用内的**每个 call 都跑**（`for (const call of toolCalls)`，`:1128`），没有单轮调用数上限。
- **发给模型的工具结果本身不截断**（`:1173` 用的是完整 `contentStr`）；截断只发生在：
  - 工具自己内部（`web_fetch` 的 20000 字：`src/tools.js:793-794`；`get_recent_messages` 最大 100 条；`list_stickers` 最大 100；`formatStickerList` 上限 500：`src/stickers.js:132`）；
  - `session.messages`（UI 展示副本）截 2000 字（`src/orchestrator.js:1174`）。

### 2.3 失败重试

- **单次请求内**：`chatCompletionWithRetry(args, retries = 2)`（`src/llm.js:210`），每模型最多 3 次，退避 1s→2s（`:234`），只重试 `isRetryableError` 判定的错误（网络/超时/5xx/429/JSON 解析失败，`src/llm.js:166-191`；4xx 与 abort 不重试，`:173`）。
- **备选模型降级**：主模型重试全失败后按 `api.fallbackModels` 顺序换模型/换 provider（`src/llm.js:129-151`、`:214-233`）。
- **工具失败不重试**：工具返回 `isError` 只是把错误文案交给模型，由模型自己决定再调一次。唯一例外是 Skill 的 `before-tool` 否决也走同一形状（`src/orchestrator.js:1147-1149`）。
- **会话级重试**：整轮失败且**一条消息都没发出**时，2s/4s 退避后清空上下文从头再来（`src/orchestrator.js:585-611`），次数 `sessionRetryAttempts`（默认 2 → 最多 3 次尝试）。发过消息绝不重试（防重复发言，注释 `:581-583`）。

### 2.4 追加进对话的两类"非工具"消息

```js
// src/orchestrator.js:987-994
const roundsLeft = maxRounds - round;
if (roundsLeft <= 3 && roundsLeft > 0 && session.sent.length === 0
    && messages[messages.length - 1]?.role !== 'system') {
  messages.push({
    role: 'user',
    content: `【系统提醒】工具调用轮次只剩 ${roundsLeft} 轮。如果你打算回应本次消息，请立刻调用 send_message 要把说的话发出去，不要再调用其它工具 —— 轮次耗尽后你将没有机会发言，群友会收不到任何内容。`
  });
}
```

（原文为「把要说的话发出去」。）另一类是 inline tool call 的修复（§2.5）。

### 2.5 模型把工具调用写在正文里

核心自带解析器 `parseInlineToolCalls()` 支持三种写法（`src/orchestrator.js:1930-1984`）：

1. `<tool_call><function=send_message><parameter=messages>…</parameter></function></tool_call>`
2. `<tool_call>{"name":"send_message","arguments":{…}}</tool_call>`
3. `<tool_call>send_message\n{"messages":"…"}</tool_call>`

解析成功后会把最后一条 assistant 消息**改写**成 `content:null + tool_calls`（`src/orchestrator.js:1064-1085`），否则下一轮请求消息序列非法。注意 `reply.inline-calls`（插件扩展解析器）在本仓库为空操作。

---

## 3. 配置项全貌

### 3.1 结构约定

- 文件：`data/config.json`（多实例为 `data-<N>/`），`loadConfig()` 深合并默认值（`src/config.js:407-433`），`updateConfig()` 写盘并**用滑条位置权威反算档位**（`:611-619`）。
- 坏配置不静默回默认：改名备份为 `config.json.corrupt-<时间戳>` 后再回落（`:413-432`）。
- 废弃字段在载入/保存时统一剔除（`OBSOLETE_PATHS`，`:505-537`）。

### 3.2 分组清单

> 「改行为？」列 = 该键是否会改变模型看到的内容或输出行为。

#### A. `api`（模型 API）`src/config.js:29-90`

| 键 | 默认 | 范围/取值 | UI 名称 | 改行为？ |
|---|---|---|---|---|
| `baseUrl` / `apiKey` / `model` / `provider` | `''` | 自由 | 模型配置弹窗 | 间接（换模型=换行为） |
| `fallbackModels` | `[]` | `[{model, provider?}]` | 「备选模型（故障自动降级）」（`06-settings-render.js:209-220`） | 间接 |
| `visionModel` | `''` | 模型 id | 「图片输入专用模型（留空用主模型）」（`08-modals.js:299`） | **是**（带图请求切模型） |
| `videoModel` | `''` | 模型 id | 「视频输入专用模型」（`08-modals.js:304`） | **是** |
| `videoMode` | `'auto'` | `auto/native/frames/off` | 「视频理解方式」（`08-modals.js:310-317`） | **是**（决定喂不喂画面） |
| `vision` | `true` | bool | 「图片输入（关闭则移除看图工具）」（`08-modals.js:293-295`） | **是**（移除 3 个工具 + 改写场景规则） |
| `video` | `false` | bool | 「视频输入（GIF 转视频发给模型）」（`08-modals.js:296-297`） | **是**（GIF 选路） |
| `temperature` | `0.8` | 0~2 | 「温度」（`06:844`） | **是** |
| `maxRounds` | `12` | 1~40 | 「单次运行最大工具轮数」（`06:845`） | **是** |
| `timeoutMs` | `180000` | ≥5000 | 无 UI | 否（只影响失败率） |
| `thinkingMode` | `'auto'` | `off/auto/on`（旧三态） | 与 effort 合成一个下拉（`06:847-868`） | **是** |
| `thinkingEffort` | `''` | `''/off/low/medium/high/xhigh/max` | 「思考强度」（同上） | **是** |
| `thinkingBudget` | `0` | token 数，0=不指定 | **无 UI**（`config.js:74-77` 注释：有意不给入口，手改 json） | **是**（Claude 兼容/部分网关） |
| `priceInputPerM` / `priceOutputPerM` / `priceCachedPerM` / `useOfficialPrice` / `modelPrices` | 0 / true / `{}` | — | 成本核算卡 | 否（只做本地估算） |

#### B. `webSearch`（搜索）`src/config.js:98-150`

| 键 | 默认 | UI | 改行为？ |
|---|---|---|---|
| `enabled` | `true` | 「联网搜索：启用 web_search / web_fetch 工具」（`06:285-286`） | **是**（移除 3 个工具 + 场景规则改写成"你没有联网能力"） |
| `searchUrl` | `https://cn.bing.com/search` | 「搜索地址（高级）」 | 是（换引擎） |
| `maxResults` | `6` | Bing 分支无 UI | 是（结果条数，Bing 硬钳 1~10：`web-search.js:102`） |
| `provider` | `'bing'` | 「搜索提供方」（bing/deepseek/zhipu/bocha/baidu/metaso/custom:<id>） | 是 |
| `deepseek.*` / `zhipu.*` / `bocha.*` / `baidu.*` / `metaso.*` / `providers[]` / `custom` | 见 `config.js:104-149` | 各提供方区块 | 是（key/模型/引擎/条数） |

#### C. `security`（安全例外）`src/config.js:152-163`

`allowPrivateImageHosts`(默认 `false`)、`browseLock.enabled`(`false`)、`browseLock.hosts`(`[]`)、`browseLock.siteSearchUrl`(`''`)。
**改行为**：`browseLock` 开启后 `web_fetch`/`send_image`/`search_images` 全部逐跳校验白名单（`tools.js:788`、`:998`、`:1100`），不在白名单的抓取直接失败 → 属"会改变模型能做什么"的隐式开关。

#### D. `persona`（人设与行为）`src/config.js:176-199`

| 键 | 默认 | UI 名称 | 改行为？ |
|---|---|---|---|
| `botName` | `'小鲸鱼'` | 「机器人名字」（`06:426`） | **是**（进系统提示身份句 + @ 判定） |
| `selfNickname` | `''` | 「群内展示名（可选）」（`06:427`） | 是（@ 判定 / 标签） |
| `roleText` | `PERSONAS.xiaojingyu.text` | 「角色设定」（`06:435-436`） | **是**（进用户消息【角色设定】） |
| `participation` | `'medium'` | 「参与度：安静型/普通群友/活跃型」（`06:428-433`） | **是**（改写系统提示【该说/不该说】首行） |
| `customRules` | `''` | 「管理员附加规则（可选，追加到系统提示）」 | **是** |
| `systemPromptOverride` | `''` | 「系统提示词覆盖（高级 · 可选）」+ 黄色警示（`06:439-446`） | **是**（整体替换行为准则，仅保留安全规则+工具协议） |

#### E. 人设库与作用域

`customPersonas[]`(`:189`)、`personaByChat{}`(`:194`)、`personaUnified`(`true`, `:199`)。
UI：「为所有的群聊/私聊使用同一个人设」（`06:461-462`）；关闭后出现分会话按钮网格，`●` = 已配置独立人设（`06:449-457`）。

#### F. 接入与运行节奏 `src/config.js:201-232`

| 键 | 默认 | UI | 改行为？ |
|---|---|---|---|
| `allow.groups` / `allow.private` / `deny.*` / `allowAllWhenEmpty` | `[]` / `[]` / `false` | 「聊天白名单」 | 是（决定谁进来） |
| `wakeDelayMs` | `2000` | 「防抖聚批窗口（毫秒）」（`06:870`） | 间接（决定批大小→提示词内容） |
| `drainDelayMs` | `1200` | 「批次间隔（毫秒）」（`06:871`） | 间接 |
| `maxConcurrentRuns` | `2` | 「同时处理几个会话」（`06:874`，1~8） | 否（并发度） |
| `sessionRetryAttempts` | `2` | 「失败自动重试次数」（`06:875-876`，0~5，hint「0 = 关闭。已发出过消息的会话绝不自动重试。」） | 间接 |
| `send.minGapMs` / `maxGapMs` / `byLengthMs` / `maxPerMinute` / `maxPerHour` / `hardSplitAt` / `dedupeWindowMs` | 1000 / 3000 / 20 / 80 / 500 / 4000 / 8000 | 「信息发送配置」卡（`06:879-885`） | 否（发送侧） |
| `proactive.enabled` | `false` | 「冷场时按概率主动开话题」（`06:932-933`） | **是**（凭空产生一次运行） |
| `proactive.checkIntervalMinMs/MaxMs` | `1800000/5400000` | 「检查间隔下限/上限（毫秒）」 | 否 |
| `proactive.idleThresholdMs` | `1800000` | **无 UI** | 是（冷场判定） |
| `proactive.probability` | `0.25` | 「触发概率 0~1」 | 是 |

#### G. 活跃模式 `chatActive` `src/config.js:237-240`

| 键 | 默认 | UI | 改行为？ |
|---|---|---|---|
| `enabled` | `false` | 「被召唤后可持续参与话题（1/2/3 档触发时生效）」（`ui/app/09-community.js:645-646`） | **是**（注入【活跃模式】段 + 覆盖档位判定） |
| `ttlMinutes` | `30` | 「活跃期最长持续（分钟，到期自动潜水）」（`09:647-648`，1~240） | 是 |

#### H. 表情包 `sticker` `src/config.js:242-251`

| 键 | 默认 | UI | 改行为？ |
|---|---|---|---|
| `enabled` | `true` | 「启用表情包（收藏表情同步 + 发送工具）」（`06:947-948`） | **是**（移除【可用表情包】目录段 + 停止同步；**但不禁用工具**，见 §6.4） |
| `promptMaxStickers` | `10` | 「提示词里列举的表情包数量（1~50）」（`06:960-966`） | **是** |
| `encourage` | `1` | 「发表情包的积极程度」0~3（`STICKER_LEVELS`：`06:707-712`） | **是**（改写系统提示频率行） |
| `collectEnabled` | `true` | 无单独 UI（在表情包卡语境下） | 是（`collect_sticker` 直接抛 `收藏表情功能未开启`：`sticker-manager.js:213`） |
| `maxCollectPerHour` | `10` | 同上 | 是（限频 `sticker-manager.js:216-219`） |

#### I. `store`（响应档位 / 上下文 / 锚点）`src/config.js:253-299`

| 键 | 默认 | UI | 改行为？ |
|---|---|---|---|
| `maxMessagesPerChat` | `0`（不限） | 无 UI | 否（存储） |
| `contextTier` | `4` | 滑条派生（**不要手改**，由 `contextSliderPos` 反算） | **是** |
| `contextSliderPos` | **无默认值**（缺失→`clampSliderPos` 返回 100 → 4 档） | 主滑条 | **是** |
| `historyCount` | `80` | 「响应时随【已读信息】带多少条历史（各档位相同）」（`09:632-635`，1~500） | **是** |
| `keywords` | `[]` | 「② 的关键词表（每行一个，不区分大小写）」（`09:642-643`） | **是** |
| `randomPercent` | `10` | 滑条 3 档段派生 | **是** |
| `promptAnchor.enabled` | `true` | 「复用上一轮的记忆+已读前缀，只追加新内容（推荐开启，缓存命中大幅省 token）」（`09:637-638`） | **是**（改变提示词分段结构） |
| `promptAnchor.maxExtraRead` | `5` | 「最多额外追加 N 条已读，超出则整体重置窗口」（`09:639-641`，1~50） | **是** |
| `unifiedTier` | `true` | 「统一设置」开关（`06:902`） | 是 |
| `groupSliderPos` / `groupPeakPos` | `{}` | 分群滑条 | 是 |
| `keepSessionFiles` | `0` | 无 UI | 否 |
| `peakSchedule.enabled` | `false` | 「峰谷时段」开关（`06:899`） | **是**（按时段覆盖档位） |
| `peakSchedule.peak.start/end` | `'09:00'/'18:00'` | 峰谷时段弹窗 | 是 |
| `peakSchedule.peak.sliderPos` | `10` | 高峰档位 | 是 |
| `peakSchedule.valley.sliderPos` | `100` | 低谷档位 | 是 |

档位换算（`src/tier-slider.js:18-41`）：滑条 `0~10`→1 档（仅艾特）；`10~20`→2 档（+关键词）；`20~90`→3 档（+随机，`prob=(pos-20)/70*100`，一位小数）；`90~100`→4 档（全响应）。UI 一句话说明见 `ui/app/06-settings-render.js:804-815`。

#### J. `commandMute`（指令禁言）`src/config.js:316-322`

| 键 | 默认 | UI | 改行为？ |
|---|---|---|---|
| `enabled` | `true` | 「启用指令禁言」（`06:921-922`） | **是**（触发后该群档位强制 1 档） |
| `command` | `'/安静'` | 「禁言指令（需与 @机器人 同条消息）」（`06:916-917`） | 是 |
| `durationMin` | `30` | 「禁言时长（分钟，0 = 直到手动解除）」（`06:918-919`） | 是 |
| `active` | `{}` | 运行态，不在 UI 编辑 | 是 |

触发逻辑在消息入口：`src/app.js:924-950`（要求同条消息既 @ 到机器人又含指令，2026-09-26 起用 @ 段标记判 QQ 号防重名误判）；生效在 `storeConfigForChat()` 最前面（`src/config.js:684-688` → `commandMuteTier()` `:736-746`）。

#### K. `memory`（记忆整理）`src/config.js:324-330`

| 键 | 默认 | UI | 改行为？ |
|---|---|---|---|
| `consolidateEnabled` | `true` | 「启用记忆自动整理」（`06:393-394`） | **是**（后台会调模型改写印象） |
| `useChatModel` | `true` | 「使用与聊天机器人相同的模型」（`06:395-396`） | 间接 |
| `provider` / `model` | `''` | 「记忆整理模型（点击选择）」（`06:398-405`） | 间接 |
| `consolidateMinIntervalMs` | `21600000`（6h，代码再钳 ≥30min） | 「整理冷却时间（毫秒）」+ hint（`06:407-408`） | 是 |
| `consolidateMinImpressions` | **无默认值，代码兜底 4**（`orchestrator.js:1450,1468-1469`） | 无 UI | 是 |
| `maxImpressionsPerMember` | **无默认值，代码兜底 5**（`orchestrator.js:1475,1750`） | 无 UI | 是（也是"某成员超载即触发整理"的阈值） |
| `discoverMinMessages` / `discoverMaxMembers` | **无默认值，兜底 20 / 3**（`orchestrator.js:1455-1456,1561-1564`） | 无 UI | 是（决定"发现新人"规模） |

#### L. `tools` / `skills` / `extensions`（工具与技能）`src/config.js:339-366`

| 键 | 默认 | UI | 改行为？ |
|---|---|---|---|
| `tools.enabled` | `true` | 「启用工具系统（关闭后机器人只能看不能做任何操作）」（`06:1648`） | **是**（全部工具消失） |
| `tools.categories.{8 类}` | 全 `true` | 分类开关（`06:1616-1627`） | **是** |
| `tools.overrides{}` | `{}` | 单工具开关（`06:1582-1612`，send_to 的开关就是 `crossChatSend`：`:1588-1591`） | **是** |
| `tools.crossChatSend` | `false` | 收编进 send_to 工具卡的开关 | **是**（工具级不可见） |
| `skills{}` | `{}` | 技能页 | 是（无 Skill 时为常空） |
| `extensions.hotReload` | `true` | 无 UI | 是（放进去即执行 JS） |

#### M. 其它（不改模型行为）

`server.*`（`port`/`token`/`autoStart`/`closeToTray`，`:368-375`）、`ui.*`（`theme`/`showVision`/`refreshMs`，`:376-382`）、`blocklist{}` / `globalBlocklist[]` / `communityExemptions[]`（`:304-313`）、`providers[]` / `dshProviderKeys{}` / `providersSourceYaml`（`:92-94`）。
其中 `blocklist` 有一处**隐性改行为**：被屏蔽者的历史消息也会被过滤出【已读信息】（`src/prompt.js:529-538`）。

#### N. 读代码存在、`DEFAULT_CONFIG` 里没有的键（"隐式配置"）

`store.contextSliderPos`、`memberNotes{}`（群友备注，UI 在记忆页编辑，`ui/app/05-memory-settings.js:373-406`）、`modelVision{}`（视觉能力探测结果，`src/vision-scan.js:28-44`）、`memory.consolidateMinImpressions` / `maxImpressionsPerMember` / `discoverMinMessages` / `discoverMaxMembers`。
这些键**只从磁盘/运行时写入**，缺省值各自在代码里兜底 —— 手改 `config.json` 可以直接生效。

### 3.3 「哪些开关会改变模型行为」速查

**会直接改提示词文本**：`persona.botName` / `participation` / `customRules` / `systemPromptOverride` / `personaByChat`、`sticker.encourage`、`sticker.enabled`、`sticker.promptMaxStickers`、`api.vision`、`webSearch.enabled`、`store.historyCount`、`store.keywords`、`store.promptAnchor.*`、`chatActive.enabled`、`memberNotes`。
**会改工具列表（模型能看见什么）**：`tools.enabled`、`tools.categories.*`、`tools.overrides.*`、`tools.crossChatSend`、`api.vision`（+探测结论）、`webSearch.enabled`、`skills.*`。
**会改运行触发（模型何时被叫起来）**：滑条 `contextSliderPos` / `peakSchedule` / `groupSliderPos` / `commandMute` / `chatActive` / `proactive` / 白名单与屏蔽名单。
**会话内不可见但改结果**：`api.temperature`、`api.maxRounds`、`api.thinkingEffort/Budget`、`api.videoMode`、`send.*`（分条/去重/切分影响实际发出内容）。

---

## 4. 人格如何进入 prompt

### 4.1 人设的数据结构

内置人设库只有一个条目（`src/personas.js:4-5`）：

```js
export const PERSONAS = {
  xiaojingyu: { name: '小鲸鱼（默认）', text: `# 角色卡：DeepSeek 小鲸鱼 —— QQ 群友版 …` }
};
```

**没有结构化的"说话风格/口癖/禁忌"字段**：只有 `name`（展示名）+ `text`（一整段 Markdown 角色卡）。用户自定义人设存 `customPersonas[]`，仓库里没有其内部 schema 的强校验（UI 侧按 `{id,name,text,customRules}` 使用，见 `ui/app/06-settings-render.js:515-540` 与 `/api/persona-templates` 路由 `src/routes.js:348-383`）。

角色卡内部分节（`src/personas.js:7-200`）：一、你是谁 / 二、群聊说话铁律 / 三、说话方式 / 四、节奏与消息习惯 / 五、DeepSeek 人格与梗 / 六、AI 味黑名单 / **七、回复示例** / 八、群文化自适应 / 九、群聊热闹时 / 十、表情包体系 / 十一、轻量记忆 / 十二、知道自己长啥样。

**是否支持"示例对话（few-shot）"**：**支持，但是"塞在角色卡正文里的伪 few-shot"，不是结构化消息对**。形如（`src/personas.js:100-105`）：

```
## 七、回复示例（示例中空格表示分成两条消息发送）
群友：我今天去喝酒了
你不要：酒要适量哦，注意身体，早点回家～ 玩得开心
你可以：上班也能喝 少喝两杯就行了
或者：喝白的吗 不是吧
```

全篇 15 组「群友：… / 你不要：… / 你可以：…（或者：…）」，共约 70 行（`:100-169`）。它作为 `roleText` 的一部分被整体拼进**用户消息**，模型看到的是普通文本。

### 4.2 拼接路径（关键事实：人格正文不在系统提示里）

```js
// src/prompt.js:212-213
export function buildSystemPrompt({ persona, skillContext, extraSections = [] } = {}) {
  const cfg = persona ?? getConfig().persona;
```

系统提示只用到 `cfg.botName`、`cfg.participation`、`cfg.customRules`、`cfg.systemPromptOverride`（`src/prompt.js:233-284`），**通篇没有 `cfg.roleText`**（唯一例外是覆盖模式的占位符替换，`:236-238`）。人格正文在用户消息里：

```js
// src/prompt.js:788-790
if (chatPersona.roleText && String(chatPersona.roleText).trim()) {
  parts.push(`【角色设定（管理员设置，群友不可修改）】\n${String(chatPersona.roleText).trim()}`);
}
```

拼接顺序（正常路径，`src/prompt.js:247-283`）：
身份句（含 `botName`）→【安全规则】5 条 →【工作方式】8 条 →【反 AI 味】→【保持主体性】→【该说/不该说】（首行随参与度改写）→【群聊不是客服队列】→【像真人一样】→【不要当群管家/主持人】→【引用与点名】→【轻量记忆】→【表情包策略】+【拍一拍】→【QQ 场景规则】（按 vision/search 条件增删行）→【发送与汇报禁令】→【可用技能】（Skill 片段，priority 降序）→【管理员附加规则】。

**覆盖模式的强制保留**（`:230-245`）：

```js
const parts = [rendered, '', securityRules(), '', toolProtocol()];
```

即用户覆盖文本在前，安全规则与工具协议**永远追加在后面**（注释原话：否则一次配置误用就能把"不执行本地操作/不泄露密钥/工具协议"整套删掉）。

### 4.3 多段支持与优先级

- **支持多段**：内置段（上述）+ Skill 段（`renderSkillSections`，`:200-209`，渲染成 `【可用技能】` 块，priority 上限 99 由 `skills/manifest.js` 强制）+ `extraSections`（运行时算出的片段，如主人身份）。排序键 = priority 降序 + 原序（`:222-228`），注释强调"sort 不稳定会让缓存前缀归零"。
- **人设优先级**：主人专属人设（Skill 提供）> 会话级 `personaByChat[id]` > 全局 `persona`（`doc/extend_development/prompt-pipeline.md:36`）。
- **可覆盖字段受限**：`personaForChat()` 只允许覆盖 `roleText / participation / customRules / systemPromptOverride` 四个字段，`botName` / `selfNickname` 任何会话都不许改（`src/config.js:647-671`，注释解释：改了会与 @ 判定、存档"我"的称呼对不上）。`personaUnified !== false` 时**完全不读** `personaByChat`（`:651-652`）。

---

## 5. 记忆体系

### 5.1 存什么、存哪里

```
data/memory/group_<群号>/<QQ>.json
data/memory/private_<QQ>/<QQ>.json
data/memory/<会话>/_meta.json          // { lastConsolidatedAt }
data/memory/backups/…                  // 整理前的备份
```

（`src/memory.js:1-6`、`:13-40`）

单个成员文件结构（`src/memory.js:63-69`）：

```js
{
  userId, name,
  impressions: [{ content, createdAt }],
  updatedAt, lastConsolidatedAt
}
```

**只有一层分类**：`memberImpression`（群友印象）。`append()` 对其它 category 直接返回 `null`（`src/memory.js:223-229`），工具层 enum 也只允许 `memberImpression`（`src/tools.js:657`）。旧版的 `activeTopic` / `pendingThought` 在迁移时**被直接丢弃**（`src/memory.js:124` 注释：「本版只保留群友印象」）。
→ **没有"话题层"和"关系层"**。所谓"话题"由 `chatActive` 的活跃期状态承担（存在 orchestrator 内存 `activeTopics` Map，不落记忆）；"关系"只能靠印象文本自己表达。

### 5.2 怎么检索

**没有向量/相似度检索**，是「按成员过滤 + 每成员取最近 3 条」的规则式注入：

```js
// src/memory.js:455-469
formatForPrompt(chatKey, { userIds = null } = {}) {
  const notes = getConfig().memberNotes || {};
  const all = this.members(chatKey);
  if (!all.length) return '';
  const filter = userIds ? new Set([...userIds].map(String)) : null;
  const picked = filter
    ? all.filter((m) => !m.userId || filter.has(String(m.userId)))   // 无 QQ 号的旧数据始终带上
    : all.slice(0, 15);
  if (!picked.length) return '';
  const lines = ['【对群友的印象】'];
  for (const m of picked) {
    const who = notes[String(m.userId)] || m.name || String(m.userId || '') || '某人';
    for (const e of m.impressions.slice(-3)) lines.push(`- ${who}：${e.content}`);
  }
  return lines.join('\n');
}
```

注意：不过滤时**最多 15 人**、每人**最近 3 条**；备注名（`memberNotes`）优先于记忆里的名字。

### 5.3 怎么注入 prompt（位置与"谁能进"）

```js
// src/prompt.js:807-825
const relevantUserIds = new Set();
if (anchored) {
  for (const uid of (anchorDecision.anchor.memberIds || [])) relevantUserIds.add(String(uid));
} else {
  for (const m of ctx.triggerEntries || []) { if (m.senderId && !m.self) relevantUserIds.add(String(m.senderId)); }
  for (const m of (past?.messages || [])) { if (m.senderId && !m.self) relevantUserIds.add(String(m.senderId)); }
}
const memText = ctx.memory.formatForPrompt(ctx.chatKey, { userIds: [...relevantUserIds] });
if (memText) parts.push(`【记忆】\n${memText}`);
```

即"只注入本轮真的出现在提示词里（触发批 + 已读窗口）的群友印象"，避免模型看到历史里没出现过的陌生人印象。用户消息段序（`src/prompt.js:776-889`，注释在 `:776-786`）：
【角色设定】→【可用表情包】→【引导说明】→【记忆】→【已读信息】→【新已读信息】→【未读信息】→【新加入成员】→【活跃模式】→【当前时间】。
排序原则是**越稳定越靠前**（前缀缓存），所以【当前时间】精确到秒却放最末（`:885-887`）。

### 5.4 写入 / 删除 / 整理

- 写入：模型调 `memory_append` → `MemoryStore.append()` → `#appendRaw()`（同内容去重、每条截 300 字，`src/memory.js:203-220`）。
- 删除：`memory_remove` → `remove()`（按 userId 优先、target 次之；不传则删该人全部，`src/memory.js:395-440`）。
- 管理端编辑：`editMemberImpression()`（同时把备注写进 `config.memberNotes`，`:281-318`）、`replaceMember()`（带备份，`:321-360`）、`removeChat()`（`:379-393`）。
- **自动整理**：`#maybeConsolidateMemory()`（`src/orchestrator.js:1458-1485`），条件 = 二选一 + 冷却：
  - A. 全群印象总数 > `consolidateMinImpressions`（默认 4）；
  - B. 任一成员印象条数 > `maxImpressionsPerMember`（默认 5）；
  - 且距上次整理 ≥ `consolidateMinIntervalMs`（默认 6h，代码钳 ≥30min，`:1478`）。
  - 整理**只减不增**：结果条数变多视为幻觉直接放弃（`:1753-1756`），每条截 120 字、最多 `maxImpressionsPerMember` 条；整理前整目录备份（`src/memory.js:538-549`）。
  - "新建印象"模式：对"发言 ≥ 20 条却零印象"的活跃群友，取其最近 40 条发言提炼稳定特征（`src/orchestrator.js:1788-1809`）。

---

## 6. 表情 / 贴表情 / 拍一拍

### 6.1 能力怎么暴露给模型

| 能力 | 工具 | 参数 | 返回 |
|---|---|---|---|
| 看表情库 | `list_stickers` | `query`, `limit` | `{total, matched, truncated, stickers:[{id,desc,localNote,tags,useCount}]}` |
| 看表情图 | `get_sticker_image` (requiresVision) | `stickerId` | 多模态 parts（GIF 可转 video_url） |
| 给表情写认知 | `sticker_note` | `stickerId`, `note`, `tags[]`, `usage` | `{updated,id,localNote,tags}` |
| 发一张表情 | `send_sticker` | `stickerId`, `replyToMessageId?`, `atUserId?` | `{sent:true, messageId}` |
| 偷图收藏 | `collect_sticker` | `messageId`, `note` | `{collected,id,emojiId,qqSaved,localSaved,warning?}` |
| 拍一拍 | `send_poke` | `targetUserId` | `{poked:true}` |

本地表情库落 `data/stickers.json`（`src/stickers.js:7`），条目字段：`id/resId/emojiId/qqFile/localFile/persistFailed/url/md5/desc/localNote/tags/usage/source/useCount/lastUsedAt/lastContext/createdAt/updatedAt`（`src/stickers.js:20-44`）。QQ 收藏是"源"，本地库是"AI 认知层"（`src/stickers.js:1-2`）。

### 6.2 提示词里怎么描述使用时机

**时机与禁忌**（系统提示，`src/stickerRules()` → `buildStickerStrategyHint()`，`src/stickers.js:193-212`）：

```
【表情包策略：像真人一样用，不刷屏】
- 合适时机：被戳中笑点/槽点、接梗、怼人、赞同、自嘲、安慰、无语、赢了/输了、告别/晚安、别人发了表情时回一张，都可以自然用。
- <频率行，随 encourage 档位改写>
- 选择：优先用备注（desc）和你的记忆（localNote/tags）能准确对上语境的；没有备注/不确定的表情，先 get_sticker_image 看图再决定，不要瞎发。
- 发送：用 send_sticker；一条消息只能是一张表情，不能在同一气泡里附带文字；想说的话先用 send_message 作为单独气泡发出，再单独发表情。
- 不要：在严肃/正式/敏感话题硬塞表情；不要每次都用同一个；不要一条消息里塞多个表情；不要把文字和表情混在同一个气泡里。
```

**频率四档**（`src/stickers.js:198-203`，索引 0~3 与 UI `STICKER_LEVELS` 对应）：
0「表情包是备选项，不勉强」→ 1「大约每 3~5 轮来一张」→ 2「回应、吐槽、接梗时优先考虑配一个」→ 3「能配表情的地方尽量配…聊天要有表情包的烟火气」。

**目录注入**（用户消息【可用表情包】，`src/stickers.js:148-190`）：`total` 再钳到 `min(30, promptMaxStickers)`（`:151`），按 `useCount` 排序（`:152-156`），前 `floor(total/2)` 固定 + 其余按 `round = now/60min` 播种 xorshift 洗牌轮换（`:159-178`，注释：同轮次结果稳定以命中缓存）；`max ≤ 15` 时才带 `id=`（`:148`、`:186`）；**不展示 useCount**（`:183-186` 注释：会让该段每次运行都变，把后面全部段落挤出缓存前缀）。整段头部文案：

```
【可用表情包】你的 QQ 收藏表情里有 N 个表情（以下为常用/轮换的 M 个，完整列表可用 list_stickers 查询）：
- <desc 或 localNote 或「（无备注，可先看图）」> [tags/...] id=...
```

**拍一拍**：系统提示里独立一段（`src/prompt.js:140`）：

```
【拍一拍】send_poke 可以发 QQ 拍一拍。收到消息里的 [拍一拍] 事件时可以自然回应（"？干嘛""再拍试试""哈哈"），也可以回一个拍一拍。有时也可以主动戳一下正在聊的人/熟人，像真人手贱一下反而更拟真；但别频繁。
```

另在用户消息的触发标签里体现：`[拍一拍]` 被拍的是我 → 标「拍我」（召唤信号），拍别人 → 标「拍一拍」（背景事件）（`src/prompt.js:566`）。拍一拍我 = 1 档等效召唤（`src/prompt.js:487-490`：`reason: '拍了拍我'`）。历史里的拍一拍条目**不进【已读信息】**（`src/prompt.js:526-528`）。

### 6.3 发送时的工程细节（会影响"能不能发出去"）

- `ensureSendable()` 发送前把 http 直链下载转存为 `file:///`（`src/sticker-manager.js:140-175`）。
- `sender.sendSticker` 三级回退：本地文件 → base64 → 报错（`src/sender.js:206-238`），发表情前随机停 600~1500ms（`:205`）。
- `collect()` 会**必定调 `add_custom_face` 入 QQ 收藏**（`src/sticker-manager.js:204-255`），失败不阻断但回 `warning`。
- 限频：`maxCollectPerHour`（默认 10，`sticker-manager.js:216-219`）。

### 6.4 一个容易踩的坑：`sticker.enabled=false` 并不关闭表情工具

`getToolAvailability` 里**没有**任何针对 `sticker.enabled` 的判断（`src/tool-registry.js:126-212`）。关掉后：
- 不再同步 QQ 收藏、不再注入【可用表情包】、系统提示策略段仍在（`stickerRules()` 无条件调用，`src/prompt.js:270`）；
- 但 `list_stickers` / `find` 仍返回**磁盘缓存**（`sync()` 在 disabled 时直回 `this.entries`，`src/sticker-manager.js:91`），`send_sticker` 仍然能发（`src/tools.js:271-311` 无 enabled 检查）；
- 只有 `collect_sticker` 会真的失败（`收藏表情功能未开启`）。

---

## 7. 文本格式清洗：`md-to-plain.js`

### 7.1 做了什么

`mdToPlain(md)`（`src/md-to-plain.js:3-30`）纯正则流水线，**没有括号动作/内心戏过滤**：

| 步骤 | 正则 | 行 |
|---|---|---|
| 代码块去围栏（保留内容） | ` ```[a-zA-Z0-9_+-]*\n?([\s\S]*?)``` ` | `:6` |
| 行内代码去反引号 | `` `([^`\n]+)` `` | `:8` |
| 图片：`![alt](url)` → 只留 alt（空则 url） | `!\[([^\]]*)\]\(([^)\s]+)\)` | `:10` |
| 链接：`[text](url)` → `text (url)` | `\[([^\]]+)\]\(([^)\s]+)\)` | `:11` |
| 粗体/斜体/删除线 | `***x***`/`**x**`/`*x*`/`~~x~~`/`__x__` → `x` | `:13-17` |
| 标题符 | `^#{1,6}\s+` 去掉 | `:19` |
| 引用符 | `^>\s?` 去掉 | `:21` |
| 列表符 | `^\s*[-*+]\s+` → `• ` | `:23` |
| 表格分隔行/竖线 | 过滤分隔行、去首尾 `\|` | `:25-26` |
| 折叠 3+ 空行 | `\n{3,}` → `\n\n` | `:28` |

`splitForQQ(text, max=4000)`（`:33-59`）：按 `max` 切分，优先在换行处切、切点落在代理对中间时前移一个码元（防 emoji 变乱码）。

**"括号动作过滤"实际在提示词层，不在清洗层**：靠人格文本与系统提示反复禁止（`src/personas.js:45`「括号尽量少用，尤其不要用括号解释自己」、`:92`「每句都带括号、内心戏刷屏」列入 AI 味黑名单；`src/personas.js:33`「不要写"（不关我事，潜水）"这类括号内心戏」）。代码里没有任何"删括号内容"的清洗。

### 7.2 在哪个环节调用

**只有一处**：发送管道 `SendQueue.sendTextBatch()`（`src/sender.js:9` 引入，`:144` 调用）：

```js
// src/sender.js:141-151
const hardSplitAt = Number(getConfig().send?.hardSplitAt) || 0;
const parts = [];
for (const m of list) {
  const plain = mdToPlain(String(m ?? ''));
  if (!plain) continue;
  if (/^\[object Object\]$/.test(plain)) continue;
  if (hardSplitAt > 0 && plain.length > hardSplitAt) parts.push(...splitForQQ(plain, hardSplitAt));
  else parts.push(plain);
}
```

调用范围：`send_message` / `send_to` / 提醒触发（`src/orchestrator.js:1342` 走同一 `sendTextBatch`）。`send_image` / `send_sticker` 的 `note` 走的是各自的 sender 路径，**不经过** `mdToPlain`（`src/tools.js:1048-1052`、`:299-302`）——「未确认」这两个 note 是否会原样带 Markdown 进群，需看 `onebot.sendImage/sendSticker` 内部，本次未逐行核对。

---

## 8. 其它会改变模型行为、容易被忽略的机制

### 8.1 回复抢救（reply-rescue）—— 本仓库里实际不生效

`src/reply-rescue.js` 的定位：救"把成稿写在正文里、没通过工具发出去"的模型（尤其本地小模型），否则群友什么都收不到。核心立场写在文件头（`:13-17`）：只搬原句、拿不准一律沉默、**不自己发送**，而是合成一条 `send_message` 的 tool_call 交给核心统一发送链路。

三级递进（`:19-35`，实现 `:36-101`）：
1. `reply.is-planning-text` —— 正则否决"计划腔/候选稿"，整段放弃；
2. `reply.loose-send` —— 正则抽取，不花模型调用；
3. `reply.salvage-lines` / `reply.salvage` —— 让模型当裁判判"成品话 vs 内心戏"；
最后 `reply.grounded-select` 只保留"确实出自自己草稿、且没发过"的原句（`:83-91`，筛不了就不发）。

触发点：模型返回**没有任何工具调用**时（`src/orchestrator.js:1086-1116`），救不出东西就 `break` 正常结束。

> **重要事实**：上述能力全部通过 `cap('…')` 从 Skill 提供者取；本仓库**不含** `skills/` / `plugins/` 目录（`src/plugin-loader.js:41-42` 指向的两个目录都不存在），`#capFirst()` 一律返回 `null`（`src/orchestrator.js:682-686` 注释原话：「插件没装/没启用时 `#capFirst` 一律返回 null，所有分支都是空操作」）。因此 `rescueUnsentReply()` 在本仓库里恒等于 `return []` —— 抢救功能**存在但未激活**。同理失效的还有：`reply.inline-calls`（扩展内联解析）、`reply.local-policy`（本地小模型提前收尾，`src/orchestrator.js:1230-1251`）、`message.owner-check`（主人人设）、`message.speaker-format`（发言人标签，有核心兜底，见 `src/prompt.js:305-334`）。
> 核心自带的兜底仍在：`parseInlineToolCalls()`（§2.5）能处理标准 `<tool_call>` 写法。

### 8.2 提醒（reminders）

- 存储：`data/reminders.json`，每条 `{id, chatKey, text, dueAt, createdAt, createdBy, fired}`（`src/reminders.js:1-5,35-48`）。
- 调度：`startReminderLoop()` 每 20s 扫一次（启动 5s 后首检），**发送成功后才 markFired**，失败保留未触发下轮重试（`src/orchestrator.js:1298-1327`，注释解释「先标记再发会让断线时的提醒永久丢失」）。
- 触发文案：`⏰ 提醒：${r.text}`，走统一发送管道（共享限频/去重/切分/留档，`src/orchestrator.js:1333-1350`）。
- **对模型的影响**：提醒是「系统自动发言」，不经过 LLM；但它会把 message_id 写进存档，下一轮该群的历史里就会出现机器人自己说的这条提醒。
- 清理：已触发的只保留最近 200 条（`src/reminders.js:84-91`）。

### 8.3 思考（thinking）

`src/thinking.js` 把用户的一个档位**翻译成各厂商不同的参数**（方言表 `:29-50`，域名优先匹配 `:100-101`）：

| 方言 | 开 | 关 | 备注 |
|---|---|---|---|
| deepseek / glm | `thinking:{type:'enabled'}` | `{type:'disabled'}` | 与 temperature 互斥（`omitTemperature`） |
| qwen | `enable_thinking:true` + `thinking_budget` | `false` | |
| openai-o | `reasoning_effort`（xhigh/max→high） | `'minimal'` | 互斥 temperature |
| anthropic | `thinking:{type:'enabled', budget_tokens?}` | `{type:'disabled'}` | |
| gemini | `extra_body.google.thinking_config.thinking_budget`（-1=动态） | `0` | |
| openrouter | `reasoning:{max_tokens}` 或 `{effort}` | `{effort:'minimal'}` | |
| ollama | `think:true` | 不发 | |
| generic / local-openai | **不发任何参数** | —— | 宁可没思考也不打挂自建网关 |

对提示词的影响：**不写一句提示词**，只改请求体，并可能删掉 `temperature`（`src/llm.js:326-343`）。旧三态迁移：`mode==='on'` → effort `high`（`src/thinking.js:148`）。网关拒收思考参数时按 `looksLikeThinkingRejection()` 判据（`:263-280`，要求字段名与拒绝词出现在同一小片段，避免把 `invalid model name: deepseek-thinking` 误判）摘掉字段重发一次（`src/llm.js:411-414`）。

### 8.4 分级滑动（tier-slider）

滑条位置 ↔ 档位/概率的唯一换算源（`src/tier-slider.js`），刻意零依赖以避免 `config ↔ prompt` 循环依赖（`:1-6`）。**唯一真相是滑条位置**：前端只上报 `store.contextSliderPos`，`updateConfig()` 里权威反算 `contextTier`/`randomPercent`（`src/config.js:611-619`）。

档位语义（2026-09-25 改版后）：**只决定触发方式，不再决定读取条数**（`src/prompt.js:419-445` 大段注释；`src/config.js:260-266`）。判定优先级：拍一拍我 > 被艾特 > 关键词 > 随机 > 全响应（`resolveContextTier()`，`src/prompt.js:446-512`）；私聊恒 4 档（`:456-460`）。
随机档骰子必须**固定下来**（调用方传 `roll`），否则每次渲染都重掷、会话记录与提示词对不上（`:477-483` 注释）。
未命中 → 标已读、不建会话、不调模型（零 token）。

档位被这些机制**覆盖**（`storeConfigForChat()`，`src/config.js:682-718`）：指令禁言（强制 1 档）→ 峰谷时段（高峰/低谷 sliderPos）→ 分群滑条/分群峰谷 → 全局滑条；活跃期再覆盖为"必响应"（`src/orchestrator.js:527-530`）。

### 8.5 提示词锚点（prefix cache anchoring）

连续触发时，把上一轮实际发出的【记忆】+【已读信息】前缀**原样复用**，新增已读进【新已读信息】、新增群友印象进【新加入成员】（`src/prompt.js:581-608` 设计说明，实现 `resolvePromptAnchor()` `:628-691`）。
重置条件（任一）：无锚点 / 出现锚点成员集外的新群友 / 锚点第一条已滚出存档窗口 / id 序列不连续 / 追加条数 > `maxExtraRead`（默认 5）。
**关键副作用**：锚定轮**不受 `historyCount` 截尾**（`:594-600` 注释，2026-09-26 修），实际送入的已读条数可到 `锚点数 + maxExtraRead`。这是"为什么实际上下文比 historyCount 多"的唯一解释。

### 8.6 视觉能力探测（vision-scan）门控看图工具

`detectModelVision()` 用一个 1×1 PNG 发最小 chat 请求，200+choices = `vision`；4xx 且不像鉴权/模型名问题 = `no-vision`；其余 `unknown`；结果落 `config.modelVision["provider|||model"]`（`src/vision-scan.js:50-97`，批量扫描 `:103-161`，每 5 条/每 2s 落盘一次）。
运行时 `modelImageVerdict()` 先读探测结论、再回落内置官方资料表（`:39-44`），`no-vision` 会让 `visionEnabled=false` → 3 个看图工具整组消失（`src/orchestrator.js:758-759`）。**这是"我勾了 vision 但工具还不出现"的最常见原因**（UI 侧对应「扫描图片能力」按钮，`ui/app/08-modals.js:319`）。

### 8.7 主动开话题 / 活跃模式 / 会话级中止

- 主动机会：独立定时循环，随机间隔、按概率、只挑"白名单内 + 无未读 + 静默超阈值 + 不在跑"的群（`src/orchestrator.js:1354-1390`）；档位固定 4、触发摘要「主动机会（冷场开话题）」（`:514-515`、`:539-541`），并在 user 提示末尾追加"（主动机会）…可以主动抛一个自然的话题，也可以安静结束"（见 `doc/extend_development/prompt-pipeline.md:89-92`；本报告未逐字核对该字符串在当前代码里的位置）。
- 活跃模式：`finish` 带回 `activeTopic` 且本次 tier 在 1~3 → 进活跃期，存 `activeTopics.set(chatKey,{topic,until})`；活跃期内 finish("话题结束") 退出，无 topic 则续期（`src/orchestrator.js:1193-1221`）；活跃期注入提示词【活跃模式】段（`src/prompt.js:873-881`）。
- 发送侧还有一层真人感处理：消息间隔按字数加权随机（`src/sender.js:122-129`）、8 秒内容去重、每分钟/每小时限频。

---

## 9. 文档与代码的已知漂移（读文档时必看）

| `doc/` 说法 | 代码事实 |
|---|---|
| `prompt-pipeline.md:13` 四档各有读取条数（atCount/keywordCount/randomCount/allCount） | 这些字段已废弃并**主动剔除**（`src/config.js:510-515`），统一 `store.historyCount`（默认 80） |
| `prompt-pipeline.md:83` 有【此刻状态】段（群名/消息密度/距上次发言） | 该段已于 2026-09-25 整体删除（`src/prompt.js:848-851`） |
| `prompt-pipeline.md:84` 第 7 段叫【本次唤醒】 | 现名【未读信息】（`src/prompt.js:853-855`） |
| `prompt-pipeline.md:57-70` 列了 11 个插件/技能的 prompt.sections | 本仓库**不含** `skills/` `plugins/` 目录，实际一个都没有 |
| `prompt-pipeline.md:34` 人设优先级含"主人专属人设" | 依赖 `message.owner-check` 能力（Skill 提供），本仓库不存在 → 实际只有「会话级 > 全局」两层 |
| `prompt-pipeline.md` 通篇未提 | 【记忆】段位置（2026-09 提到已读信息之前以配合锚点）、【新已读信息】/【新加入成员】两段、锚点机制（`src/prompt.js:776-786`） |

另有 `doc/extend_development/video-modes.md` 与代码一致（`videoMode` 四值、`specializedModelFor` 切模型），可放心参考。

---

## 10. 「未确认」清单

1. `send_image` / `send_sticker` 的 `note` 文本是否也经过 Markdown 清洗 —— 未逐行读完 `src/onebot.js` 的 `sendImage`/`sendSticker`。
2. 主动机会在 user 提示末尾追加的那句原话，当前代码位置未逐行确认（仅见旧文档）。
3. `customPersonas[]` 条目的完整字段集（UI 按 `{id,name,text,customRules}` 用，后端路由是否强校验未细看 `src/routes.js:359-383`）。
4. `knowledge` 分类、`media` 分类下除内置工具外的 Skill 工具行为（本仓库无 Skill 可查）。
5. 群成员备注 `memberNotes` 与 `speaker-identity` Skill 的输出格式差异（Skill 不存在，仅有核心兜底 `名字(QQ:xxx)`）。
