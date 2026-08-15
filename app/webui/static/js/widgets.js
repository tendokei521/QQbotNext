/* QQBot 配置小组件：string_list / list / dynamic / repeater / showIf。
   由 main.js 在 DOMContentLoaded 时调用 initAllConfigWidgets() 初始化。 */

// ==================== 配置小组件（string_list / list / dynamic / repeater / showIf） ====================
window.__configWidgets = {};

function widgetKey(mod, key) { return mod + '.' + key; }
function getWidget(mod, key) { return window.__configWidgets[widgetKey(mod, key)] || null; }

function escHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** HTML 属性内转义（额外转义单引号，用于内联事件参数）。 */
function escAttr(s) {
    return escHtml(s).replace(/'/g, '&#39;');
}

function parseJsonAttr(raw, fallback) {
    if (raw == null || raw === '') return fallback;
    try { return JSON.parse(raw); } catch (e) { return fallback; }
}

async function fetchJson(url) {
    try {
        const resp = await fetch(url);
        if (!resp.ok) return null;
        return await resp.json();
    } catch (e) { return null; }
}

/** 通用子字段渲染（dynamic / repeater 复用）。返回 { el, getValue, setValue } */
function renderSubField(schema, initial) {
    const wrap = document.createElement('div');
    wrap.className = 'widget-subfield';
    const label = document.createElement('label');
    label.style.cssText = 'font-size:12px;font-weight:600;display:block;margin:6px 0 2px;color:#2d3748;';
    label.textContent = schema.label || schema.key;
    wrap.appendChild(label);
    if (schema.hint) {
        const hint = document.createElement('div');
        hint.style.cssText = 'font-size:12px;color:#718096;margin:2px 0 6px;';
        hint.textContent = schema.hint;
        wrap.appendChild(hint);
    }
    let getValue = () => null, setValue = () => {};
    const type = schema.type || 'string';

    if (type === 'boolean') {
        const sw = document.createElement('label'); sw.className = 'switch';
        const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = !!initial;
        sw.appendChild(cb); sw.appendChild(Object.assign(document.createElement('span'), { className: 'slider' }));
        wrap.appendChild(sw);
        getValue = () => cb.checked;
        setValue = (v) => { cb.checked = !!v; };
    } else if (type === 'select') {
        const sel = document.createElement('select'); sel.className = 'form-control mode-select';
        (schema.options || []).forEach(opt => {
            const o = document.createElement('option');
            if (typeof opt === 'object') { o.value = opt.value; o.textContent = opt.label; }
            else { o.value = opt; o.textContent = opt; }
            sel.appendChild(o);
        });
        sel.value = initial != null ? String(initial) : '';
        wrap.appendChild(sel);
        getValue = () => sel.value;
        setValue = (v) => { sel.value = v != null ? String(v) : ''; };
    } else if (type === 'textarea') {
        const ta = document.createElement('textarea'); ta.className = 'form-control auto-resize';
        ta.rows = schema.rows || 3; ta.value = initial != null ? String(initial) : '';
        wrap.appendChild(ta);
        getValue = () => ta.value;
        setValue = (v) => { ta.value = v != null ? String(v) : ''; };
    } else if (type === 'time') {
        const inp = document.createElement('input'); inp.type = 'time';
        inp.className = 'form-control'; inp.style.maxWidth = '160px';
        inp.value = initial || '00:00';
        wrap.appendChild(inp);
        getValue = () => inp.value;
        setValue = (v) => { inp.value = v || '00:00'; };
    } else if (type === 'number') {
        const inp = document.createElement('input'); inp.type = 'number'; inp.className = 'form-control';
        if (schema.min != null) inp.min = schema.min;
        if (schema.max != null) inp.max = schema.max;
        if (schema.step != null) inp.step = schema.step;
        inp.value = initial != null ? initial : '';
        wrap.appendChild(inp);
        getValue = () => { const v = parseFloat(inp.value); return isNaN(v) ? null : v; };
        setValue = (v) => { inp.value = v != null ? v : ''; };
    } else {
        const inp = document.createElement('input');
        inp.type = type === 'password' ? 'password' : 'text';
        inp.className = 'form-control';
        if (schema.placeholder) inp.placeholder = schema.placeholder;
        inp.value = initial != null ? String(initial) : '';
        wrap.appendChild(inp);
        getValue = () => inp.value;
        setValue = (v) => { inp.value = v != null ? String(v) : ''; };
    }
    return { el: wrap, getValue, setValue };
}

// ---------- string_list ----------
function initStringListWidget(mod, key, el) {
    let items = parseJsonAttr(el.dataset.value, []);
    if (!Array.isArray(items)) items = String(items || '').split('\n').filter(s => s.trim() !== '');
    function render() {
        el.innerHTML = '';
        const wrap = document.createElement('div');
        if (items.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'widget-empty'; empty.style.cssText = 'color:#718096;font-size:12px;';
            empty.textContent = '暂无项目';
            wrap.appendChild(empty);
        }
        items.forEach((val, idx) => {
            const row = document.createElement('div');
            row.style.cssText = 'display:flex;gap:6px;margin-bottom:6px;';
            const inp = document.createElement('input');
            inp.type = 'text'; inp.className = 'form-control'; inp.value = val || '';
            inp.addEventListener('input', () => { items[idx] = inp.value; });
            const rm = document.createElement('button');
            rm.type = 'button'; rm.className = 'btn-remove'; rm.textContent = '✕';
            rm.title = '删除';
            rm.addEventListener('click', () => { items.splice(idx, 1); render(); });
            row.appendChild(inp); row.appendChild(rm);
            wrap.appendChild(row);
        });
        const add = document.createElement('button');
        add.type = 'button'; add.className = 'btn btn-sm btn-outline';
        add.textContent = '+ 添加'; add.style.cssText = 'margin-top:6px;';
        add.addEventListener('click', () => { items.push(''); render(); });
        wrap.appendChild(add);
        el.appendChild(wrap);
    }
    render();
    window.__configWidgets[widgetKey(mod, key)] = {
        type: 'string_list',
        get: () => { const out = {}; out[key] = items.map(v => String(v).trim()).filter(Boolean); return out; },
        set: (v) => { items = Array.isArray(v) ? v.slice() : (typeof v === 'string' ? v.split('\n') : []); render(); },
    };
}

// ---------- list（后端数据列表） ----------
function initListWidget(mod, key, el) {
    const schema = {
        endpoint: el.dataset.endpoint || '',
        id_field: el.dataset.idField || 'id',
        name_field: el.dataset.nameField || 'name',
        meta_fields: parseJsonAttr(el.dataset.metaFields, []),
        sortable: el.dataset.sortable === '1',
        checkboxes: el.dataset.checkboxes === '1',
        mode_select: el.dataset.modeSelect === '1',
    };
    let items = [];
    let mode = parseJsonAttr(el.dataset.mode, 'all') || 'all';
    let loading = true;
    let error = '';
    let dragSrc = null;  // 拖拽源行（所有行共享，否则 drop 看不到源）

    function render() {
        el.innerHTML = '';
        if (schema.mode_select) {
            const ms = document.createElement('select');
            ms.className = 'form-control mode-select';
            ms.style.cssText = 'margin-bottom:8px;';
            [['all', '全部开启 — 所有列表项默认参与'], ['partial', '部分 — 仅勾选的列表项参与'], ['none', '全部关闭']].forEach(function (kv) {
                const o = document.createElement('option'); o.value = kv[0]; o.textContent = kv[1];
                if (mode === kv[0]) o.selected = true;
                ms.appendChild(o);
            });
            ms.addEventListener('change', function () {
                mode = ms.value;
                if (mode === 'all') items.forEach(it => it.enabled = true);
                else if (mode === 'none') items.forEach(it => it.enabled = false);
                render();
            });
            el.appendChild(ms);
        }
        const listEl = document.createElement('div');
        if (loading) {
            listEl.innerHTML = '<div class="widget-loading" style="color:#718096;font-size:12px;">正在加载列表数据...</div>';
        } else if (error) {
            listEl.innerHTML = '<div class="widget-empty" style="color:#718096;font-size:12px;">' + escHtml(error) + '</div>';
        } else if (items.length === 0) {
            listEl.innerHTML = '<div class="widget-empty" style="color:#718096;font-size:12px;">暂无数据，请检查 Bot 连接或点击刷新</div>';
        } else {
            items.forEach(function (it, i) {
                const row = document.createElement('div');
                row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:6px 8px;border-bottom:1px solid #e2e8f0;';
                row.setAttribute('draggable', schema.sortable ? 'true' : 'false');
                row.dataset.id = it.id;
                if (schema.sortable) {
                    const h = document.createElement('span');
                    h.textContent = '⠿'; h.style.cssText = 'cursor:grab;color:#a0aec0;';
                    row.appendChild(h);
                }
                const idx = document.createElement('span');
                idx.textContent = (i + 1); idx.style.cssText = 'color:#a0aec0;min-width:18px;text-align:center;font-size:12px;';
                row.appendChild(idx);
                const info = document.createElement('div');
                info.style.cssText = 'flex:1;min-width:0;';
                const name = document.createElement('div');
                name.textContent = it.name || it.id;
                name.title = it.name || it.id;
                name.style.cssText = 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px;';
                info.appendChild(name);
                const metas = [];
                (it.meta || []).forEach(function (mv, mi) {
                    if (mv != null) metas.push((schema.meta_fields[mi] || '') + ': ' + mv);
                });
                if (metas.length) {
                    const meta = document.createElement('div');
                    meta.textContent = metas.join('  |  ');
                    meta.style.cssText = 'font-size:12px;color:#718096;';
                    info.appendChild(meta);
                }
                row.appendChild(info);
                if (schema.checkboxes) {
                    const cb = document.createElement('input');
                    cb.type = 'checkbox'; cb.checked = !!it.enabled;
                    cb.addEventListener('change', function () {
                        it.enabled = cb.checked;
                        if (schema.mode_select && (mode === 'all' || mode === 'none')) { mode = 'partial'; render(); }
                    });
                    row.appendChild(cb);
                }
                if (schema.sortable) {
                    row.addEventListener('dragstart', function (e) {
                        dragSrc = row;  // 共享变量，drop 在目标行上也能读到源行
                        row.style.opacity = '0.5';
                        e.dataTransfer.effectAllowed = 'move';
                        e.dataTransfer.setData('text/plain', String(it.id));  // 必须 setData，否则拖拽不生效
                        // 自定义拖拽提示图（替换默认的方块快照）
                        var ghost = document.createElement('div');
                        ghost.textContent = '⠿ 调整顺序';
                        ghost.style.cssText = 'position:fixed;top:0;left:0;background:#3182ce;color:#fff;' +
                            'padding:4px 10px;border-radius:4px;font-size:12px;pointer-events:none;';
                        document.body.appendChild(ghost);
                        e.dataTransfer.setDragImage(ghost, 8, 8);
                        setTimeout(function () { ghost.remove(); }, 0);
                    });
                    row.addEventListener('dragover', function (e) {
                        e.preventDefault();
                        e.dataTransfer.dropEffect = 'move';
                        row.classList.add('widget-drag-over');
                    });
                    row.addEventListener('dragleave', function () {
                        row.classList.remove('widget-drag-over');
                    });
                    row.addEventListener('drop', function (e) {
                        e.preventDefault();
                        row.classList.remove('widget-drag-over');
                        if (dragSrc && dragSrc !== row) {
                            const srcIdx = items.findIndex(x => x.id === dragSrc.dataset.id);
                            const tgtIdx = items.findIndex(x => x.id === row.dataset.id);
                            if (srcIdx > -1 && tgtIdx > -1) {
                                const moved = items.splice(srcIdx, 1)[0];
                                items.splice(tgtIdx, 0, moved);
                                items.forEach((x, xi) => x.index = xi);
                                if (schema.mode_select && (mode === 'all' || mode === 'none')) mode = 'partial';
                                render();
                            }
                        }
                        dragSrc = null;
                    });
                    row.addEventListener('dragend', function () { dragSrc = null; row.style.opacity = ''; });
                }
                listEl.appendChild(row);
            });
        }
        el.appendChild(listEl);
    }

    async function load() {
        loading = true; error = '';
        const botId = getCurrentBotId();
        if (!schema.endpoint || !botId) {
            loading = false;
            error = botId ? '' : '连接 Bot 后获取数据';
            render();
            return;
        }
        render();
        const data = await fetchJson('/api/module/' + mod + '/list/' + schema.endpoint + '?bot_id=' + botId);
        loading = false;
        if (data && data.ok) {
            items = data.items || [];
            if (schema.mode_select && data.mode) mode = data.mode;
            items.forEach(function (it, i) { if (it.index == null) it.index = i; });
        } else {
            error = '列表数据加载失败';
        }
        render();
    }

    render();
    load();
    window.__configWidgets[widgetKey(mod, key)] = {
        type: 'list',
        get: function () {
            const cfg = {};
            items.forEach(function (it, i) { cfg[it.id] = { enabled: !!it.enabled, index: i }; });
            const out = {};
            out[key] = cfg;
            if (schema.mode_select) out[key + '_mode'] = mode;
            return out;
        },
        set: function (v) {
            if (v && typeof v === 'object') {
                Object.keys(v).forEach(function (id) {
                    const it = items.find(x => x.id === id);
                    if (it && v[id]) { it.enabled = !!v[id].enabled; it.index = v[id].index != null ? v[id].index : it.index; }
                });
            }
        },
        reload: load,
    };
}

// ---------- dynamic（后端动态选项） ----------
function initDynamicWidget(mod, key, el) {
    const endpoint = el.dataset.endpoint || '';
    let saved = parseJsonAttr(el.dataset.value, {});
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) saved = {};
    let selected = parseJsonAttr(el.dataset.selected, '') || '';
    let options = [];
    let fields = [];
    let loading = true;
    let subfields = [];
    const store = {};

    function render() {
        el.innerHTML = '';
        const row = document.createElement('div');
        row.style.cssText = 'display:flex;gap:8px;align-items:center;margin-bottom:8px;';
        const sel = document.createElement('select');
        sel.className = 'form-control mode-select';
        sel.style.cssText = 'flex:1;';
        (options || []).forEach(function (opt) {
            const o = document.createElement('option');
            o.value = opt.value; o.textContent = opt.label;
            if (selected === opt.value) o.selected = true;
            sel.appendChild(o);
        });
        if (options.length === 0) {
            const o = document.createElement('option'); o.value = '';
            o.textContent = loading ? '加载中...' : '暂无可选项';
            sel.appendChild(o);
        }
        sel.addEventListener('change', function () {
            selected = sel.value;
            if (selected) { store[selected] = store[selected] || saved[selected] || {}; }
            loadFields(selected);
        });
        row.appendChild(sel);
        const refresh = document.createElement('button');
        refresh.type = 'button'; refresh.className = 'btn btn-sm btn-outline'; refresh.textContent = '↻';
        refresh.title = '刷新选项'; refresh.addEventListener('click', load);
        row.appendChild(refresh);
        el.appendChild(row);

        const body = document.createElement('div');
        if (loading) {
            body.innerHTML = '<div class="widget-loading" style="color:#718096;font-size:12px;">加载中...</div>';
        } else if (!selected) {
            body.innerHTML = '<div class="widget-empty" style="color:#718096;font-size:12px;">暂无可选项，请点击刷新</div>';
        } else if (fields.length === 0) {
            body.innerHTML = '<div class="widget-empty" style="color:#718096;font-size:12px;">该选项暂无可配置项</div>';
        } else {
            subfields = [];
            fields.forEach(function (fd) {
                const sf = renderSubField(fd, store[fd.key] !== undefined ? store[fd.key] : fd.default);
                subfields.push(sf);
                body.appendChild(sf.el);
            });
        }
        el.appendChild(body);
    }

    async function loadFields(value) {
        if (!value) { fields = []; render(); return; }
        const data = await fetchJson('/api/module/' + mod + '/dynamic/' + endpoint + '/' + encodeURIComponent(value) + '?bot_id=' + (getCurrentBotId() || ''));
        fields = (data && data.ok) ? (data.fields || []) : [];
        render();
    }

    async function load() {
        loading = true;
        render();
        const botId = getCurrentBotId();
        if (!endpoint || !botId) {
            loading = false; options = [];
            render();
            return;
        }
        const data = await fetchJson('/api/module/' + mod + '/dynamic/' + endpoint + '?bot_id=' + botId);
        loading = false;
        options = (data && data.ok) ? (data.options || []) : [];
        const known = options.map(o => o.value);
        if (!selected || known.indexOf(selected) === -1) selected = known.length ? known[0] : '';
        render();
        if (selected) {
            store[selected] = store[selected] || saved[selected] || {};
            await loadFields(selected);
        }
    }

    window.__configWidgets[widgetKey(mod, key)] = {
        type: 'dynamic',
        get: function () {
            if (selected && subfields.length) {
                store[selected] = store[selected] || {};
                fields.forEach(function (fd, i) { if (subfields[i]) store[selected][fd.key] = subfields[i].getValue(); });
                saved[selected] = store[selected];  // 写回 saved，新选项才不丢失
            }
            const out = {};
            out[key] = saved;
            out[key + '_selected'] = selected;
            return out;
        },
        set: function (v) {
            if (v && typeof v === 'object') saved = v;
        },
        reload: load,
    };

    load();
}

// ---------- repeater（可增删分组） ----------
function initRepeaterWidget(mod, key, el) {
    const template = parseJsonAttr(el.dataset.template, {});
    let items = parseJsonAttr(el.dataset.value, []);
    if (!Array.isArray(items)) items = [];

    function render() {
        el.innerHTML = '';
        const wrap = document.createElement('div');
        items.forEach(function (itemData, idx) {
            const card = document.createElement('div');
            card.style.cssText = 'border:1px solid #e2e8f0;border-radius:6px;padding:10px;margin-bottom:10px;background:#fdfdfd;';
            const head = document.createElement('div');
            head.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;';
            const title = document.createElement('span');
            title.style.cssText = 'font-weight:bold;font-size:0.85rem;';
            title.textContent = '分组 ' + (idx + 1);
            const rm = document.createElement('button');
            rm.type = 'button'; rm.className = 'btn-remove'; rm.textContent = '✕';
            rm.addEventListener('click', function () { items.splice(idx, 1); render(); });
            head.appendChild(title); head.appendChild(rm);
            card.appendChild(head);
            Object.keys(template).forEach(function (subKey) {
                const fd = JSON.parse(JSON.stringify(template[subKey]));
                fd.key = subKey;
                if (!(subKey in itemData)) itemData[subKey] = fd.default !== undefined ? fd.default : '';
                const sf = renderSubField(fd, itemData[subKey]);
                sf.el.addEventListener('input', function () { itemData[subKey] = sf.getValue(); });
                sf.el.addEventListener('change', function () { itemData[subKey] = sf.getValue(); });
                card.appendChild(sf.el);
            });
            wrap.appendChild(card);
        });
        const add = document.createElement('button');
        add.type = 'button'; add.className = 'btn btn-sm btn-outline';
        add.textContent = '+ 新增分组'; add.style.cssText = 'margin-top:4px;';
        add.addEventListener('click', function () {
            const newItem = {};
            Object.keys(template).forEach(function (subKey) {
                newItem[subKey] = template[subKey].default !== undefined ? template[subKey].default : '';
            });
            items.push(newItem); render();
        });
        wrap.appendChild(add);
        el.appendChild(wrap);
    }
    render();
    window.__configWidgets[widgetKey(mod, key)] = {
        type: 'repeater',
        get: function () { const out = {}; out[key] = items; return out; },
        set: function (v) { items = Array.isArray(v) ? v : []; render(); },
    };
}

// ---------- 初始化 / 条件显示 / 刷新 ----------
function initConfigWidgets(moduleName) {
    const container = document.getElementById('config-container-' + moduleName);
    if (!container) return;
    container.querySelectorAll('.config-item').forEach(function (item) {
        try {
            const el = item.querySelector('[data-widget-type]');
            if (!el) return;
            const wtype = el.getAttribute('data-widget-type');
            const key = item.getAttribute('data-config-key');
            if (!key) return;
            if (window.__configWidgets[widgetKey(moduleName, key)]) return;
            if (wtype === 'string_list') initStringListWidget(moduleName, key, el);
            else if (wtype === 'list') initListWidget(moduleName, key, el);
            else if (wtype === 'dynamic') initDynamicWidget(moduleName, key, el);
            else if (wtype === 'repeater') initRepeaterWidget(moduleName, key, el);
        } catch (e) {
            console.error('[Widget] 初始化失败 ' + moduleName + ':', e);
        }
    });
    // showIf 响应式监听（只绑定一次）；顺带标记「未保存」触发自动保存
    if (!container.dataset.showifBound) {
        container.dataset.showifBound = '1';
        const onFormChange = function () {
            applyShowIf(moduleName);
            if (typeof markModuleDirty === 'function') markModuleDirty(moduleName);
        };
        container.addEventListener('change', onFormChange);
        container.addEventListener('input', onFormChange);
    }
    applyShowIf(moduleName);
}

function initAllConfigWidgets() {
    document.querySelectorAll('[id^="config-container-"]').forEach(function (container) {
        initConfigWidgets(container.id.replace('config-container-', ''));
    });
    initPluginPages();
}

/** 插件自定义配置页 iframe：按内容自适应高度（同源可直接测量）。 */
function resizePluginPage(el) {
    if (!el || !el.contentDocument) return;
    try {
        const doc = el.contentDocument;
        const h = Math.max(
            doc.documentElement ? doc.documentElement.scrollHeight : 0,
            doc.body ? doc.body.scrollHeight : 0
        );
        if (h > 40) el.style.height = h + 'px';
    } catch (e) { /* 跨域 iframe 无法测量，保持当前高度 */ }
}

function resizeAllPluginPages() {
    document.querySelectorAll('[id^="plugin-page-"]').forEach(resizePluginPage);
}

/** 插件自定义配置页：iframe 带上当前选中账号的 bot_id，并随内容自适应高度 */
function initPluginPages() {
    const botId = getCurrentBotId();
    document.querySelectorAll('[id^="plugin-page-"]').forEach(function (el) {
        const base = el.getAttribute('data-page');
        if (!base) return;
        if (!el.__pluginPageInit) {
            el.__pluginPageInit = true;
            el.addEventListener('load', function () {
                resizePluginPage(el);
                // 页面内有异步加载（配置/状态/任务），内容高度变化后自动跟随
                if (el.__pluginPageRO) { el.__pluginPageRO.disconnect(); el.__pluginPageRO = null; }
                try {
                    const doc = el.contentDocument;
                    if (doc && doc.body) {
                        el.__pluginPageRO = new ResizeObserver(function () { resizePluginPage(el); });
                        el.__pluginPageRO.observe(doc.body);
                    }
                } catch (e) { /* 不支持 ResizeObserver 或跨域，忽略 */ }
            });
        }
        el.src = botId ? base + '?bot_id=' + botId : base;
    });
}

function reloadDataWidgets() {
    Object.keys(window.__configWidgets).forEach(function (k) {
        const w = window.__configWidgets[k];
        if (w && w.reload) w.reload();
    });
}

function applyShowIf(moduleName) {
    const container = document.getElementById('config-container-' + moduleName);
    if (!container) return;
    container.querySelectorAll('.config-item[data-showif-key]').forEach(function (item) {
        const condKey = item.getAttribute('data-showif-key');
        const rawCond = item.getAttribute('data-showif-value');
        let condVal = rawCond;
        try { condVal = JSON.parse(rawCond); } catch (e) { /* keep raw */ }
        const control = container.querySelector('.config-item[data-config-key="' + condKey + '"]');
        let current = null;
        if (control) {
            const el = control.querySelector('input,select');
            if (el) current = el.type === 'checkbox' ? el.checked : el.value;
        }
        const visible = current === null || String(current) === String(condVal);
        item.style.display = visible ? '' : 'none';
    });
}
