# 一键整合包 × 新手引导：可落地的完整设计

> 面向：框架维护者 / 产品设计。本文把两份既有报告（「一键部署差距」与「整合包构想」）合并成**一套设计**，
> 并给出**后端契约**与**前端形态**，目标只有两个：**稳定**（不出半成品、不回滚不了）、**好用**（小白能走完、老手能复用）。
>
> 基线：`main@d2a9d8b`。所有"现状"结论均带 `文件:行号`，可按行号复核。
> 与其它文档的关系：身份/开关的信息架构见 [`identity-and-capability-console.md`](identity-and-capability-console.md)；
> 作用域存储见 [`multi-account-design.md`](multi-account-design.md)；模块安装见 [`module-management-redesign.md`](module-management-redesign.md)。
> 本文是**「工件（artifact）」与「就绪（readiness）」两个新概念**的准一来源。

---

## 0. 一页结论

| 问题 | 结论 |
|---|---|
| **小白卡在哪？** | 不是代码质量，是**三段"没有桥"**：Python 本体、协议端 NapCat、以及"配置完了但没生效"（`group_enable` 默认关且静默 `return`：`app/agent/loop/pipeline.py:316-317`）。 |
| **整合包该长什么样？** | 不是"在配置项中间插一层运行时合并"，而是**一个可安装的配置工件（Bundle）**：导入 = 预检 → 事务化实例化 → 记录来源；回滚 = 恢复快照。**物化后它就是账号自己的配置**，运行时零改动。 |
| **为什么不是"再做一个配置档案"？** | 你 5 天前删掉的「配置档案」（`b745b87`）是"按会话合并的覆盖层"，没有工件生命周期、不可导出、不可分享——失败原因不是分层，是**没有 artifact**。本文的 Bundle 与它语义相反。 |
| **稳定的最低标准？** | 三条：①预检与应用共用同一个 plan；②任何失败都能回到应用前（快照 + journal + 启动恢复）；③所有"不生效"都能在界面上读到原因。缺一条就会重演"机器人不回话、日志干净"。 |
| **好用的最低标准？** | 一条主路径：**双击 → 首启向导（4 步）→ 群里 @ 有回复**；一条复用路径：**导出/导入套装**；所有操作都有"下一步该点哪"。 |
| **先做什么？** | P0（本周，多数零代码）：就绪横幅 + 静默坑可见 + 发行链路修复；P1（1 周）：首启向导；P2–P4（2–4 周）：Bundle 预检/实例化/回滚/卸载；P5：协议端托管 + 免安装发行包。 |

**本文最值钱的一句话**：把 Bundle 做成首启向导的最后一屏——它既解决"小白第一次配什么"，又天然是备份/换机/分享/发行包的通用容器。**一个工件格式，同时服务部署、分发、迁移**。

### 0.1 需求 → 机制 → 验收（对照你提的四个词）

| 你要的 | 本文的机制 | 怎么验收 |
|---|---|---|
| **易上手** | 首启向导 4 步 + 就绪卡 + 启动横幅；`group_enable` 等静默点全部补日志 | 新机器从零到"群里 @ 有回复"≤5 步；每一步都有"下一步点哪" |
| **高拓展** | Bundle 工件（声明式 manifest + 内容物 + 引用重写）+ 模块 code 走既有 zip 通道 | 导出一个套装 → 另一台机器导入 → 行为一致；第三方能照规范做包 |
| **稳定** | 预检=应用（同一 plan）+ 事务 + 快照回滚 + 启动恢复 + 值校验器 | 8.1 六条不变式全部有测试；失败矩阵 12 种全部有确定表现 |
| **好用** | 预检报告四块（将创建/将修改/依赖/需手填）+ 结果逐项 + 一键回滚 + 错误三类分流 | 用户不需要看日志就能知道"哪一步、为什么、怎么办" |

---

## 1. 痛点：两份报告合并后的清单

> 两份报告的结论可以压成两句：**部署报告**说"差三道墙（Python / 协议端 / Key+人格），且多数是'无提示'不是'做不到'"；
> **整合包报告**说"方向对，但缺的不是配置层而是**拥有配置的实体**，且这层做过又删了，要换轴重做"。
> 合并后：**两者是同一个病——缺"工件"和"就绪"两个概念**（§1.4）。所以本文把它们做成一套东西，而不是两个功能。

### 1.1 小白路径：11 步里有 5 处会"卡死且无提示"

| # | 步骤 | 现状 | 卡点性质 |
|---|---|---|---|
| 1 | 装 Python 3.10+ | `start.bat` 找不到时 `[FAIL] Python 3.10+ not found` + pause | 硬门槛，非代码能解 |
| 2 | 取代码 | git clone / 下载 zip | 中 |
| 3 | 装依赖 | `pip install`；无镜像，国内易超时；且 `start.py:601-604` 对任何非 0 退出码都打印"没联网 / 没配镜像"，而 `check_deps.py:344` 的退出码 3 实际是 **Python 版本不符** → 把排查方向指反 | 高（错误指向错） |
| 4 | 启动 | `start.py` 建 venv、生成 `.env`、开浏览器带 token | 好 |
| 5 | 装协议端 NapCat | 完全用户自理：装、扫码、配反向 WS 地址与 token | 硬门槛 |
| 6 | 建账号 + 填 ws/token | `AddBotDialog` 刻意不提 NapCat（`AddBotDialog.vue:9-19` 注释写明口径），小白要在两个 UI 之间"对暗号"；`ws_url` 保存时**不校验** | 高 |
| 7 | 配 Provider + 填 Key | 有预设与测试接口，但要求先建预设→再建模型→再回 Agent 页入池，**三步三次跳页** | 高 |
| 8 | 选模型入池 | 没入池时 `provider_config()` 回退空 key，错误提示却说"去 Provider 预设页填写"——**把人指回已经填过的地方** | 高（错误指向错） |
| 9 | 开群回复 | `group_enable` 默认 **False**（`app/agent/config.py:69`），群里 @ 机器人命中后**直接 return、零日志** | **最高**（静默） |
| 10 | 群里 @ 一下 | 第 9 步没开 = 完全没反应 | 同上 |
| 11 | 更新/备份/迁移 | 无；只能整目录拷 | 中 |

**结论**：第 9 步是"最伤"的一条——用户做完 10 步、在群里 @ 机器人，得到的是**沉默**。所以第一优先级不是"减少步骤"，而是**把沉默变成诊断**；减少步骤要等向导把 6–10 步合流。

> **`group_enable` 默认值的决策**：**不改默认值**（`False` 是刻意的安全默认，改成 `True` 会让存量部署升级后突然在群里说话）。
> 改为三处可见：①首启向导第 2 步强制选择"群聊/私聊"（默认勾选群聊）；②就绪卡把"群聊回复未开启"列为 blocker；
> ③命中未开启时打一条**按账号限流**的 WARN（`[skip] 群消息被忽略：群聊回复未开启 → Agent→基础配置`）。

### 1.2 整合包想做的事，以及前车之鉴

- 需求原话：**一键导入/配置模块、人格设定以及其它配置，并且可以和其它配置相互独立**。
- 现有替代品：`AgentCopyConfigDialog.vue`（把 A 账号的 Agent 配置整块复制到 B 账号）。它是**数据模型缺陷做成功能**的典型：只能账号间搬、只有 Agent、公开的全局预设不跟着走、无版本、无校验、不可分享。
- 前车之鉴：`b745b87` 删除的「配置档案」= 把整套配置存快照 + 按 `group_xxx` 路由 + 运行时 `_profile_config()` 合并。**它失败在四件事**：不可导出/不可分享/不可版本化；只覆盖 Agent；入口藏在实验开关后；语义是"每次读取都合并"。

→ 设计口径：**别在读取路径插层，在「预设/模块」与「绑定」之间插一个"工件"**，导入即物化，激活只是元数据。

### 1.3 稳定性痛点（这次必须一起修，否则导入就是放大器）

| # | 痛点 | 证据 |
|---|---|---|
| S1 | **配置写入无值校验**：模块配置逐键 `set()`（`app/webui/api/modules.py:380-418`，逐键在 `:401-407`），Agent 配置同样只处理 password 哨兵（`app/webui/api/agent.py:132-182`）。离谱案例：①模块权限 `group_mode` 接受任意字符串，而 `authority.py:184-198` 只认 `whitelist/blacklist` → **非法值落入"两个分支都不进"= 等同放行**；②`provider_model_pool` 在 schema 里声明成 `string`（`config_schema.py:764-765`），运行时按 list 消费（`runtime.py:281`）——**schema 根本不参与写入校验** | 手工改自己认；**机器导入别人的配置写错了 = 静默垃圾数据**，还会被前端下次自动保存写回 |
| S2 | **写配置非事务且"先内存后落盘"**：`binding_store.py:147-148` 调 `_schedule_persist`，`:168-192` 落盘异常只 `log`；接口已返回 200 | 内存与 DB 分歧；批量导入中途失败留半套配置 |
| S3 | **无"就绪"判定**：判断"能不能回话"要同时看模型池、`group_enable`、连接、监听端口，**没有一处聚合** | 直接导致 §1.1 的静默 |
| S4 | **引用完整性只做了一半**：`provider_preset` 删除只清 `provider_preset_id`/`provider_model_id`（`provider_preset_service.py:256-289`），而运行期真正消费的是 **`provider_model_pool`**（`runtime.py:274-281`）→ 池里留死 id 静默降级；`list_references` 也只查 `provider_model_id`（`provider_model_service.py:215-220`），不查池。`role_preset` 删除完全不查引用（`role_preset_service.py:151-155`） | 悬挂引用静默降级，排障无入口 |
| S5 | **发行链路是断的**：`release.yml:55` 打包 `-C dashboard dist`，仓库根无 `dashboard` 目录（真实产物在 `app/webui/dashboard/dist`）→ 该 step 必失败；`pyproject.toml:50-54` 的 package-data 不含 `dist` → wheel 安装后前端回 503（`webui/app.py:88-90`）；`dist` 现 1077 文件 / 15.09 MB 全部被 git 跟踪，且 `scripts/prune_dist.py:42-55` 只留"index.html 引用 + 最近 7 天"——**Vue 懒加载 chunk 不在 index.html 里，会被误删** | "源码能跑、分发不能用" |
| S6 | **失败提示指向错/被吞**：`check_deps.py:340-344` 用退出码 3 表示 Python 版本不符，但 `start.py:241-244` 把非 0 统一折成 2，于是 `:601-604` 打印"没联网 / 没配镜像"；Agent 配置端点 JSON 解析失败退化成 `{}` 并**返回成功**（`agent.py:139-143`、`:182`），请求体非对象又直接 500（`:145-147`） | 用户按提示走 → 更迷 |
| S7 | **密钥脱敏不一致（安全问题，且是导出功能的前置）**：`provider_preset_service.py:78-86` 的 `_public_preset` 明示"不再打码"，`GET /api/provider-presets` 与 `/api/agent/config` 会**回传明文 api_key**；而模块/Agent 配置走另一套 `PASSWORD_MASK` 哨兵 | ①任何登录会话可读全部密钥；②导出功能若依赖接口回读，会把密钥打进包 |

### 1.4 根因：缺两个概念

1. **缺"工件（artifact）"**——所以"一套配置"无法离开某台机器/某个账号存在，只能复制粘贴。
2. **缺"就绪（readiness）"**——所以"能不能用"只能靠用户自己拼七八个状态，拼错就表现为"坏了"。

本设计就是补这两个概念，并让它们互相咬合：**向导的最后一屏 = 选工件；工件的第一屏 = 就绪检查**。

---

## 2. 设计公理（后续所有决策都从这里推）

| # | 公理 | 落地约束 |
|---|---|---|
| A1 | **一个事实一个来源** | 工件导入后**物化**为账号配置/全局预设；运行时不读"套装层"，不做合并覆盖（`b745b87` 的教训）。 |
| A2 | **预检先行，且预检=应用** | dry-run 产出的 `plan` 就是应用时执行的同一对象；预检结论与应用结果不一致 = 缺陷。 |
| A3 | **全成或全退** | 快照 + journal + 启动恢复；任何一步失败都回到应用前，禁止半成品。 |
| A4 | **静默失败是缺陷** | 所有"没反应"必须在 readiness/日志/UI 三处之一有可读原因（`group_enable` 这类必须补日志）。 |
| A5 | **工件不携带代码、不携带密钥** | 包内只有数据与声明；模块代码只能走既有 zip 安装通道；密钥必须导入后手填。 |
| A6 | **不覆盖别人的对象** | 导入只创建新对象（新 id），同名靠后缀区分；"独立"由此成立。 |

---

## 3. 参照物：抄什么、不抄什么

> 全部结论来自官方文档/源码，链接见 §11。**下面每一条都标了"我们这个项目能不能直接抄"。**

### 3.1 PCL2 整合包（Minecraft 启动器）

**事实**（已核对官方帮助）：

- PCL **不定义格式**，只消费 CurseForge `.zip` 与 Modrinth `.mrpack`（自身也能导出）。
- 安装：把包拖进 PCL 窗口 → 输入版本名 → 确定；另有一种"零交互"：包名恰为 `modpack.zip` / `modpack.mrpack` 且与 `PCL.exe` 同目录时，打开 PCL 即自动安装。
- **版本隔离**：2.9.2 起新用户默认开启；开启后该版本的数据（`options.txt`、存档、mods、config）落在独立版本目录。
- 导出：自动检测可导出内容 + 勾选项（Mod / 重要数据 / Mod 设置 / 启动器程序 / 个性化）+ **"保存导出配置"**；官方明确"正版账号等敏感信息不会随整合包发放"。
- **PCL 没有内建整合包更新**（多个 feature request 被标 duplicate 关闭）；现状是重下新包、用户自行迁移。

**抄**：拖入即装 + 文件名自动识别；安装前的"将安装什么/缺什么/多大"确认页；**每套件独立实例目录**；导出勾选框 + 保存导出配置；敏感信息永不进包。
**不抄**：把"整合包更新"说成抄 PCL（它没有）。**我们的更新语义必须自己定义**：清单管覆盖、用户改动保留、冲突显式列出（§8.1 I5）。
**映射到本项目**：独立实例目录 → 我们已经是"配置与代码分离 + bindings 按账号"，故"隔离"落在**对象命名空间 + 每账号激活**（§4.4）。

### 3.2 Modrinth `.mrpack` / CurseForge `manifest.json`

**事实**：

- mrpack：根目录 `modrinth.index.json`；`formatVersion`、`game`、`versionId`、`name`、`files[{path, hashes{sha1, sha512}, env{client,server}=required|optional|unsupported, downloads[], fileSize}]`、`dependencies{minecraft, forge, fabric-loader…}`；**双哈希必填**；下载域名白名单；规范明确警告 `path` 不得含 `..`/盘符/绝对路径（否则"导入 zip"可写出实例目录）。
- CurseForge：`manifestType`、`manifestVersion`、`minecraft.modLoaders`、`files[{projectID, fileID, required}]`、`overrides/`；**不打包 jar 的理由是分发权与作者收益**，`overrides` 只允许许可允许的三方内容，否则整包被拒。

**抄**：声明式 manifest + **内容哈希**（我们 v1 用 `checksums.sha256`，v2 再加签名）；`env: required/optional/unsupported` 三态（我们的 optional 模块/组件）；路径规范化校验；"只发清单不发二进制"的思想（我们的模块默认引用而非打包）。
**不抄**：强制在线下载（国内环境应允许随包携带）；把第三方 jar 打进包（版权）。

### 3.3 VS Code Profiles / Extension Pack

**事实**：`.code-profile` 可导出/导入，**勾选包含哪些部件**（settings/keybindings/snippets/tasks/MCP/extensions）；支持 Template、**创建前 Preview**、按文件夹关联。
**抄**：导出时的部件勾选（§7.5）；导入前 Preview（§7.4 第②屏）；"Template"作为向导第 3 步的可选项。
**不抄**：依赖云端账号同步。

### 3.4 AstrBot（QQ 机器人，部署与插件体验）

**事实**：官方部署矩阵极宽（脚本 / 桌面客户端 / 启动器 / Docker / K8s / 宝塔 / 1Panel）；启动器自动拉源码+装依赖+启动；**首启日志打印面板地址与随机初始密码**，登录后要求改密；WebUI 内完成全部配置；插件市场"搜索→安装（确认来源）→到已安装页确认已打开且无加载错误"，**加载失败的插件有独立列表可单插件重载**；删除插件时可**分别勾选**是否同时删除配置/持久化数据；系统设置里有**备份/恢复**，诊断包默认脱敏、raw 模式才含密码/Token/IP。
**抄**：①首启打印访问入口 + 一次性凭据（本项目已有 `WEBUI_AUTO_OPEN` + token 链接，补"要求改密/随机令牌"即可）；②**健康检查页**（端口/依赖/插件加载）；③**失败项列表 + 单插件重载**（不重启）；④卸载时"保留/删除数据"双选项；⑤备份/恢复 + 脱敏诊断包。
**不抄**：把"装完"当"启用"（见 3.5 的 Koishi 反例）。

### 3.5 Koishi（插件市场与启用语义）

**事实**：市场"添加"→选版本→安装，**装完不会自动启用**，要去"插件配置"手动启用；停用不删代码与配置；必填未填红条、改动未存紫条；依赖管理页可批量"应用更改/全部更新"；**删除插件配置不可撤销**（官方 DANGER）。
**抄**：状态与参数分离；"必填未填"的红条语义；依赖批量变更前先预览。
**不抄**：**"安装"与"启用"分两步且无引导**——这正是我们要避免的"配置完了但没生效"（§1.1 #9 同型问题）。我们的导入流程必须做到：**应用成功的最后一步 = 明确的"已生效"状态 + 就绪检查通过**，而不是"装好了，你自己去开"。

### 3.6 NapCat / 一键安装器（协议端）

**事实**：Windows 一键包 `NapCat.Shell.Windows.OneKey.zip` **无需预装 QQ**：运行 `NapCatInstaller.exe` → 进 `NapCat.XXXX.Shell` → `napcat.bat`；另有单 EXE 可视化管理器（建/管配置、启停重启、自动更新、多账号、引导安装 QQ）。"一键"省掉的是：装 QQ、找安装路径、解压放置、写启动脚本、生成配置。
**抄**：**运行时也要被托管**（对应我们 P5 的 NapCat 托管：锁定版本、拉起、二维码转发、自动写反连、健康检查、可回滚）；文档建议固定 tag 而非 `latest`（版本要锁）。
**不抄**：静默安装 + 无版本锁定。

### 3.7 Yunzai / TRSS

**事实**：四种装法（脚本矩阵 / 手动 / Docker 一行 / Windows 安装器）；门槛包括 Node 版本、Valkey、Git、Chrome；社区失败点集中在网络与依赖（推断）。
**抄**：**多入口但同一套检查**（脚本/安装器/Docker 最终都走同一个 preflight）；把"环境依赖清单"显式化（我们的 P0 README 前置条件）。
**不抄**：把依赖安装交给用户自己 debug。

### 3.8 非游戏套装（HA Blueprint / Docker Compose / 1Panel）

- **HA Blueprint**：导入先 Preview 再 Import；重导入**直接覆盖**，官方 Caution 明说可能破坏已有自动化 → 正面印证我们"快照 + 冲突列表 + 回滚"的必要性。
- **Docker Compose**：声明式、`up/pull`，但**无迁移与回滚语义** → 我们比它多一层事务。
- **1Panel / 宝塔**：参数表单 + 高级设置 + **安装日志可复制**。
**抄**：安装日志可复制；参数分步填；"预览将安装内容"；程序与数据分离。

### 3.9 行业共识 vs 我们的差异化

| 维度 | 行业普遍做法 | 本项目要做的 |
|---|---|---|
| 包格式 | 声明式 manifest + 哈希 | 同左（`bundle.json` + `checksums.sha256`），并带 `schema_versions`/`framework.api_version` |
| 密钥 | 不进包 | 同左，**并修掉现状"接口回传明文密钥"（S7）** |
| 升级 | 多数直接覆盖（HA 明说会破坏） | **快照 + 冲突列表 + 保留用户改动**（I5），这是差异化 |
| 安装失败 | 多为"报错 + 重试" | **事务 + 启动恢复 + 一键回滚**（I3/I4） |
| 启用语义 | 安装≠启用（Koishi） | **应用成功即"已生效"，并用就绪检查证明** |
| 隔离 | 实例目录（PCL） | 对象命名空间 + 每账号激活（§4.4） |

---

## 4. 目标模型

### 4.1 分层图

```
┌ 代码层   内置 app/plugins + 第三方 data/module/plugins        （已有：module.json v2）
├ 预设层   role_preset / provider_preset / provider_model       （已有：全局、按 id 引用）
├ 工件层   Bundle{manifest + 内容物 + 校验和}                    （新增：本文 §5）
├ 绑定层   bindings(global/account/channel) + 账号 active_bundle  （已有地基 + 一个元数据指针）
└ 运行层   AgentRuntime / 模块实例 / 网关                         （零改动 ← 这是关键）
```

### 4.2 两个工件，同一套规范

| 工件 | 消费者 | 内容 | 产出方式 |
|---|---|---|---|
| **Config Bundle（配置套装）** | 已有用户 / 换机 / 分享 | 人格 + Provider 连接形态 + Agent 配置 + 模块开关与参数 | WebUI 导出 / 手写 / 第三方制作 |
| **Deploy Kit（发行包）** | 从零开始的小白 | 框架代码 + 内嵌 Python + wheelhouse + dashboard dist + 默认 Bundle | CI 构建（修复 release 链路后） |

两者共用 `bundle.json` 的 `schema` 命名空间与校验器：Deploy Kit 里内嵌一个 `bundles/default.zip`。
**v1 只做 Config Bundle**；Deploy Kit 在 P5（先修 §1.3 S5 的三处硬伤）。

### 4.3 生命周期状态机

```
          ┌──────────┐  validate  ┌───────────┐  import  ┌────────────┐
 文件/URL →│ 预览报告  │──────────→│ 待安装(内存) │─────────→│ installed  │
          └──────────┘            └───────────┘           └─────┬──────┘
           （不落库）                 失败=丢弃                   │ apply
                                                                 ▼
                                            ┌────────────── applying（有快照）
                                            │       成功 ↓        ↓ 失败/崩溃
                                      ┌─────┴────┐        ┌──────────────┐
                                      │  active  │        │ rolled_back  │
                                      └─────┬────┘        └──────┬───────┘
                                            │ rollback           │ 可重试
                                            └────────────────────┘
                                      uninstall ↓（清理本包创建的对象）
                                          ┌──────────┐
                                          │ uninstalled │
                                          └──────────┘
```

### 4.4 "相互独立"的确切含义（写进验收）

1. **对象级独立**：包内对象一律新建（新 id），不改任何既有对象；同名加后缀。
2. **账号级独立**：应用只改目标账号的 bindings；其它账号不受影响。
3. **卸载可逆**：删除本包创建的对象 + 恢复被覆盖对象的快照；用户后来新建的对象不动。
4. **v1 明确不做**：同一账号同时激活多个套装、同账号不同群用不同套装（那是被删功能的路，见 §10）。

### 4.5 与「能力控制台」的关系（避免两套开关）

- 控制台（`identity-and-capability-console.md`）是**日常调参**的地方：一行一个能力，改开关/权限/参数。
- Bundle 是**整体搬运**：它把"某账号当前的控制台状态（bindings）+ 引用的全局预设"打包。
- 导出来源 = 控制台的 resolved 视图；导入落点 = 同一套 bindings/预设 → **不产生第二套配置体系**。
- 因此顺序上是：先有"能力控制台"（P2 of that doc）→ Bundle 导出才有一致的语义；
  若并行推进，Bundle 导出直接读 `get_binding` / `get_modules_data`（两者都已是控制台数据源），不冲突。

---

## 5. 包格式规范（Bundle v1）

### 5.1 目录结构

```
群聊搭子-1.2.0.zip
├── bundle.json               # 清单（唯一必需文件）
├── README.md                 # 人读说明（预检页展示前 20 行）
├── checksums.sha256          # 内容物哈希（覆盖除自身外全部文件）
├── persona/
│   └── default.md            # 人格全文（内容物）
├── configs/
│   ├── agent.json            # Agent 配置子集（已脱敏）
│   └── module-llm_memory.json
└── modules/                  # 可选：随包模块（默认不打包，仅声明引用）
    └── llm_memory-1.0.0.zip  # 走既有 zip 安装通道，不新增执行面
```

### 5.2 `bundle.json` 字段表

| 字段 | 必填 | 说明 | 校验 |
|---|---|---|---|
| `schema` | ✅ | 固定 `qqbotnext.bundle/1` | 不等于 → `BUNDLE_SCHEMA_UNSUPPORTED` |
| `id` | ✅ | 稳定标识（更新/去重键），`[a-z0-9][a-z0-9._-]{2,63}` | 非法 → 拒绝；同 id 已装 = 升级流程 |
| `name` / `version` | ✅ | 显示名 / semver | 非法 semver 拒绝 |
| `author` / `license` / `description` | — | 元信息 | 长度上限 200 |
| `framework.api_version` | ✅ | 该包依赖的框架 API 版本（整数） | `> 本机 API_VERSION` → 拒绝 |
| `framework.min_version` | — | 框架最低版本（semver） | 不满足 → 拒绝并提示升级 |
| `schema_versions` | — | 引用的配置 schema 版本（如 `{"agent":5,"module":2}`，对齐 `SCHEMA_VERSION`） | 高于本机 → 拒绝（避免"能装不能渲染"） |
| `requires.modules[]` | — | `{name, version(约束), required}` | 缺失 required → 预检拦截；optional 缺失 → 警告并跳过 |
| `requires.resources[]` | — | 需要的资源种类（`ResourceRegistry` kind） | 未知种类 → 警告 |
| `components.personas[]` | — | `{source, name, apply_to, vars}`；`apply_to` 默认 `agent.persona_preset_id` | 文件必须存在 |
| `components.providers[]` | — | `{provider, api_base, model, preset_name, apply_to}`；**不含密钥** | `api_base` 必须合法 URL；`apply_to` 默认 `agent.provider_model_pool` |
| `components.agent[]` | — | `{source, merge:"keys"}` | 键必须在 `agent` schema 内（未知键拒绝） |
| `components.modules[]` | — | `{name, enabled, source?}` | 模块必须存在；配置键必须在模块 schema 内 |
| `secrets[]` | — | `{key, label, required, hint}`；key 用点路径指向上面组件里的密钥位 | 应用前必须全部满足（或组件跳过） |
| `permissions` | — | 建议的响应权限（`member`/`manager`…），作为默认值 | 枚举 |
| `notes` | — | `{intro, changelog, screenshots[]}` | 仅展示 |

### 5.3 内容物编码规则（决定"复制还是引用"）

| 对象 | 进包形式 | 理由 |
|---|---|---|
| 人格提示词 | **全文**（`persona/*.md` 或内联） | 人格是内容，不是共享资产；跨机器必须自包含 |
| Provider 连接 | **形态**：provider 类型 + api_base + 模型名 | 密钥不打包；api_base 可被用户改 |
| Provider 模型 | 名称 + 参数（温度等非密钥项） | 同上 |
| Agent 配置 | **子集**：人设选择、场景开关、行为参数、权限档位 | 只带"属于这个套装"的键；连接类/机器相关键走 `secrets` 或忽略 |
| 模块 | **引用**（name + 版本约束）+ 可选 zip | 代码走既有安装通道；版本要能约束 |
| 模块参数 | 该模块的 config 子集 | 与模块 schema 逐键校验 |
| 模块开关 | `enabled` 布尔 | 与参数分离（Koishi 模型） |

### 5.4 ID 与引用重写（apply engine 的核心难点）

导入时**所有新建对象都换新 id**，并重写包内全部引用。重写表：

| 对象 | 引用点（配置键/字段） | 策略 |
|---|---|---|
| 角色预设 | `agent.persona_preset_id` | 新 id 写入该键 |
| Provider 预设 | `agent.provider_preset_id`、全局默认 `default_preset_id` | 新 id 重写 |
| Provider 模型 | `agent.provider_model_id`、`agent.provider_model_pool[]`、`default_model_id` | 逐个映射；池中未匹配项移除并记 warning |
| 资源型字段 | 模块 schema `type: "resource"/"resources"` 的值 | 若包内创建了对应资源 → 映射；否则按 `on_missing` 策略（默认保留并标 hanging） |
| 模块/工具名 | `tools`/`skills` 名称列表 | 按名引用，不重写；缺失 → warning |
| 模块名 | `requires.modules[].name` | 不重写（模块全局唯一） |

> 规则：**引用重写必须发生在"写库之前"的纯函数里**（`BundlePlanner.rewrite_refs`），可单测、可 dry-run 复用。

### 5.5 完整示例（一个可分享的"群聊搭子"）

```jsonc
{
  "schema": "qqbotnext.bundle/1",
  "id": "group-chat-companion",
  "name": "群聊搭子",
  "version": "1.2.0",
  "description": "一套开箱能聊的成员人格 + DeepSeek 连接 + 记忆与主动消息参数",
  "author": { "name": "example" },
  "license": "CC-BY-4.0",
  "framework": { "api_version": 1, "min_version": "2.0.0" },
  "schema_versions": { "agent": 5, "module": 2 },
  "requires": {
    "modules": [
      { "name": "llm_memory", "version": ">=1.0.0", "required": true },
      { "name": "llm_proactive", "version": ">=1.0.0", "required": false }
    ],
    "resources": ["image"]
  },
  "components": {
    "personas": [
      { "source": "persona/default.md", "name": "群聊搭子", "apply_to": "agent.persona_preset_id" }
    ],
    "providers": [
      { "provider": "openai", "api_base": "https://api.deepseek.com/v1",
        "model": "deepseek-chat", "preset_name": "DeepSeek 官方",
        "apply_to": "agent.provider_model_pool" }
    ],
    "agent": [{ "source": "configs/agent.json", "merge": "keys" }],
    "modules": [
      { "name": "llm_memory", "enabled": true, "source": "configs/module-llm_memory.json" },
      { "name": "llm_proactive", "enabled": false }
    ]
  },
  "secrets": [
    { "key": "providers[0].api_key", "label": "DeepSeek API Key", "required": true,
      "hint": "在 platform.deepseek.com 创建；只保存在本机" }
  ],
  "permissions": { "default": "member" },
  "notes": { "intro": "导入后记得在群里 @ 一次机器人确认。", "changelog": "1.2.0 调整记忆参数" }
}
```

### 5.6 校验矩阵（每一层只干自己的事）

| 层 | 校验 | 失败表现 |
|---|---|---|
| 结构 | zip 合法性、路径穿越、符号链接、文件数/大小上限、必需文件存在 | 400 + `BUNDLE_ZIP_*` |
| 清单 | `bundle.json` JSON Schema（字段类型/必填/枚举/长度） | 400 + `BUNDLE_MANIFEST_*` |
| 兼容 | `schema` / `framework.api_version` / `min_version` / `schema_versions` | 409 + `BUNDLE_INCOMPATIBLE` + 需要什么版本 |
| 依赖 | 模块存在性 + 版本约束；可选模块缺失降级 | 422 + `BUNDLE_MODULE_MISSING`（列出全量） |
| 引用 | 包内 `source` 文件存在；ID 映射可完成；资源种类已注册 | 422 + `BUNDLE_REF_*`（逐条） |
| 值 | 用 **配置值校验器**（§6.4）逐键校验（类型/范围/枚举/长度/密码位） | 422 + 逐键 `BUNDLE_VALUE_INVALID` |
| 安全 | 密钥不得出现在任何内容物中（扫描 `api_key`/`sk-` 等高危模式）；不得含可执行脚本 | 422 + `BUNDLE_SECRET_LEAK` |
| 完整性 | `checksums.sha256` 逐文件比对 | 422 + `BUNDLE_CHECKSUM_MISMATCH` |

---

## 6. 后端设计

### 6.1 数据模型（4 张新表 + 2 个 kv）

```sql
-- 已安装的套装（工件登记）
CREATE TABLE IF NOT EXISTS bundles (
    id              TEXT PRIMARY KEY,        -- bundle.json.id（稳定标识）
    name            TEXT NOT NULL,
    version         TEXT NOT NULL,
    schema_version  TEXT NOT NULL,           -- qqbotnext.bundle/1
    author          TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    license         TEXT,
    manifest_json   TEXT NOT NULL,           -- 归一化清单（密钥位已脱敏为空）
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    file_count      INTEGER NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'file',   -- file / url / export
    status          TEXT NOT NULL DEFAULT 'installed', -- installed / error
    installed_at    INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);

-- 一次"应用到账号"= 一条记录，含回滚快照
CREATE TABLE IF NOT EXISTS bundle_activations (
    id            TEXT PRIMARY KEY,
    bundle_id     TEXT NOT NULL,
    account_id    TEXT NOT NULL,
    status        TEXT NOT NULL,             -- applying / active / failed / rolled_back
    plan_json     TEXT NOT NULL,             -- dry-run 计划（预检与应用共用）
    snapshot_json TEXT NOT NULL,             -- 应用前被覆盖对象的完整快照
    applied_json  TEXT,                      -- 实际写入明细（对象 id → 新值）
    error         TEXT,
    started_at    INTEGER NOT NULL,
    finished_at   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_bundle_act ON bundle_activations(account_id, status);

-- 本包创建的对象（卸载/回滚清理用；用户后续新建的对象不在其中）
CREATE TABLE IF NOT EXISTS bundle_objects (
    bundle_id  TEXT NOT NULL,
    kind       TEXT NOT NULL,   -- role_preset / provider_preset / provider_model / binding / module_config
    object_id  TEXT NOT NULL,   -- 目标对象 id；binding 用 "capability|scope|scope_id" 复合键
    account_id TEXT,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (bundle_id, kind, object_id)
);

-- 账号当前"来自哪个套装"（纯元数据：用于 UI 显示、升级检测、卸载前置检查）
CREATE TABLE IF NOT EXISTS account_active_bundle (
    account_id    TEXT PRIMARY KEY,
    bundle_id     TEXT NOT NULL,
    activation_id TEXT NOT NULL,
    updated_at    INTEGER NOT NULL
);
```

外加 `kv` 两个键：`bundle_api_version`（当前 1）、`setup_completed_at`（首启向导是否完成）。

> `account_active_bundle` **不参与运行时解析**（A1），只用于：详情页"已用于哪些账号"、卸载前"先用回滚清掉"、
> 更新时"哪些账号需要重新应用"。

> **为什么不给 bindings 加 `bundle` 作用域？** v1 不需要：导入即物化。等真要做"同账号多套装热切换"时再加（§10.1 给了精确改动点），避免现在就动运行时解析链。

### 6.1.1 快照的确切范围（回滚可靠性的关键）

`snapshot_json` 只存**本次会被覆盖或删除**的对象，格式与存储一致：

```jsonc
{
  "bindings": [   // capability × scope=account × account_id
    {"capability":"agent","config":{…},"permission":{…},"enabled":true},
    {"capability":"llm_memory","config":{…},"enabled":false}
  ],
  "created": [    // 本次新建的对象（回滚时删除）
    {"kind":"role_preset","id":"rp_a1"},
    {"kind":"provider_preset","id":"pp_b2"},
    {"kind":"provider_model","id":"pm_c3"}
  ],
  "active_bundle": null   // 应用前的 account_active_bundle 值
}
```

- 只覆盖 `bindings` 的 **account 档**与本次涉及的 capability；global/channel 档不动（套装不写全局默认）。
- 回滚 = `bindings` 写回 + `created` 删除 + `account_active_bundle` 还原；**不触碰**用户在此之后新建的对象。

### 6.2 服务分层与职责

| 服务 | 职责 | 不做什么 |
|---|---|---|
| `BundleService` | 注册表 CRUD、安装/卸载、激活/回滚编排、启动恢复、锁 | 不解析配置、不写业务数据 |
| `BundleValidator` | 结构/清单/兼容/依赖/引用/安全/完整性 七层校验，产出 `issues[]` | 不写库 |
| `BundlePlanner` | 把「清单 + 目标账号当前状态」算成**幂等计划**：新建对象 id 预分配、引用重写、键 diff、需手填项、冲突项 | 不执行 |
| `BundleApplier` | 执行计划：事务、快照、写入、逐项结果、失败回滚 | 不决定"该写什么"（那是 Planner） |
| `BundleExporter` | 从账号/全局收集配置 → 打包 → 脱敏 → 生成 `secrets` | 不导出密钥明文 |
| `ConfigValueValidator` | 用 schema 生成的值校验（类型/范围/枚举/长度/资源存在性） | 不读业务状态 |
| `ReadinessService` | 聚合"能否回话"的检查项（§6.5） | 不修改任何状态 |

### 6.3 应用引擎（7 步事务，稳定性的核心）

```
apply(bundle_id, account_id, secrets, plan):
 1. ACQUIRE   全局导入锁 + 账号锁（asyncio.Lock）；status=applying 落库（含 plan/snapshot=null）
 2. PLAN      若未给 plan：Planner.build()（与预检同一函数）；再次校验（防预检后状态变化）
 3. SNAPSHOT  读取将被覆盖的全部对象（agent config、模块 config/authority、引用的预设、active_bundle）
              写入 bundle_activations.snapshot_json，status 落库
 4. MATERIALIZE  在内存构造全部新对象（新 id、引用重写、密钥注入）
 5. WRITE     单事务写入：role_presets / provider_presets / provider_models / bindings / bundle_objects
              （SQLite 单事务；文件系统只动 staging，不碰 modules/）
 6. VERIFY    回读并比对：每个对象存在且值等于计划；readiness 重算（模型入池/开关状态）
              status=active；写 applied_json；清空 snapshot（保留 plan 供审计）
 7. RELEASE   解锁；WS 广播 bundle_activated
 失败（任意步）→ rollback(activation_id)：用 snapshot 恢复 + 删除已创建对象 + status=rolled_back
 崩溃恢复：启动时扫描 status='applying' → 自动 rollback（幂等：snapshot 为 null 则只清锁并标 failed）
```

**关键设计点**：

- `snapshot` 只存**被覆盖的对象**（不是全库），体积可控；格式与配置存储一致，回滚 = 写回。
- **写入必须走"批量事务"接口**，不能直接调现有 `save_*`：现状是 `binding_store.py:147-148` 先改内存、`_schedule_persist` 异步落盘，`:168-192` 落盘失败只记日志（接口已返回 200）。因此新增
  `ConfigService.apply_ops(ops: list[ConfigOp]) -> None`：在一个 DB 事务里逐表 `_persist_*`（同步、不排队），任一失败整体抛错 → Applier 用快照回滚。这是 P3 的第一个提交，也是 S2 的修复。
- **不移动/删除模块目录**：模块安装走既有 installer（有自己的 staging/备份），Bundle 只做"引用/可选随包安装"。
- **幂等**：同一 `(bundle_id, account_id)` 已 `active` 时，再次 apply 返回 `noop=true`；`force=true` 才重建（先回滚再应用）。
- **并发**：全局 `_import_lock` + 每账号 `_account_locks[account_id]`；同一包并发导入由 `bundles.id` 主键兜底（第二个请求 409 `BUNDLE_IN_PROGRESS`）。
- **对象创建必须显式给新 id 且校验不存在**：现状 `provider_preset_service.create_preset` 允许调用方传 `id`（`:210`），且落库是 `INSERT OR REPLACE`（`config_service.py:373-386`）——**不校验就会顶掉目标机已有预设**。Bundle 一律 `uuid4()` 生成并在写入前 `get_*` 判空（A6）。

### 6.4 配置值校验器（导入的前置条件，顺手补 S1）

从 schema 生成规则，`validate_value(field, value) -> None | Issue`：

| schema 声明 | 校验 |
|---|---|
| `type: text/textarea/password` | 字符串；长度上限（textarea 默认 100k）；password 允许哨兵 |
| `type: number/integer/float` | 数值类型、`min`/`max`、整数位（现状 `temperature` 无范围：`provider_model_service.py:87-92`） |
| `type: boolean/bool` | 布尔（拒绝 `"true"` 字符串） |
| `type: select` | 值必须在 `options` 的 key 里 |
| `type: string_list` | 列表 + 每项非空字符串 + 条数上限（注意 `provider_model_pool` 的 schema 写成 `string` 而运行时是 list，**按"声明宽松、消费严格"补一条特例表**） |
| `type: list/repeater` | 列表 + 按 `template` 逐项校验 |
| `type: dynamic` | 对象 + 键白名单（已知键）+ 值类型 |
| `type: resource/resources` | 值必须存在于 `ResourceRegistry`（悬挂按 `on_missing` 策略） |
| **枚举型字符串**（模块权限 `group_mode`/`user_mode`） | 必须 ∈ {`whitelist`,`blacklist`}，否则拒绝——现状非法值在 `authority.py:184-198` 等同放行（安全洞） |
| 未知键 | 拒绝（导入路径）／忽略并回显（人工保存路径，保持兼容） |

**用它同时修两条现状**：模块配置保存（`modules.py` update_config）与 Agent 配置保存（`agent.py` POST /config）都接一层校验——**人工写错也当场报错**，而不是等导入时才暴露。

### 6.5 就绪服务（把沉默变成诊断）

```
GET /api/readiness → {
  ok: false,
  ready: false,
  checks: [
    {id:"python",       ok:true,  level:"info",    title:"Python 3.10+",   detail:"3.12.4"},
    {id:"account",      ok:false, level:"blocker", title:"还没有账号",     action:{route:"/bots", label:"添加账号"}},
    {id:"connected",    ok:false, level:"blocker", title:"账号未连接",     detail:"上次错误: …", action:{route:"/bots", label:"去连接"}},
    {id:"model_pool",   ok:false, level:"blocker", title:"Agent 未选择模型", action:{route:"/agent/basic", label:"选择模型"}},
    {id:"group_enable", ok:false, level:"blocker", title:"群聊回复未开启",   action:{route:"/agent/basic", label:"开启群聊回复"}},
    {id:"ws_listen",    ok:false, level:"warn",    title:"反向 WS 未监听", detail:"默认 3001 端口没有对端接入"},
    {id:"modules_error",ok:true,  level:"info",    title:"模块加载正常",   detail:"23 个"}
  ]
}
```

- **同一份检查**用于：启动日志横幅（一次）、WebUI 就绪卡（轮询 5s）、首启向导的完成判定。
- 每个 `check` 必须给 `action`（路由或 API），保证"点一下就能修"。
- `blocker` 决定 `ready=false`；`warn` 不影响 `ready`，但要在卡片里显示。

### 6.6 API 契约

统一响应：成功 `{ok:true, ...}`；失败 `{ok:false, code, message, hint, details}`（HTTP 4xx/5xx）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/bundles` | 列表：`{bundles:[{id,name,version,status,size,modules,activations:[{account_id,status}]}], total}` |
| GET | `/api/bundles/{id}` | 详情：manifest + 关联账号 + 可用更新 + README |
| POST | `/api/bundles/validate` | `multipart zip` → 预检报告（不落库，附 `token` 供 import 复用同一份计划） |
| POST | `/api/bundles/import` | `multipart zip` + `token?` + `secrets` → 登记安装，返回 `{id, plan}`（**不应用**） |
| POST | `/api/bundles/{id}/apply` | `{account_id, secrets?, force?}` → `{activation_id, plan, result}` |
| POST | `/api/bundles/{id}/rollback` | `{account_id}` → 恢复快照，`{restored:[…]}` |
| DELETE | `/api/bundles/{id}` | `?purge=true` 同时清理本包创建对象（需先无 active 激活） |
| GET | `/api/bundles/export` | `?account_id=` → 流式 zip（`Content-Disposition`） |
| GET | `/api/bundles/{id}/download` | 重新下载已安装包（本地留存副本） |
| GET | `/api/readiness` | 就绪检查（§6.5） |
| GET | `/api/bindings` | `?account_id=` 返回该账号全部能力的有效 binding + 来源（导出与能力控制台共用；现状 `list_bindings` 没有路由） |
| POST | `/api/setup/quick-model` | 向导专用：一次完成"建预设 + 建模型 + 入池" |
| POST | `/api/setup/test-model` | 复用 provider 测试通道（`provider_preset_service.test_preset`） |
| POST | `/api/setup/complete` | 标记首启完成（kv） |

**预检报告结构（前端直接渲染）**：

```jsonc
{
  "ok": true, "token": "v1.<hash>",
  "bundle": { "id":"group-chat-companion", "name":"群聊搭子", "version":"1.2.0" },
  "summary": { "modules": 2, "presets": 3, "keys": 18, "size": 41231, "secrets_required": 1 },
  "issues": [
    {"level":"blocker","code":"BUNDLE_MODULE_MISSING","title":"缺少模块 llm_memory",
     "detail":"已安装 v0.9.0，要求 >=1.0.0","fix":"升级后再导入"},
    {"level":"warn","code":"BUNDLE_KEY_IGNORED","title":"忽略 3 个不适用键","detail":["api_base","retry_attempts"]}
  ],
  "changes": [
    {"target":"agent","kind":"config","diff":[{"key":"group_enable","from":false,"to":true}]},
    {"target":"module:llm_memory","kind":"config","diff":[…]},
    {"target":"agent","kind":"create","object":"role_preset:群聊搭子"},
    {"target":"agent","kind":"create","object":"provider_model:deepseek-chat"}
  ],
  "secrets": [{"key":"providers[0].api_key","label":"DeepSeek API Key","required":true}],
  "conflicts": [{"kind":"same_name","object":"role_preset","name":"群聊搭子","resolution":"create_with_suffix"}]
}
```

### 6.6.1 应用与回滚的请求/响应（前端照此渲染）

```jsonc
// POST /api/bundles/group-chat-companion/apply
{ "account_id": "acc1", "secrets": { "providers[0].api_key": "sk-..." }, "force": false }

// 200（成功）
{
  "ok": true,
  "activation_id": "act_7f3a",
  "noop": false,
  "result": {
    "created": [
      {"kind":"role_preset","name":"群聊搭子","id":"rp_a1"},
      {"kind":"provider_preset","name":"DeepSeek 官方","id":"pp_b2"},
      {"kind":"provider_model","name":"deepseek-chat","id":"pm_c3"}
    ],
    "updated": [
      {"target":"agent","keys":[{"key":"group_enable","from":false,"to":true},
                                {"key":"persona_preset_id","from":"","to":"rp_a1"}]},
      {"target":"module:llm_memory","keys":[{"key":"memory_enable","from":false,"to":true}]}
    ],
    "skipped": [{"module":"llm_proactive","reason":"not_installed","severity":"warn"}],
    "readiness_after": {"ready": true, "blockers": []}
  }
}

// 409（版本/依赖不满足）——details 里给出完整清单，前端直接列表展示
{ "ok": false, "code": "BUNDLE_MODULE_VERSION", "message": "缺少满足条件的模块",
  "hint": "先升级模块再导入", "details": {"modules":[{"name":"llm_memory","want":">=1.0.0","have":"0.9.0"}]} }

// POST /api/bundles/group-chat-companion/rollback
{ "account_id": "acc1" }
// 200
{ "ok": true, "status": "rolled_back",
  "restored": [{"target":"agent","keys":["group_enable","persona_preset_id"]}],
  "deleted": [{"kind":"role_preset","id":"rp_a1"},{"kind":"provider_preset","id":"pp_b2"}] }
```

**前端规则**：`result.created/updated/skipped` 三段必须分别渲染（用户要看到"哪些没做、为什么"）；
失败一律给 `code + hint`，不支持"只显示 message"。

### 6.7 与现有零件的接线点（复用而非重造）

| 现有零件 | 位置 | 复用方式 |
|---|---|---|
| 事务化 zip 安装（staging/备份/原子替换/布局容错/回滚） | `app/plugin/installer.py`（限制 `:37-42`、布局归一 `:77-123`、`preflight:170-209`、安装 `:212-304`、`rollback:283`、`_atomic_install:494`、`_backup_existing:504`） | Bundle 的"可选随包模块"与包解压复用同一套安全逻辑（路径穿越 `_safe_parts:392-429`、符号链接 `_is_symlink:451`） |
| zip 安全（大小/文件数） | `installer.py:370-375` | **需补一条**：它按中央目录"声明值"累加，**不实测解压后大小**（可被伪造头绕过）。Bundle 必须在解压时逐条计数 |
| 配置中心读写 | `app/infrastructure/config/config_service.py`（`save_* :310-344` 等、`INSERT OR REPLACE :373-386`）；`binding_store.py`（`list_bindings:36-52`、`set_binding:118`、`save_binding:151`、`get_all_module_configs:373`、`save_module_config:388`、`save_module_authority:433`） | BundleApplier 走新增 `apply_ops`（事务）；快照从 `get_binding(capability, "account", account_id)` 读取；`list_bindings` 目前**没有 WebUI 路由**，P2 要补 `GET /api/bindings` |
| 绑定解析 | `app/domain/binding.py`（`resolve:101-148`） | **不改动**（A1：不做运行时合并） |
| 预设服务 | `role_preset_service.py`（create `:72-149`、delete `:151-155`）、`provider_preset_service.py`（`_public_preset:78-86`、`list_references:187`、create `:196-222`（id 可外部传入 `:210`）、delete 级联 `:256-289`）、`provider_model_service.py`（`list_references:215-220`） | 导入建对象走 create；删除前复用 `list_references`；**P2/P3 需补**：引用检查覆盖 `provider_model_pool`（运行期真正消费处 `runtime.py:274-281`），`role_preset` 删除补引用清理 |
| schema 与值校验 | `app/plugin/schema_utils.py`（`SCHEMA_VERSION:21`、`KNOWN_TYPES:32`、`normalize_field:57-62`）、`resource_validation.py`（`apply_on_missing`） | `ConfigValueValidator` 在其上加"值"层；注意现状只"诊断不拒收" |
| 模块编目/版本 | `app/plugin/catalog.py:70-117`、`:173-237`；`manifest.py:57-104`（`api_version`） | 版本约束解析的数据源；补"包内版本 vs 已装版本"比较 |
| 密钥脱敏 | `PASSWORD_MASK`（`app/application/bot_service.py`）、`_mask_password_config` | 导出与预检统一用它；**前置修复**：`provider_preset_service._public_preset` 目前不回掩码（S7） |
| dry-run 交互形态 | `POST /api/modules/validate-zip` + `InstallPreview` | Bundle 预检照抄交互与文案结构 |
| 聚合读取 | `app/application/bot_service.py::get_modules_data:225-329`；`GET /api/modules?account_id`（`webui/api/modules.py:307-310`） | 导出时一次取该账号全部模块配置/权限/开关（password 已打码） |
| **zip 打包/还原先例** | `app/application/session_history_service.py:487-534`；`webui/api/sessions.py:264-293` | 包生成、随包媒体、解压时 basename 白名单——Bundle 导出可直接照抄结构 |
| 日志/WS | `app/webui/ws.py`、`app/core/logger.py` | 新增 `bundle_*` 事件；导入过程逐项广播进度 |

### 6.8 迁移与兼容

| 情况 | 处理 |
|---|---|
| 包 schema 版本 `< 当前` | 走 `BundleMigrator.migrate(manifest, from_version)`；无迁移函数则拒绝并提示 |
| 包 schema 版本 `>` | 拒绝（`BUNDLE_SCHEMA_UNSUPPORTED`） |
| 框架 `api_version` 不足 | 拒绝并给"需要 ≥x.y.z" |
| 模块版本不满足 | required → 拒绝；optional → 跳过 + warning |
| 已装同 id 不同版本 | 升级流程：先预检 diff（新增/删除的键），保留用户"改过的键"（见 §8.1 I5） |
| 旧数据 | 无需迁移（新增表）；`bundles` 空即"没用过套装" |

### 6.9 安全

| 面 | 措施 |
|---|---|
| zip 攻击 | 路径穿越/绝对路径/盘符/符号链接拒绝；**解压时逐条实测大小**（≤200MB）与文件数（≤5000）、单文件 ≤50MB；**不信任中央目录声明值**（现状 `installer.py:370-375` 的漏洞）；解压深度 ≤32 |
| 代码执行 | **包内不执行任何代码**；`modules/*.zip` 只能走 `ZipPluginInstaller` 通道（有审计日志、可卸载、失败回滚） |
| 密钥（导出侧） | 导出**不依赖接口回读**：直接从内部配置结构取，`password` 字段一律转 `secrets` 声明；内容物全量扫描 `sk-`/`api_key` 模式，命中即拒绝导入（误报需用户显式确认） |
| 密钥（回读侧，前置修复 S7） | `provider_preset_service._public_preset:78-86` 改为恒掩码（`PASSWORD_MASK`），更新时哨兵保留旧值（已有机制）；Agent/模块接口统一用同一套 `_mask_password_config` |
| 密钥（写入侧） | 导入绝不可把 `••••••` 哨兵当真实值落库（现状只有各端点各自特判：`modules.py:404`、`agent.py:167`、`provider_preset_service.py:236`）——`apply_ops` 在统一入口过滤 |
| 路径 | 包内容只写入 `data/bundles/<id>/` 与数据库；不得写到用户指定路径 |
| 日志 | 清单与配置在日志中脱敏；导入导出记录 `bundle_id/version/account/结果` |
| DoS | 校验与解压有超时（30s）；大包走流式处理；并发锁避免重复解压 |
| 卸载边界 | 只删 `bundle_objects` 里本包创建的对象；用户后来改过的对象保留并在结果里列出 |

---

## 7. 前端设计

### 7.1 信息架构与路由

| 路由 | 页面 | 说明 |
|---|---|---|
| `/setup` | 首启向导（全屏，无侧栏） | 未完成时自动进入；可"稍后再配"跳过 |
| `/bundles` | 套装中心 | 列表 + 导入/导出入口 |
| `/bundles/:id` | 套装详情 | 清单/组件/激活账号/更新/卸载/下载 |
| `/` | 总览 | 顶部新增「就绪卡」，替换现有散落的空状态引导 |

侧栏新增两项（放在"总览"之后）：**首次配置**（未完成时高亮 + 角标）、**套装中心**。

**两条用户旅程**（所有页面设计都要能回答"我在哪条上"）：

| 旅程 | 用户 | 路径 | 成功标准 |
|---|---|---|---|
| **A 从零到回话** | 小白 | 双击 `start.bat` → `/setup` 4 步（第 3 步选一个内置套装）→ 就绪卡全绿 → 群里 @ 有回复 | ≤5 步、全程有"下一步" |
| **B 换机/分享/复用** | 老手 | `/bundles` → 导出当前账号（勾选部件）→ 另一台机器 `/bundles` → 导入 → 预检 → 填密钥 → 应用 → 结果 | 配置一致、失败可回滚、密钥只写不读回 |

### 7.2 首启向导（4 步，每步一个提交级功能）

```
┌ 首次配置 ──────────────────────────────────────────────── 步骤 1/4 ┐
│  ① 连接   ──── ② 模型 ──── ③ 人格 ──── ④ 完成                    │
│                                                                   │
│  把 QQ 号接进来                                                    │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │ [1] 安装协议端（NapCat）→ 扫码登录                            │  │
│  │     下载地址 / "我已经装好了"                                 │  │
│  │ [2] 在 NapCat 里添加反向 WebSocket：                          │  │
│  │     ws://127.0.0.1:3001/onebot/v11/ws  （Token 已自动生成）     │  │
│  │     [复制地址] [复制 Token]                                   │  │
│  │ [3] 回到这里填：QQ 号(可选) / 地址 / Token → [测试连接]         │  │
│  └─────────────────────────────────────────────────────────────┘  │
│  ● 已连接 123456789  ✓                       [稍后再配]  [下一步] │
└───────────────────────────────────────────────────────────────────┘
```

| 步 | 内容 | 完成判定 | 跳过后果 |
|---|---|---|---|
| 1 连接 | NapCat 三步指引 + 地址/Token 表单 + `测试连接`（**参考 NapCat 一键包：无需预装 QQ 也能先给出三条命令**） | 账号存在且 `connected` | 可跳过，总览显示 blocker |
| 2 模型 | Provider 二选一（预设下拉 / 自定义 api_base）+ 填 Key + `拉取模型` + 勾选 → 自动入池 | `provider_model_pool` 非空且连通测试通过 | 可跳过（Agent 不会回复） |
| 3 人格 / 套装 | 两个 tab：**选套装**（推荐，来自 `/api/bundles` 与内置默认套装）/ **从零写人格**（文本框 + 预设模板）；选套装时内嵌 §7.4 的预检 | `persona_preset_id` 或 `system_prompt` 非空 | 可跳过（用默认人设） |
| 4 完成 | 就绪清单（全部 ✓）+ **模块加载失败列表（可单击重载，不重启）** + "在群里 @ 我试试" + 常见问题三条 | 用户点"完成" | 写 `setup_completed_at`，不再自动进入 |

**两条来自成熟项目的硬规矩**（AstrBot/NapCat 的首启体验）：

- **首启打印"入口 + 一次性凭据"并要求改密**：本项目已有 `WEBUI_AUTO_OPEN` 与带 token 的登录链接，补"随机访问令牌 + 首次登录提示修改"即可。
- **"装完"必须等于"已生效"**：第 4 步的清单与 `ReadinessService` 同一数据源；任何一项红，都不显示"完成"。

**硬规则**：第 2 步的"勾选模型"与"入池"必须**同一步完成**（现状要跳三次页，是 TOP3 卡点）；第 4 步的清单与 `ReadinessService` **同一数据源**。

### 7.3 总览页：就绪卡（未就绪 / 已就绪）

```
┌ 运行状态 ─────────────────────────────────────────────────────────┐
│ ⚠ 还不能回复消息 · 2 个问题待解决                                   │
│  ✗ 还没有账号                              [添加账号 →]            │
│  ✗ Agent 未选择模型                        [选择模型 →]            │
│  ✓ 已连接 1 个账号 · 群聊回复已开启                                │
└───────────────────────────────────────────────────────────────────┘
                                  ↓ 全部通过后
┌ 运行状态 ─────────────────────────────────────────────────────────┐
│ ✓ 一切就绪 · 123456789 已连接 · 群聊回复已开启 · 2 个模型可用        │
│ 在群里 @ 我试试；有问题去 [日志] 看「为什么不回」                    │
└───────────────────────────────────────────────────────────────────┘
```

- 数据来自 `GET /api/readiness`（5s 轮询，页面隐藏时暂停）。
- 每条 `blocker` 必须带按钮跳转（`action.route`）。
- 点"为什么不回"跳到日志页并预置过滤条件 `tag=skip`（需要后端把 §1.3 的静默点都打成该 tag 的 WARN）。

### 7.4 套装中心

**列表页**

```
┌ 套装中心 ────────────────────────────── [导入套装] [导出当前账号] ┐
│ ┌───────────────────────┐ ┌───────────────────────┐              │
│ │ 群聊搭子        v1.2.0 │ │ 客服助手        v0.9.3 │              │
│ │ 内置 · 2 模块 · 3 预设 │ │ 第三方 · 1 模块        │              │
│ │ 已用于：主号 123456789 │ │ 未使用                 │              │
│ │ [详情] [应用到账号…]   │ │ [详情] [应用到账号…]   │              │
│ └───────────────────────┘ └───────────────────────┘              │
│ 空态：还没有套装。从「导出当前账号」生成第一个，或导入别人分享的 .zip │
└───────────────────────────────────────────────────────────────────┘
```

**导入对话框（5 屏，逐屏可达）**

```
① 选择文件       ② 预检报告        ③ 填写密钥        ④ 应用          ⑤ 结果
  拖入 .zip   →   将安装什么/冲突 →  1 个必填项   →   进度逐项    →   ✓/✗ + 回滚
```

② 预检报告是整套设计的"信任界面"，必须包含四块：

```
┌ 预检：群聊搭子 v1.2.0 ────────────────────────────────────────────┐
│ 将创建：角色预设 ×1、Provider 预设 ×1、模型 ×1、模块配置 ×2        │
│ 将修改：主号 123456789 的 6 个键                                   │
│   group_enable     关 → 开                                        │
│   persona_preset_id  (空) → 群聊搭子                              │
│ 依赖：✓ llm_memory 1.2.0   ⚠ llm_proactive 未安装（可选，将跳过）   │
│ 需要你填：① DeepSeek API Key                                      │
│ 风险：无                                                          │
│                              [取消]  [继续：填写密钥]              │
└───────────────────────────────────────────────────────────────────┘
```

⑤ 结果屏（逐项 + 可回滚）：

```
✓ 已创建 角色预设「群聊搭子」
✓ 已创建 Provider 预设 + 模型（deepseek-chat）
✓ 已更新主号 6 个键（group_enable 已开启）
⚠ 跳过 llm_proactive 配置（模块未安装）
[立即在群里 @ 试试]  [回滚这次应用]  [完成]
```

**详情页**：组件清单（模块/预设/配置）、已激活账号、更新检查、卸载（**弹窗里双勾选：删除本包创建的配置 / 保留**，参考 AstrBot 的删除选项）、下载/分享。

**导入对话框的两个硬性交互**（来自 1Panel/AstrBot 的经验）：

- **全程日志可复制**：`[复制日志]` 按钮，内容含 bundle_id/version/每步耗时/失败原文——用户求助时不用截图。
- **失败项可单独重试**：应用结果是"逐项状态"，失败项旁边有 `[重试此项]`，不必整包重来。

**导出对话框的"保存导出配置"**：勾选组合记进 `kv`（按账号），下次打开预填——这是 PCL 的成熟做法，避免每次重复勾。

### 7.5 导出流程

入口三处：套装中心顶部、Agent 面板「导出为套装」、账号卡片「导出该账号配置」。对话框选项：

| 选项 | 默认 | 说明 |
|---|---|---|
| 包含 Agent 配置 | ✓ | 场景开关、行为参数、权限档位 |
| 包含人格 | ✓ | 导出人格全文（不带预设 id） |
| 包含模块 | 仅开关 + 参数 | 不打包代码；勾"随包携带模块"才加 zip（并提示体积） |
| 包含 Provider 连接形态 | ✓ | 只带 provider/api_base/模型名，**密钥永远不带** |
| 密钥处理 | 生成 `secrets` 声明 | 导出时扫描到密钥 → 转为待填项，并提示"接收方需自行填写" |
| **保存这份勾选** | ✓ | 记进 `kv`（按账号），下次预填（PCL 的"保存导出配置"） |

导出后弹"下载 + 说明下一步"：`bundle.json` 可手改；分享前请确认 README 无个人信息。

### 7.6 错误与空态设计（三类，一一对应处理方式）

| 类型 | 例子 | 界面 | 必须给 |
|---|---|---|---|
| 可自愈 | 模块版本低、模型未入池 | 卡片内联提示 + 按钮 | 一条命令/一次点击的修复动作 |
| 需人工 | Key 无效、包损坏、密钥泄漏 | 对话框说明 + 复制详情 | 原因、位置（哪一行/哪个键）、下一步 |
| 需外部 | Python 缺失、NapCat 未装、端口占用 | 引导页 + 文档链接 | 外部步骤清单（不是"请检查网络"） |

**禁止**：`alert()`、只写"失败"、把后端英文错误原样抛给用户（沿用现有 `errorMessage()` 包装，但补齐 `hint`）。

### 7.7 前端组件树与状态

```
views/
  SetupPage.vue                  # 向导外壳（stepper + 路由守卫）
  BundlesPage.vue                # 套装列表
  BundleDetailPage.vue           # 详情 + 激活/卸载
components/
  setup/SetupStepConnection.vue
  setup/SetupStepModel.vue       # 含"拉取模型"与"入池"同屏
  setup/SetupStepPersona.vue     # 套装 tab / 从零 tab
  setup/SetupStepDone.vue        # 复用 ReadinessCheckList
  readiness/ReadinessCard.vue
  readiness/ReadinessCheckList.vue
  bundles/BundleCard.vue
  bundles/BundleImportDialog.vue # 5 屏状态机
  bundles/BundlePreviewReport.vue
  bundles/BundleSecretForm.vue
  bundles/BundleApplyProgress.vue
  bundles/BundleApplyResult.vue
  bundles/BundleExportDialog.vue
stores/
  readiness.ts   # {checks, ready, refresh(), poll 5s}
  bundles.ts     # {list, detail, validate(file), import(), apply(), rollback(), export()}
```

**导入对话框状态机**（store 内显式建模，避免散在组件里）：

```
idle → validating → preview → needs_secrets → importing → applying → done | failed
             ↓ 失败                                   ↓ 失败
          error(可重试)                          failed(可回滚)
```

**WS 事件**（后端广播，前端订阅刷新）：

| 事件 | 载荷 | 用途 |
|---|---|---|
| `bundle_imported` | `{id, version}` | 列表刷新 |
| `bundle_applied` | `{activation_id, bundle_id, account_id, result}` | 详情/总览刷新 |
| `bundle_rolled_back` | `{activation_id, account_id}` | 同上 |
| `readiness_changed` | `{ready, blockers}` | 就绪卡即时更新（可选，轮询兜底） |

### 7.8 文案规范（沿用 `docs/UI_COPY.md`）

- 每句 ≤40 汉字；`0 = 关闭` 这类写法统一。
- 不写黑话（"接管""迁移""schema"）；不写跨控件指路。
- 关键文案示例：
  - 就绪卡：「还不能回复消息」/「Agent 未选择模型 → 选择模型」
  - 预检：「将修改主号 6 个键」/「需要你填：DeepSeek API Key」
  - 应用失败：「没有改成任何东西（已自动恢复）」+ `[查看原因]`
  - 回滚：「已恢复到应用前」+ `[再试一次]`

---

## 8. 稳定性：不变式、失败矩阵、测试

### 8.1 六条不变式（写进 `tests/` 与 review checklist）

| # | 不变式 | 守护测试 |
|---|---|---|
| I1 | **物化后无运行时合并**：`domain/binding.py` 的 `resolve()` 不感知 bundle；bundle 只写库 | 断言 `binding.py` 无 `bundle` 引用；`test_bundle_does_not_touch_resolver` |
| I2 | **预检=应用**：同一 `plan` 对象；预检后状态变化导致计划失效 → 重新预检，不"带病执行" | `test_plan_signature_changes_on_state_drift` |
| I3 | **全成或全退**：任何一步异常后，被触及的对象与快照逐字节一致 | `test_apply_failure_restores_byte_equal` |
| I4 | **崩溃可恢复**：`applying` 记录在启动时自动回滚 | `test_startup_recovery_rolls_back_applying` |
| I5 | **不覆盖用户改动**：升级/重复应用时，用户改过的键列为冲突并保留（或显式选择） | `test_upgrade_preserves_user_edits` |
| I6 | **密钥零落盘**：包、日志、API 响应中不出现密钥明文 | `test_no_secret_in_bundle_or_log_or_response` |

### 8.2 失败矩阵（每一种都要有确定表现）

| 失败点 | 用户看到 | 系统状态 | 是否可重试 |
|---|---|---|---|
| zip 非法/超限 | 「不是有效的套装包」+ 原因 | 无变化 | 换包 |
| 清单缺字段 | 逐条列出缺失项 | 无变化 | 改包 |
| 框架版本过低 | 「需要 QQBot Next ≥ x.y.z」 | 无变化 | 升级框架 |
| 模块缺失（required） | 列出模块 + 当前/要求版本 | 无变化 | 装模块 |
| 配置值非法 | 「键 X 的值不合法（要求 1–60）」 | 无变化 | 改包 |
| 密钥缺失 | 停在"填写密钥"这一步 | 无变化（未 apply） | 填完继续 |
| 写库失败 | 「没有改成任何东西（已自动恢复）」+ 详情 | 回滚完成 | 可重试 |
| 应用中途进程崩溃 | 重启后：该次应用标记「已自动回滚」 | 启动恢复 | 可重试 |
| 并发应用同账号 | 「另一个应用正在进行」 | 不变 | 稍后 |
| 回滚失败（极端） | 「需要手动恢复」+ 快照下载 + 操作指引 | 标记 `failed` 并告警 | 人工 |
| 模块随包安装失败 | 复用 installer 回滚；报告哪一步 | 模块目录还原 | 可重试 |

### 8.3 测试清单

**后端单测**：manifest 校验 20 例（缺字段/坏类型/版本/约束/未知键）；值校验器按类型 12 例；`rewrite_refs` 纯函数（含池映射、悬挂资源）；plan 确定性（同输入同输出）。

**集成**：导入→应用→导出 **round-trip**（导出的包再导入，配置等价）；回滚 byte-equal；启动恢复（构造 `applying` 记录）；并发 apply（两协程，一成一 409）；zip-slip/超大/符号链接拒绝；密钥泄漏扫描（构造含 `sk-` 的包被拒）。

**前端**：`vitest`（导入状态机：每个状态迁移 + 失败分支）；手动验收清单（向导 4 步、就绪卡 blocker 跳转、预检报告四块、应用结果回滚）。

**回归**：全量 `pytest -q` + `ruff check .` + 前端 `build`（按 AGENTS.md 大工程收尾档位）。

---

## 9. 分期落地（每步一个 Conventional Commit + 验收）

| 阶段 | 内容 | 规模 | 验收 |
|---|---|---|---|
| **P0 止漏**（本周） | ①`group_enable` 静默 return 补 WARN（按账号限流）+ 启动 readiness 横幅；②`start.py:241-244` 不再把退出码 3 折成 2，明确提示"Python 版本不符"；③保存账号校验 `ws_url`（复用 `parse_endpoint`）；④`agent.py` JSON 解析失败不再"返回成功"、非对象体不再 500；⑤修 `release.yml:55` 路径 + `pyproject` 打包 dist（或明确不含并给指引）+ **用干净构建替代 `prune_dist.py`**（`vite.config.ts` 关掉 `emptyOutDir` 会让旧 chunk 堆积；`prune_dist.py:42-55` 又会误删懒加载 chunk → 改为 build 到临时目录后整体替换）；⑥README 前置条件 + NapCat 三步 + 故障排查 | 1–1.5d | 群里不回时有日志可查；wheel 装出来能看到界面；release 能产出可下载包且前端完整 |
| **P1 向导 + 诊断**（1 周） | `/api/readiness` + `/api/setup/*` + `SetupWizard` 4 步 + 总览就绪卡 + "拉取模型即入池" + **模块加载失败列表（可单插件重载）** + 首启令牌提示 | 3–5d | 新机器从零到"群里 @ 有回复"≤5 步、全程有下一步提示；任一红项都不能"完成" |
| **P2 工件 v1（只导出/导入/预检）** | `bundle.json` 规范 + `BundleValidator` + `BundlePlanner`（仅计划）+ `BundleExporter` + `POST /validate|/import` + 套装中心列表/详情 + 导入预检 UI | 5–7d | 导出→删账号→导入→配置一致；预检四块信息完整；非法包全部被拒且原因可读 |
| **P3 实例化 + 回滚** | `bundles`/`bundle_activations`/`bundle_objects`/`account_active_bundle` + `ConfigService.apply_ops` 事务 + `BundleApplier` 7 步 + 启动恢复 + `apply/rollback` UI + 应用结果 | 5–7d | 应用后行为改变；拔电式中断后重启自动回滚；回滚后逐字节还原 |
| **P4 更新/卸载/校验器** | `ConfigValueValidator` 接入人工保存与导入 + 版本约束 + 升级 diff + 卸载清理 + 失败注入测试 | 4–6d | 用户改过的键在升级时被保留；卸载只删本包对象；非法值当场被拒 |
| **P5 部署与分发** | NapCat 托管（锁定版本/拉起/二维码转发/自动写反连/健康检查/回滚）+ Deploy Kit（内嵌 Python + wheelhouse + 默认套装）+ 一键更新 | 2–3 周 | 双击 → 扫码 → 回话；更新失败可回滚到上一版 |

**回滚策略**：P0/P1 纯增量；P2 只新增 API 不改现有写路径；P3 之后新增表，旧数据默认"无套装"，`kv.bundle_api_version` 控制开关，出问题可整体停用（读路径不依赖 bundle）。

---

## 10. 明确不做（写进文档，防止第三次分叉）

1. **不做运行时合并层**（`b745b87` 的路）：不在 `AgentConfig.get()`/`binding.resolve()` 里读"当前套装"。
2. **不做包内可执行代码/脚本钩子**：供应链风险；模块代码只能走 zip 安装通道。
3. **不把 `data/app.db`、`data/module/` 整体打包**：不可合并、带密钥、无法演进。
4. **不打包密钥**：任何形式（含"加密后的"）都不进包。
5. **v1 不支持"同账号不同群不同套装"**：若将来要做，精确改动点是 `domain/binding.py`：新增 `SCOPE_BUNDLE`，`SCOPES` 改为 `(global, bundle, account, channel)`，`_matches` 增分支，`resolve(bundle_id=...)` 传入账号的 active_bundle；存储加 `scope_type='bundle'` 行；`binding_store.list_bindings` 无需改（按 capability 全查）。**这是独立项目，不进 v1**。
6. **不做云端市场/账号体系**：v1 只支持本地文件与 URL 导入；市场是生态问题，不是功能问题。

---

## 11. 参考来源

**Minecraft 整合包**

- PCL2 帮助：[整合包安装](https://github.com/Meloong-Git/PCLHelp/blob/master/Minecraft/整合包安装.xaml)、
  [导出整合包](https://github.com/Meloong-Git/PCLHelp/blob/master/Minecraft/导出整合包.xaml)、
  [整合包制作](https://github.com/Meloong-Git/PCLHelp/blob/master/指南/整合包制作%20-%20Public.xaml)、
  [版本隔离 2.9.2 说明](https://github.com/Meloong-Git/PCL/releases/tag/2.9.2)、
  [整合包更新请求被标 duplicate #9105](https://github.com/Meloong-Git/PCL/issues/9105)
- Modrinth：[`.mrpack` 格式规范](https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack)
- CurseForge：[导出整合包（含 overrides 许可要求）](https://support.curseforge.com/en/support/solutions/articles/9000198500-exporting-a-modpack-for-curseforge-project-submission)、
  [manifest 字段（第三方文档）](https://gdlauncher.com/docs/modpack-manifest-format/)

**QQ 机器人生态**

- AstrBot：[文档导航（部署矩阵）](https://docs.astrbot.app/what-is-astrbot.html)、
  [桌面客户端](https://docs.astrbot.app/deploy/astrbot/desktop.html)、
  [启动器部署](https://docs.astrbot.app/deploy/astrbot/launcher.html)、
  [WebUI（首启密码/备份/诊断包）](https://docs.astrbot.app/use/webui.html)、
  [插件市场与删除选项](https://docs.astrbot.app/use/plugin.html)
- NapCat：[Shell 一键包与可视化管理器](https://napneko.github.io/guide/boot/Shell)
- Koishi：[安装和配置插件（市场/启用/依赖/删除）](https://koishi.chat/zh-CN/manual/usage/market)
- TRSS-Yunzai：[README（四种装法与依赖）](https://github.com/TimeRainStarSky/Yunzai)

**套装/模板产品**

- VS Code：[Profiles（导入导出/Preview）](https://code.visualstudio.com/docs/configure/profiles)、
  [Extension Pack](https://code.visualstudio.com/docs/configure/extensions/extension-marketplace)
- Home Assistant：[Blueprints（重导入覆盖的官方 Caution）](https://www.home-assistant.io/docs/automation/using_blueprints/)
- Docker：[Compose](https://docs.docker.com/compose/)；1Panel：[应用商店安装流程](https://docs.fit2cloud.com/1panel/user_manual/appstore/install)

**项目内文档与代码**

- 作用域与仲裁：[`multi-account-design.md`](multi-account-design.md)
- 身份/开关信息架构：[`identity-and-capability-console.md`](identity-and-capability-console.md)
- 模块安装与统一编目：[`module-management-redesign.md`](module-management-redesign.md)
- 前端架构：[`dashboard-frontend-architecture.md`](dashboard-frontend-architecture.md)
- 文案规范：[`UI_COPY.md`](UI_COPY.md)
- 被删除的「配置档案」提交：`b745b87`（本文 §1.2 的教训来源）

> 未能核实的点（正文已避开断言）：PCL 是否对"缺依赖"有专门弹窗；Yunzai 的口碑统计；
> Obsidian 是否有官方仓库模板。
