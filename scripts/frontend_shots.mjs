#!/usr/bin/env node
/**
 * 前端改动验收截图脚本（本地开发用，不进 CI）。
 *
 * 为什么要这一步：前端问题（文字被挤成竖排、按钮被挤出屏幕、选中态文字跳动）
 * 类型检查与单测都发现不了，必须看真实渲染。脚本把「登录 → 逐路由截图 → 量布局」
 * 做成一条命令，避免每次手写 Chrome 命令。
 *
 * 依赖：puppeteer-core 已是 app/webui/dashboard 的 devDependency（`npm install` 会装上），
 * 脚本自己按 dashboard/node_modules 定位它；缺失时先 `cd app/webui/dashboard; npm install`。
 * 它不进生产依赖，CI 也不需要装浏览器。
 *
 * 用法（先起预览服务，见 docs/FRONTEND_VERIFY.md）：
 *   node scripts/frontend_shots.mjs \
 *     --base http://127.0.0.1:9311 --token <WEBUI_TOKEN> \
 *     --routes /ai,/reply --widths 1440,390 --out logs/review/shots
 *
 * 产出：
 *   <out>/<width>/<route 转义>.png      —— 路由截图（可加 --full-page）
 *   <out>/metrics.json                  —— 每个路由的 innerWidth/scrollWidth/超宽元素
 * 退出码：发现横向溢出（scrollWidth > innerWidth + 1）时非 0，可当闸门用。
 */
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import fs from 'node:fs'
import path from 'node:path'

function parseArgs(argv) {
  const args = { widths: '1440,390', routes: '/', out: 'logs/review/shots', fullPage: false }
  for (let i = 2; i < argv.length; i += 1) {
    const key = argv[i]
    if (!key.startsWith('--')) continue
    const name = key.slice(2)
    if (name === 'full-page') {
      args.fullPage = true
      continue
    }
    args[name] = argv[i + 1]
    i += 1
  }
  return args
}

/** puppeteer-core 是纯 ESM 包，用 createRequire 只做「定位」，真正加载走 import()。 */
async function loadPuppeteer() {
  const anchors = [
    path.resolve('app/webui/dashboard/package.json'),
    path.resolve('package.json'),
  ]
  for (const anchor of anchors) {
    if (!fs.existsSync(anchor)) continue
    const require = createRequire(anchor)
    try {
      const entry = require.resolve('puppeteer-core')
      return (await import(pathToFileURL(entry).href)).default
    } catch {
      /* 换下一个锚点 */
    }
  }
  throw new Error(
    '找不到 puppeteer-core。先在 app/webui/dashboard 下执行：npm i --no-save puppeteer-core',
  )
}

function chromePath() {
  const candidates = [
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    '/usr/bin/google-chrome',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ]
  const hit = candidates.find((p) => fs.existsSync(p))
  if (!hit) throw new Error('找不到 Chrome，可用 --chrome 指定可执行文件路径')
  return hit
}

const args = parseArgs(process.argv)
const puppeteer = await loadPuppeteer()
const base = (args.base || 'http://127.0.0.1:9311').replace(/\/$/, '')
const token = args.token || ''
const routes = String(args.routes).split(',').filter(Boolean)
const widths = String(args.widths).split(',').map((n) => Number(n.trim())).filter(Boolean)
const outDir = path.resolve(args.out)

const browser = await puppeteer.launch({
  executablePath: args.chrome || chromePath(),
  headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--hide-scrollbars'],
})

const page = await browser.newPage()

// 鉴权：登录页支持 /login?accesstoken=<WEBUI_TOKEN> 一键登录并下发 HttpOnly 会话 cookie。
if (token) {
  await page.goto(`${base}/login?accesstoken=${encodeURIComponent(token)}`, {
    waitUntil: 'networkidle2',
  })
}

const metrics = []
for (const width of widths) {
  const height = Number(args.height) || (width < 600 ? 844 : 1000)
  await page.setViewport({ width, height, deviceScaleFactor: 1 })
  for (const route of routes) {
    const url = `${base}/#${route.startsWith('/') ? route : `/${route}`}`
    await page.goto(url, { waitUntil: 'networkidle2' })
    await new Promise((r) => setTimeout(r, Number(args.wait) || 1500))
    const info = await page.evaluate(() => {
      const doc = document.documentElement
      const vw = window.innerWidth
      const over = []
      for (const el of document.querySelectorAll('body *')) {
        const r = el.getBoundingClientRect()
        if (r.width <= 0 || r.height <= 0) continue
        if (r.right > vw + 1) {
          over.push({ tag: el.tagName.toLowerCase(), cls: String(el.className).slice(0, 60) })
        }
      }
      return {
        innerWidth: vw,
        scrollWidth: doc.scrollWidth,
        overflow: Math.max(0, doc.scrollWidth - vw),
        over: over.slice(0, 5),
      }
    })
    const name = route.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '') || 'root'
    const dir = path.join(outDir, String(width))
    fs.mkdirSync(dir, { recursive: true })
    const shot = path.join(dir, `${name}.png`)
    await page.screenshot({ path: shot, fullPage: Boolean(args.fullPage) })
    metrics.push({
      route,
      width,
      ...info,
      screenshot: path.relative(process.cwd(), shot),
    })
    const flag = info.overflow > 0 ? 'OVERFLOW' : 'ok'
    console.log(`${flag.padEnd(8)} ${String(info.overflow).padStart(4)}px ${width}px ${route}`)
  }
}

await browser.close()
fs.mkdirSync(outDir, { recursive: true })
fs.writeFileSync(path.join(outDir, 'metrics.json'), JSON.stringify(metrics, null, 2))
console.log(`\n截图与指标：${path.relative(process.cwd(), outDir)}/metrics.json`)

const broken = metrics.filter((m) => m.overflow > 0)
if (broken.length) {
  console.error(`\n${broken.length} 个路由存在横向溢出：`)
  for (const row of broken) {
    console.error(`  ${row.width}px ${row.route} → +${row.overflow}px`, row.over)
  }
  process.exit(1)
}
