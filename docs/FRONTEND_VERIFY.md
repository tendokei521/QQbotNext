# 前端改动验证：截图 + 布局量测

本文件是 `AGENTS.md`「验证规则」在前端（`app/webui/dashboard/**`）的细则。
**后端改动靠测试，前端改动靠测试 + 真实渲染**：类型检查、单测、构建全绿，
页面仍可能坏得不能用——这类问题只有看截图、量 DOM 才抓得到。

## 1. 为什么必须截图（本仓库真实踩过的坑）

| 事故 | 测试/构建是否发现 | 截图是否发现 |
|---|---|---|
| 390px 宽下 Tab 被 flex 压成 ~12px，中文变竖排逐字换行 | 否，`vue-tsc` 与 build 均通过 | 是，一眼可见 |
| 选中态从 `outlined` 切到 `tonal`，1px 边框变粗→内容重排，卡片文字左右跳 2px | 否 | 是（截图对比） |
| 弹窗内 Tab 被滚动容器压扁、按钮被挤出屏幕 | 否 | 是 |
| 长文案在窄屏被容器裁掉半句（"…（未绑定"） | 否 | 是 |
| 某段配置页在 390px 出现横向滚动条，需要左右拖 | 否 | 是，`scrollWidth > innerWidth` 可量 |

结论：**没看过渲染结果就说"改好了"，按 `AGENTS.md` 硬性底线第 4 条算未验证。**

## 2. 适用范围

改动以下任一项都要走完本流程（文案也算，因为文案长度直接决定会不会截断/换行）：

- `app/webui/dashboard/src/**` 的 `.vue` / `.ts`（组件、样式、布局、状态、交互）；
- 面向用户的前端文案（`UI_COPY.md` 相关、页面标题、空态/提示语）；
- 后端注入面板的 HTML（`app/webui/**` 里的内联页面）。

只改 Python 后端、且完全不影响页面结构与文案时，按 `AGENTS.md` 原有分档走即可。

## 3. 五步流程（第 5 步只在改「选中态 / hover / 展开收起」时必须做）

### 第 1 步：静态闸门（改了就必须跑）

```powershell
cd app\webui\dashboard
npm run typecheck      # vue-tsc --noEmit，比 build 快，先跑
npm run build          # typecheck + vite build → app/webui/dashboard/dist/
cd ..\..\..

# 文案 / SFC 结构 / 契约闸门
venv\Scripts\python.exe -m pytest tests\architecture -q
```

> `dist` 是进仓库的构建产物：**截图前必须重新 build**，否则截到的是旧界面，
> 白忙一场（这是最容易犯的错）。

### 第 2 步：起预览服务（独立测试库，不碰真实数据）

```powershell
$env:DB_PATH = "$PWD\logs\review\app.db"      # 独立 sqlite，随便改不心疼
$env:LOG_DIR = "$PWD\logs\review\logs"
$env:MODULE_DIR = "$PWD\logs\review\module"
$env:WEBUI_TOKEN = ""                          # 预览关鉴权，省掉登录步骤
$env:WEBUI_PORT = "9311"
$env:WEBUI_HOST = "127.0.0.1"
$env:WEBUI_AUTO_OPEN = "false"
venv\Scripts\python.exe main.py
```

> 端口固定 9311：容易记、不和开发常用端口打架；`logs/` 已在 `.gitignore` 里，
> 截图/日志/测试库都不会污染仓库。

### 第 3 步：截图 + 量布局（一条命令）

仓库自带脚本 `scripts/frontend_shots.mjs`（`puppeteer-core` 已是
`app/webui/dashboard` 的 devDependency，`npm install` 就装上；只是本地验收工具，
CI 不需要装浏览器）：

```powershell
# node_modules 已就绪可跳过这步
cd app\webui\dashboard; npm install; cd ..\..\..

# 桌面 1440 + 手机 390，两个尺寸都截
node scripts\frontend_shots.mjs `
  --base http://127.0.0.1:9311 `
  --routes /ai,/reply,/ai/settings/behavior `
  --widths 1440,390 `
  --out logs\review\shots
```

产出：

- `logs/review/shots/<宽度>/<路由>.png`：截图，人工逐张看；
- `logs/review/shots/metrics.json`：每条路由的 `innerWidth` / `scrollWidth` / `overflow`
  与最右侧超宽元素（tag + class 前缀）。

脚本在发现 `scrollWidth > innerWidth + 1` 时**退出码非 0**，可以当闸门用。
预览服务开了鉴权（`WEBUI_TOKEN` 非空）时加 `--token <令牌>`，脚本会先走
`/login?accesstoken=` 换 HttpOnly 会话 cookie（不要指望 localStorage——生产鉴权是 cookie）。

**为什么不用 `chrome --headless --window-size=390,844`**：`--window-size` 是**窗口**尺寸，
不是 CSS 视口尺寸。实测（`defaultViewport: null` + 该参数）拿到的 `innerWidth` 是
**512**、`innerHeight` 746——Chrome 有窗口最小宽度，390 根本给不出来。用这种办法
"验证" 390px 布局，实际看的是 512px，结论无效。puppeteer 的
`setViewport({width: 390, height: 844})` 直接设定 CSS 视口，才是真的 390。

### 第 4 步：人工核对清单（截图 + metrics 一起看）

逐张截图确认：

- [ ] **没有竖排/挤扁**：中文按正常横排，没有被压到一字一行；flex 子项没有缩成细条；
- [ ] **没有截断/重叠**：长文案不出现半句话被裁掉、文字压在按钮/图标上；需要省略就真的显示 `…`；
- [ ] **没有横向滚动**：`metrics.json` 里所有 `overflow` 为 0（390px 尤其要看）；
- [ ] **主操作可见**：开关、保存按钮、Tab 在 390px 仍完整可见且可点，不需要横向拖动；
- [ ] **状态不跳动**：hover / 选中 / 展开前后，元素位置不变（只有颜色、阴影、边框色可以变）；
- [ ] **中英文文案对得上**：改过的文案在页面上就是最终文案，没有残留旧词或开发者用语。

### 第 5 步（状态类改动必做）：量"点前 / 点后"的坐标差

"看起来没动"不算数，`getBoundingClientRect` 差值才算数。以"提示词风格三选一"为例
（`.prompt-option--active` 是实际类名，换成你改的那个选项/卡片的选择器）：

```js
// 点击另一个选项前后，量目标元素 rect 差；正常应 dx=dy=dw=dh=0
// （整页滚动造成的统一 dy 不算跳动，用多个元素互相比对即可排除）
const rect = (sel) =>
  page.evaluate((s) => {
    const r = document.querySelector(s).getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  }, sel)

const sel = '.prompt-option--active'
const before = await rect(sel)
await page.click('.prompt-option:not(.prompt-option--active)')
await new Promise((r) => setTimeout(r, 300))
const after = await rect(sel)
// 断言 |after.x-before.x|、|after.y-before.y|、|after.w-before.w|、|after.h-before.h| <= 1
```

判定口径：位置/尺寸差 ≤1px 视为稳定；>1px 就是"文字会跳"，必须改成
恒定边框 + 颜色/阴影高亮这种不占布局的写法。

## 4. 提交要求

1. **截图不算交付物**：`logs/` 被 gitignore，别把 png 塞进仓库；提交信息/汇报里写清
   "跑了哪些宽度、哪些路由、metrics 是否全 0"。
2. **源码与构建产物分两个提交**：先提交 `src/**`（`feat(dashboard): …` /
   `fix(dashboard): …`），再提交 `app/webui/dashboard/dist/**`（`build: rebuild dashboard dist`）。
   混在一起提交会让 review 无法区分"人改的"和"构建出来的"。
3. **文案改动也要跑闸门**：`tests/architecture/test_dashboard_copy.py`、
   `test_ui_copy.py`、`test_vue_sfc_structure.py` 会拦开发者用语、裸 `v-html`、
   结构越界等，改文案后别跳过。
4. 预览库里改过的测试数据（账号、开关、提示词风格）要记着是**测试库**，
   不要拿它当"用户真实配置"下结论。

## 5. 常见坑

- **忘了 build**：`dist` 是提交产物，截图跑的是 `dist` 不是 `src`，改完先 build。
- **hash 路由**：地址形如 `http://127.0.0.1:9311/#/ai`，脚本内部已拼好，
  手写 URL 时注意 `#` 后面才是路由。
- **数据异步**：页面先渲染骨架再拉接口，截图要等 `networkidle2` + 固定延时
  （脚本 `--wait 1500` 默认值就是干这个的）；量 DOM 前确认目标元素已存在，别量到空节点。
- **滚动条**：截图参数默认 `--hide-scrollbars`，避免 Windows 滚动条吃掉宽度造成假溢出。
- **只截 1440 就下结论**：手机端问题全在 390，两个宽度都要看。
- **改完共享组件只验证一个页面**：会被连带影响的页面（侧栏、对话框、公共卡片）抽 2-3 个一起看。
