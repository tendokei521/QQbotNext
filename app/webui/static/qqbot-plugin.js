/*!
 * qqbot-plugin.js —— 模块自定义页（`pages/index.html`）的公共 SDK。
 *
 * 用法（插件页里）：
 *   <script src="/api/plugin-sdk.js"></script>
 *   <script>
 *     const bot = QQBOT.plugin();          // 自动读注入的模块名/账号
 *     const cfg = await bot.getConfig();   // { ok, module, config }
 *     await bot.saveConfig({ greet: 'hi' });// 扁平对象，只传要改的键
 *   </script>
 *
 * 约定：
 * - 页面由框架注入 `PLUGIN_MODULE` / `PLUGIN_ACCOUNT_ID`（兼容 `PLUGIN_BOT_ID`），SDK 直接用；
 * - 鉴权靠同源 HttpOnly 会话 cookie（浏览器自动带）。`WEBUI_TOKEN` 仅作后备：
 *   cookie 失效时 SDK 会带 `Authorization: Bearer <token>` 重试一次；
 * - 配置接口的请求体是**扁平对象**（直接 `{key: value}`），不要再包一层 `{config: ...}`；
 * - 静态资源（css/js/图片）放 `pages/` 下，用 `bot.asset('app.css')` 取 URL。
 *
 * 这个文件只依赖浏览器原生 API（无框架、无构建），也刻意不碰 `window.parent`。
 */
(function (global) {
  'use strict'

  var INJECTED_MODULE = global.PLUGIN_MODULE || ''
  var INJECTED_ACCOUNT =
    global.PLUGIN_ACCOUNT_ID != null ? global.PLUGIN_ACCOUNT_ID : global.PLUGIN_BOT_ID != null ? global.PLUGIN_BOT_ID : null
  var LEGACY_TOKEN = global.WEBUI_TOKEN || ''

  function QQBOTError(status, message, payload) {
    var err = new Error(message || '请求失败')
    err.name = 'QQBOTError'
    err.status = status
    err.payload = payload
    return err
  }

  function withAccount(url, accountId) {
    if (accountId == null || accountId === '') return url
    var sep = url.indexOf('?') >= 0 ? '&' : '?'
    return url + sep + 'account_id=' + encodeURIComponent(String(accountId))
  }

  function parseBody(text) {
    try {
      return JSON.parse(text)
    } catch (e) {
      return { status: 'error', message: String(text || '').slice(0, 500) }
    }
  }

  async function rawRequest(method, url, body, accountId, token) {
    var init = { method: method, credentials: 'same-origin', headers: {} }
    var target = withAccount(url, accountId)
    if (token) init.headers['Authorization'] = 'Bearer ' + token
    if (body !== undefined) {
      init.headers['Content-Type'] = 'application/json'
      init.body = JSON.stringify(body)
    }
    var res = await fetch(target, init)
    var text = await res.text()
    var data = parseBody(text)
    if (!res.ok) throw QQBOTError(res.status, data && data.message ? data.message : res.statusText, data)
    return data
  }

  /**
   * 发起一次带鉴权与账号参数的请求；cookie 失效时用注入令牌重试一次。
   * @param {string} method
   * @param {string} url 以 /api 开头的相对路径
   * @param {any} [body] 有值时按 JSON 发送
   */
  async function request(method, url, body) {
    var accountId = QQBOT.accountId
    try {
      return await rawRequest(method, url, body, accountId, '')
    } catch (err) {
      if (err && err.status === 401 && LEGACY_TOKEN) {
        return await rawRequest(method, url, body, accountId, LEGACY_TOKEN)
      }
      throw err
    }
  }

  function moduleBase(moduleName) {
    return '/api/module/' + encodeURIComponent(String(moduleName || QQBOT.module || ''))
  }

  var QQBOT = {
    /** 当前页面所属模块名（由框架注入） */
    module: INJECTED_MODULE,
    /** 当前选中账号 id（可能为 null：尚未选择账号） */
    accountId: INJECTED_ACCOUNT,

    /**
     * 取一个"绑定到某模块"的实例（多模块共用一个页面时用）。
     * @param {{module?: string, accountId?: string|null}} [opts]
     */
    plugin: function (opts) {
      opts = opts || {}
      var self = {
        module: opts.module != null ? String(opts.module) : QQBOT.module,
        accountId: opts.accountId !== undefined ? opts.accountId : QQBOT.accountId,
      }
      function url(path) {
        return moduleBase(self.module) + path
      }
      Object.defineProperties(self, {
        base: { value: moduleBase(self.module) },
        /** 读取模块配置：`{ ok, module, config }` */
        getConfig: { value: function () { return request('GET', url('/config')) } },
        /**
         * 保存模块配置（扁平对象，只传要改的键）。
         * 成功返回 `{ ok, message, cleared? }`；校验失败抛 `QQBOTError(400)`。
         */
        saveConfig: { value: function (patch) { return request('POST', url('/config'), patch || {}) } },
        /** 页面静态资源 URL（文件放 pages/ 下） */
        asset: { value: function (rel) { return url('/assets/') + String(rel || '').replace(/^\/+/, '') } },
        /**
         * 轮询配置变化（可选；多标签页/外部改动时用）。
         * @returns {() => void} 取消函数
         */
        watchConfig: {
          value: function (onChange, intervalMs) {
            var last = null
            var timer = global.setInterval(async function () {
              try {
                var data = await self.getConfig()
                var serialized = JSON.stringify((data && data.config) || {})
                if (last !== null && serialized !== last) onChange((data && data.config) || {})
                last = serialized
              } catch (e) {
                /* 轮询失败不打断页面，等下一轮 */
              }
            }, Math.max(1000, Number(intervalMs) || 5000))
            return function () { global.clearInterval(timer) }
          },
        },
      })
      return self
    },

    /** 兼容直接调用：等价 `QQBOT.plugin().getConfig()` */
    getConfig: function () { return QQBOT.plugin().getConfig() },
    /** 兼容直接调用：等价 `QQBOT.plugin().saveConfig(patch)` */
    saveConfig: function (patch) { return QQBOT.plugin().saveConfig(patch) },
    /** 兼容直接调用：等价 `QQBOT.plugin().asset(rel)` */
    asset: function (rel) { return QQBOT.plugin().asset(rel) },
    /** 原始请求（自定义接口用） */
    request: request,
    QQBOTError: QQBOTError,
  }

  global.QQBOT = QQBOT
})(typeof window !== 'undefined' ? window : this)
