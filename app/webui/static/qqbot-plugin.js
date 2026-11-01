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

  // ────────────────────────── 声明式表单（内置模块页用） ──────────────────────────
  // 目标：内置模块的 pages/index.html 只写"字段清单 + 分组"，不重复写 DOM/保存/防抖。

  function el(tag, cls, text) {
    var node = document.createElement(tag)
    if (cls) node.className = cls
    if (text != null) node.textContent = text
    return node
  }

  function debounce(fn, ms) {
    var timer = null
    return function () {
      var args = arguments
      global.clearTimeout(timer)
      timer = global.setTimeout(function () { fn.apply(null, args) }, ms)
    }
  }

  function asList(value) {
    if (Array.isArray(value)) return value.map(String)
    if (value == null || value === '') return []
    return String(value)
      .split('\n')
      .map(function (s) { return s.trim() })
      .filter(Boolean)
  }

  function buildControl(field, onEdit) {
    var type = field.type || 'text'
    if (type === 'switch') {
      var wrap = el('label', 'q-switch')
      var box = document.createElement('input')
      box.type = 'checkbox'
      var track = el('span', 'q-track')
      wrap.appendChild(box)
      wrap.appendChild(track)
      box.addEventListener('change', function () { onEdit(field.key, box.checked) })
      return {
        el: wrap,
        wide: false,
        set: function (v) { box.checked = !!v },
        focus: box,
      }
    }
    if (type === 'select') {
      var sel = el('select', 'q-select')
      var options = field.options || {}
      Object.keys(options).forEach(function (value) {
        var opt = document.createElement('option')
        opt.value = value
        opt.textContent = options[value]
        sel.appendChild(opt)
      })
      sel.addEventListener('change', function () { onEdit(field.key, sel.value) })
      return {
        el: sel,
        wide: false,
        set: function (v) { sel.value = v == null ? '' : String(v) },
        focus: sel,
      }
    }
    if (type === 'number') {
      var holder = el('span', 'q-ctrl-inline')
      var num = el('input', 'q-input q-num')
      num.type = 'number'
      num.inputMode = 'decimal'
      if (field.min != null) num.min = field.min
      if (field.max != null) num.max = field.max
      num.step = field.step != null ? field.step : 1
      holder.appendChild(num)
      if (field.unit) holder.appendChild(el('span', 'q-unit', field.unit))
      var integer = String(num.step).indexOf('.') < 0
      var read = function () {
        var raw = num.value === '' ? (field.min != null ? field.min : 0) : Number(num.value)
        if (!isFinite(raw)) raw = field.min != null ? field.min : 0
        if (field.min != null && raw < field.min) raw = field.min
        if (field.max != null && raw > field.max) raw = field.max
        return integer ? Math.round(raw) : raw
      }
      num.addEventListener('change', function () {
        var value = read()
        num.value = value
        onEdit(field.key, value)
      })
      return {
        el: holder,
        wide: false,
        set: function (v) { num.value = v == null ? '' : v },
        focus: num,
      }
    }
    if (type === 'textarea' || type === 'lines') {
      var area = el('textarea', 'q-textarea')
      area.rows = field.rows || (type === 'lines' ? 5 : 4)
      if (field.placeholder) area.placeholder = field.placeholder
      area.addEventListener('change', function () {
        onEdit(field.key, type === 'lines' ? asList(area.value) : area.value)
      })
      return {
        el: area,
        wide: true,
        set: function (v) {
          area.value = type === 'lines' ? asList(v).join('\n') : v == null ? '' : String(v)
        },
        focus: area,
      }
    }
    var input = el('input', 'q-input')
    input.type = 'text'
    if (field.placeholder) input.placeholder = field.placeholder
    input.addEventListener('change', function () { onEdit(field.key, input.value) })
    return {
      el: input,
      wide: false,
      set: function (v) { input.value = v == null ? '' : String(v) },
      focus: input,
    }
  }

  /**
   * 渲染一张声明式配置表单（内置模块页专用）。
   *
   * spec = {
   *   title, description, mount,
   *   groups: [{ title, hint, open, fields: [{ key, label, hint, type, options, min, max, unit, rows, visible(cfg) }] }]
   * }
   *
   * type: switch | select | number | text | textarea | lines（string_list，一行一个）
   * 保存：改哪个键就只提交哪个键（扁平 patch），防抖 400ms；失败可重试。
   */
  function renderForm(spec) {
    var mount = typeof spec.mount === 'string' ? document.querySelector(spec.mount) : spec.mount || document.body
    if (!mount) throw new QQBOTError(0, '表单挂载点不存在: ' + spec.mount)
    mount.innerHTML = ''
    var bot = QQBOT.plugin({ module: spec.module, accountId: spec.accountId })
    var config = {}
    var pending = {}
    var controls = []
    var saveNow = null

    var root = el('div', 'q-form')
    var head = el('div', 'q-page-head')
    if (spec.title) head.appendChild(el('div', 'q-page-title', spec.title))
    if (spec.description) head.appendChild(el('p', 'q-page-desc', spec.description))
    root.appendChild(head)

    var status = el('div', 'q-status')
    status.dataset.state = 'idle'
    var statusText = el('span', '', '')
    var retryBtn = el('button', 'q-retry', '重试保存')
    retryBtn.type = 'button'
    retryBtn.hidden = true
    status.appendChild(statusText)
    status.appendChild(retryBtn)
    root.appendChild(status)

    var banner = el('div', 'q-error-banner')
    banner.hidden = true
    root.appendChild(banner)

    var body = el('div', 'q-form-body')
    root.appendChild(body)
    mount.appendChild(root)

    function setStatus(state, text, retry) {
      status.dataset.state = state
      statusText.textContent = text || ''
      retryBtn.hidden = !retry
    }

    function refreshVisibility() {
      controls.forEach(function (item) {
        var visible = item.field.visible ? !!item.field.visible(config) : true
        item.row.hidden = !visible
      })
    }

    function onEdit(key, value) {
      config[key] = value
      pending[key] = true
      refreshVisibility()
      setStatus('dirty', '未保存…')
      if (saveNow) saveNow()
    }

    // 建分组与字段
    ;(spec.groups || []).forEach(function (group) {
      var card = el('details', 'q-card')
      if (group.open !== false) card.open = true
      var summary = el('summary')
      summary.appendChild(el('span', '', group.title || '设置'))
      if (group.hint) summary.appendChild(el('span', 'q-card-hint', group.hint))
      card.appendChild(summary)
      var cardBody = el('div', 'q-card-body')
      card.appendChild(cardBody)
      ;(group.fields || []).forEach(function (field) {
        var row = el('div', 'q-row')
        var main = el('div', 'q-row-main')
        main.appendChild(el('div', 'q-label', field.label || field.key))
        if (field.hint) main.appendChild(el('div', 'q-hint', field.hint))
        var ctrl = el('div', 'q-ctrl')
        var control = buildControl(field, onEdit)
        if (control.wide) {
          row.classList.add('q-wide')
          ctrl.style.width = '100%'
        }
        ctrl.appendChild(control.el)
        row.appendChild(main)
        row.appendChild(ctrl)
        cardBody.appendChild(row)
        controls.push({ field: field, row: row, control: control })
      })
      body.appendChild(card)
    })

    function applyConfig(cfg) {
      config = cfg && typeof cfg === 'object' ? cfg : {}
      controls.forEach(function (item) { item.control.set(config[item.field.key]) })
      refreshVisibility()
    }

    function failure(err) {
      var text = err && err.status === 401 ? '登录状态已失效，请刷新 Dashboard 后重试' : (err && err.message) || '请求失败'
      banner.hidden = false
      banner.textContent = text + '（点右上角「重试保存」可再试一次）'
    }

    saveNow = debounce(async function () {
      var patch = {}
      Object.keys(pending).forEach(function (key) { patch[key] = config[key] })
      if (!Object.keys(patch).length) return
      setStatus('saving', '保存中…')
      try {
        await bot.saveConfig(patch)
        Object.keys(patch).forEach(function (key) { delete pending[key] })
        banner.hidden = true
        setStatus('saved', '已保存')
      } catch (err) {
        setStatus('error', '保存失败', true)
        failure(err)
      }
    }, 400)

    retryBtn.addEventListener('click', function () {
      if (saveNow) saveNow()
    })

    setStatus('saving', '加载中…')
    if (bot.accountId == null) {
      banner.hidden = false
      banner.textContent = '还没有选择账号：先在 Dashboard 顶部选中一个账号，再回来改配置。'
      setStatus('idle', '')
      return bot
    }
    bot.getConfig()
      .then(function (data) {
        applyConfig((data && data.config) || {})
        setStatus('saved', '已加载')
      })
      .catch(function (err) {
        banner.hidden = false
        banner.textContent = '配置读取失败：' + ((err && err.message) || '未知错误')
        setStatus('error', '读取失败')
      })
    return bot
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
    /** 渲染声明式配置表单（内置模块页专用，见 renderForm 注释） */
    form: renderForm,
    QQBOTError: QQBOTError,
  }

  global.QQBOT = QQBOT
})(typeof window !== 'undefined' ? window : this)
