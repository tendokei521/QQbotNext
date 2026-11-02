# 前端改动验证：构建 → 预览 → 截图量测

`AGENTS.md`「验证规则」的前端细则。适用于 `app/webui/dashboard/src/**` 的 `.vue`/`.ts`、前端文案，以及 `app/webui/**` 里注入的内联页面。类型检查、`vue-tsc`、单测全绿也可能坏在渲染上（中文竖排、按钮被挤出 390px、选中态跳 2px），只能靠截图 + 量 DOM 发现。

## 1. 构建（改完必做）

```powershell
cd app\webui\dashboard
npm run typecheck      # vue-tsc --noEmit，比 build 快，先跑
npm run build          # typecheck + vite build → app/webui/dashboard/dist/
cd ..\..\..
```

`dist/` **是进仓库的产物**，页面加载的是它而不是 `src/`：不重新 build 就截图，截的是旧界面。

## 2. 起预览服务（独立测试库，端口 9311）

```powershell
$env:DB_PATH         = "$PWD\logs\review\app.db"
$env:LOG_DIR         = "$PWD\logs\review\logs"
$env:MODULE_DIR      = "$PWD\logs\review\module"
$env:WEBUI_HOST      = "127.0.0.1"
$env:WEBUI_PORT      = "9311"
$env:WEBUI_AUTO_OPEN = "false"
venv\Scripts\python.exe main.py
```

| 项 | 说明 |
|---|---|
| 端口 | 配置默认 9200；预览固定 9311，避免和日常实例抢端口 |
| 鉴权 | `.env` 里没有 `WEBUI_TOKEN` 就是免鉴权。**别写成空字符串**：PowerShell 里 `$env:X=""` 等于删除该变量，会回落到 `.env` 的值 |
| 数据 | `DB_PATH` / `LOG_DIR` / `MODULE_DIR` 全指向 `logs/review/`（`/logs/` 已 gitignore），随便改 |
| 地址 | hash 路由：`http://127.0.0.1:9311/#/ai`，`#` 后面才是路由 |

## 3. 截图 + 量横向溢出

```powershell
# 首次或 node_modules 缺失时：cd app\webui\dashboard; npm install; cd ..\..\..
node scripts\frontend_shots.mjs `
  --base http://127.0.0.1:9311 `
  --routes /ai,/reply,/modules,/stats `
  --widths 1440,390 `
  --out logs\review\shots
```

| 参数 | 默认 | 作用 |
|---|---|---|
| `--routes` | `/` | 逗号分隔的 hash 路由；改共享组件时抽 2~3 个受影响页面一起截 |
| `--widths` | `1440,390` | 桌面 + 手机，**两个都要看** |
| `--out` | `logs/review/shots` | 输出目录 |
| `--wait` / `--token` | 1500 / 空 | `networkidle2` 后再等的毫秒数；预览开了鉴权时传令牌，脚本先走 `/login?accesstoken=` 换 cookie |
| `--height` / `--full-page` / `--chrome` | 390 宽 844、其余 1000 / 关 / 自动探测 | 视口高度、整页截图、指定 Chrome 路径 |

产出：`logs/review/shots/<宽度>/<路由转义>.png`（如 `1440/ai-settings-behavior.png`）+ `logs/review/shots/metrics.json`。
脚本用 `setViewport` 设定 CSS 视口（不是 `chrome --window-size` 那种窗口尺寸），并在 `scrollWidth > innerWidth + 1` 时**退出码非 0**，可直接当闸门。

## 4. 逐张看 + 读 metrics

`metrics.json` 每行 = 一条路由 × 一个宽度：`route`、`width`、`innerWidth`、`scrollWidth`、`overflow`、`over`（最右侧超宽元素，最多 5 个，含 tag 与 class 前缀）、`screenshot`。

| 检查 | 通过标准 |
|---|---|
| 横向滚动 | 所有行 `overflow == 0`（390px 尤其看） |
| 竖排 / 截断 / 重叠 | 中文横排不缩成细条；长文案不半句被裁、不压住按钮；省略号是真 `…` |
| 主操作可见 | 开关、保存、Tab 在 390px 完整可见且可点 |
| 文案 | 页面上就是最终文案，无旧词/开发者用语 |
| 状态不跳动 | hover / 选中 / 展开前后只有颜色、阴影、边框色变 |

## 5. 状态类改动：量「点前 / 点后」坐标差

「看着没动」不算数，`getBoundingClientRect` 差值才算：

```js
const rect = (sel) => page.evaluate((s) => {
  const r = document.querySelector(s).getBoundingClientRect()
  return { x: r.x, y: r.y, w: r.width, h: r.height }
}, sel)
const sel = '.prompt-option--active'          // 换成你改的选择器
const before = await rect(sel)
await page.click('.prompt-option:not(.prompt-option--active)')
await new Promise((r) => setTimeout(r, 300))
const after = await rect(sel)
```

判定：`|dx|`/`|dy|`/`|dw|`/`|dh|` 都 ≤1px 才算稳定（整页滚动造成的统一位移用多元素互比排除）；>1px 就是「文字会跳」，改成恒定边框 + 颜色/阴影高亮这种不占布局的写法。

## 6. 提交

1. **截图不是交付物**：`logs/` 已 gitignore，别把 png 塞进仓库；汇报写清「跑了哪些宽度、哪些路由、`overflow` 是否全 0」。
2. **源码与产物分两个提交**：先 `src/**`（`feat(dashboard): …` / `fix(dashboard): …`），再 `app/webui/dashboard/dist/**`（`build: rebuild dashboard dist`）；文案改动同样跑闸门（见 [UI_COPY.md](UI_COPY.md) §6），预览库里造的测试数据别当成用户真实配置。
