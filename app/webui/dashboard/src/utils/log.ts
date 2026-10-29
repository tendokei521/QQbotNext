/**
 * 日志展示清洗：把后台日志行变成用户看得懂的一句话。
 *
 * 后台 `user.log` 仍带技术前缀（`[app.webui.app:479] 2026-10-28 12:49:52.233 - INFO - [Main] [WebUI] ...`），
 * 这些前缀对开发者有用，对用户是噪音。这里只做**展示层**清洗，不改日志文件本身：
 * - 时间戳：去掉 `[文件:行号] `；
 * - 正文：去掉 `[Main]` 这类内部标签前缀，并合并重复标签（`[WebUI] [WebUI]` → `[WebUI]`）；
 * - `[发送->]` / `[请求->]` 这类本身有含义的标记保留。
 */

/** 内部模块标签：出现时只作为前缀噪音去掉（`[发送->]` 等业务标记不在其中）。 */
const INTERNAL_TAGS = new Set([
  'Main',
  'Service',
  'Module',
  'WebUI',
  'Config',
  'Provider',
  'Gateway',
  'Agent',
  'BotService',
  'Hooks',
  'Scheduler',
  'Memory',
  'Knowledge',
  'Resource',
  'RolePreset',
  'Setup',
  'Stats',
  'Readiness',
  'OneBot',
  'Cache',
  'DB',
  'Task',
  'Readiness',
  'Bundle',
  'Policy',
  'Media',
  'Logger',
])

/** 行首连续标签：`[Main] [WebUI] ` 这种链式前缀。 */
const LEADING_TAGS = /^((?:\[[^\]]{1,24}\]\s*)+)/

/** 时间戳里形如 `[app.webui.app:479] ` 的模块定位前缀。 */
export function cleanLogTimestamp(timestamp: string): string {
  return String(timestamp || '')
    .replace(/^\[[^\]]*:\d+\]\s*/, '')
    .trim()
}

/**
 * 是否是「技术日志」（启动、连接、鉴权等内部细节）。
 *
 * 用户默认只想看「机器人做了什么」；启动地址、反向连接地址、模块预加载数量这类
 * 是排查问题才需要的技术细节，默认收起，可在日志页开关里打开。
 *
 * 注意：
 * - 匹配的是**原始行**，前面可能带 `[Main] [Config] ` 这类内部标签，所以不锚定行首；
 * - 启动文案会变（「已配置装配 N 个账号」/「已按配置装配 N 个账号」都出现过），
 *   这里用宽容匹配，不写死某个版本；
 * - **警告/错误永远不算技术日志**——真出问题时不能被藏起来。
 */
const TECHNICAL_HINTS = [
  /WebUI 启动中/,
  /反向 WS 监听中/,
  /模块预加载完成/,
  /已.*装配\s*\d+\s*个账号/,
  /已启用访问令牌鉴权/,
  /已迁移作用域绑定/,
  /OneBot 实现里/,
]

export function isTechnicalLog(level: string, message: string): boolean {
  const lv = String(level || '').toLowerCase()
  if (lv === 'warning' || lv === 'error') return false
  const text = String(message || '')
  return TECHNICAL_HINTS.some((pattern) => pattern.test(text))
}

/**
 * 正文去噪：连续去掉内部标签前缀，并合并相邻重复标签。
 *
 * 注意只处理**行首**：正文中间出现的 `[xxx]` 通常是引用内容，保持原样。
 */
export function cleanLogMessage(message: string): string {
  let text = String(message || '').trim()
  const match = text.match(LEADING_TAGS)
  if (match) {
    const tags = match[1].match(/\[[^\]]+\]/g) || []
    // 全是内部标签才整段去掉；混入业务标记（如 [发送->]）时保留原样
    const onlyInternal = tags.every((tag) => INTERNAL_TAGS.has(tag.slice(1, -1)))
    if (onlyInternal) text = text.slice(match[1].length)
  }
  // 合并正文里相邻的重复标签 `[WebUI] [WebUI]`
  text = text.replace(/(\[[^\]]+\])\s*\1/g, '$1')
  text = text.replace(LEADING_TAGS, (all) => (all.length < text.length ? '' : all))
  return text.trim() || String(message || '')
}
