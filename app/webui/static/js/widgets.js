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

// ---------- 资源引用（type: resource / resources） ----------
/** 已挂载的资源选择器的刷新回调：WS 收到 resources_updated 时按 kinds 局部刷新。 */
const resourceReloaders = [];

/** 资源增删改后刷新资源选择器候选；kinds 为空表示全部。 */
function refreshResourceWidgets(kinds) {
    const wanted = Array.isArray(kinds) ? kinds.map(String) : [];
    resourceReloaders.forEach(function (entry) {
        try {
            if (!wanted.length || wanted.indexOf(entry.kind) >= 0) entry.reload();
        } catch (e) {
            console.error('[Resource] 刷新候选失败:', e);
        }
    });
}

/**
 * 资源引用字段：候选来自框架资源注册表（GET /api/resources/<kind>）。
 *
 * 与新版 Dashboard 的 ResourceSelect 同一口径：种类未注册 / 加载失败都给可读提示，
 * 悬挂引用（存了但资源已删）补一条「（已删除或未找到）」，绝不显示成空白下拉。
 * bot_id 由 getCurrentBotId() 现取（当前内置种类都不需要，将来"某账号下的资源"才用）。
 */
function initResourceWidget(moduleName, key, el) {
    const kind = (el.getAttribute('data-resource') || '').trim();
    const multiple = el.getAttribute('data-multiple') === '1';
    let filters = {};
    try { filters = JSON.parse(el.getAttribute('data-filters') || '{}') || {}; } catch (e) { filters = {}; }
    let raw = null;
    try { raw = JSON.parse(el.getAttribute('data-value') || 'null'); } catch (e) { raw = null; }
    // 保存时的兜底值：拉不到候选（接口挂了/种类未注册）时按原值回填，别把已存配置抹掉
    let original = multiple
        ? (Array.isArray(raw) ? raw.map(String) : [])
        : (raw === null || raw === undefined ? '' : String(raw));

    const box = document.createElement('div');
    const status = document.createElement('div');
    status.style.cssText = 'font-size:12px;margin-top:4px;color:rgba(255,255,255,0.55);';
    box.appendChild(status);
    el.appendChild(box);

    let select = null;
    let options = [];

    function currentValues() {
        if (select) {
            return multiple
                ? Array.prototype.map.call(select.selectedOptions, function (o) { return o.value; })
                : select.value;
        }
        return original;
    }

    function buildSelect() {
        if (select) select.remove();
        select = document.createElement('select');
        select.className = 'form-control';
        select.id = 'config-' + moduleName + '-' + key;   // 让 showIf / 脏标记等既有逻辑仍能定位
        if (multiple) {
            select.multiple = true;
            select.size = Math.min(Math.max(options.length, 2), 8);
        } else {
            const empty = document.createElement('option');
            empty.value = '';
            empty.textContent = '（不使用）';
            select.appendChild(empty);
        }
        const selected = multiple
            ? (Array.isArray(original) ? original : [])
            : [String(original === '' ? '' : original)];
        options.forEach(function (o) {
            const opt = document.createElement('option');
            opt.value = o.value;
            opt.textContent = o.label;
            opt.selected = selected.indexOf(o.value) >= 0;
            select.appendChild(opt);
        });
        select.addEventListener('change', function () {
            if (typeof markModuleDirty === 'function') markModuleDirty(moduleName);
        });
        box.insertBefore(select, status);
    }

    async function load() {
        if (!kind) {
            status.textContent = '该字段未声明 resource（资源种类）';
            status.style.color = '#e57373';
            return;
        }
        status.style.color = 'rgba(255,255,255,0.55)';
        status.textContent = '加载中…';
        const params = new URLSearchParams();
        Object.keys(filters).forEach(function (k) { params.set(k, filters[k]); });
        const botId = (typeof getCurrentBotId === 'function') ? getCurrentBotId() : '';
        if (botId) params.set('bot_id', botId);
        const qs = params.toString();
        try {
            const resp = await apiFetch('/api/resources/' + encodeURIComponent(kind) + (qs ? '?' + qs : ''));
            const data = await resp.json().catch(function () { return {}; });
            if (!resp.ok) {
                // 404 = 种类未注册：明确说出来，而不是留一个空下拉让人以为"没有数据"
                status.textContent = resp.status === 404
                    ? ('资源种类未注册：' + kind)
                    : ('资源加载失败：' + (data && data.message ? data.message : resp.status));
                status.style.color = '#e57373';
                return;
            }
            options = (data.options || []).map(function (o) {
                return { value: String(o.value), label: String(o.label) };
            });
            // 悬挂引用：存了但候选里没有（资源被删）→ 补一条可读选项
            const stored = multiple
                ? (Array.isArray(original) ? original : [])
                : (original === '' ? [] : [String(original)]);
            stored.forEach(function (v) {
                if (!options.some(function (o) { return o.value === v; })) {
                    options.push({ value: v, label: v + '（已删除或未找到）' });
                }
            });
            status.textContent = options.length ? '' : '（该资源暂无候选）';
            buildSelect();
        } catch (e) {
            status.textContent = '资源加载失败：' + (e && e.message ? e.message : e);
            status.style.color = '#e57373';
        }
    }

    /** 刷新候选：先把用户当前选择收进 original，避免刷新把未保存的改动冲掉。 */
    function reload() {
        original = currentValues();
        options = [];
        void load();
    }
    resourceReloaders.push({ kind: kind, reload: reload });

    window.__configWidgets[widgetKey(moduleName, key)] = {
        type: 'resource',
        get: function () {
            const out = {};
            out[key] = currentValues();
            return out;
        },
        set: function (v) {
            original = multiple
                ? (Array.isArray(v) ? v.map(String) : [])
                : (v === null || v === undefined ? '' : String(v));
            if (select) buildSelect();
            else void load();
        },
    };
    void load();
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
            else if (wtype === 'resource') initResourceWidget(moduleName, key, el);
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

// showIf 求值：与后端 config_schema.py / 新版前端 ConfigForm 共用同一套条件文法。
// 支持 {key,value} / {key,in} / {key,notIn} / {key,gt} / {key,notEmpty}
// 以及 {allOf:[...]} / {anyOf:[...]} 组合。
// 历史版本只认 {key,value}，模板也只下发这两段属性；统一改成整段条件 JSON 后，
// 后端新增复杂条件不会再让这个页面渲染失败（曾因 showIf.value 为 Undefined 直接 500）。
function showIfTruthy(value) {
    if (Array.isArray(value)) return value.length > 0;
    if (typeof value === 'string') return value.trim() !== '';
    return value !== null && value !== undefined && value !== false && value !== 0;
}

function showIfControlValue(container, key) {
    const control = container.querySelector('.config-item[data-config-key="' + key + '"]');
    if (!control) return undefined;            // 找不到控制项 → 无法判定
    const el = control.querySelector('input,select,textarea');
    if (!el) return undefined;
    return el.type === 'checkbox' ? el.checked : el.value;
}

function showIfMatches(container, cond) {
    if (!cond || typeof cond !== 'object') return true;
    if (Array.isArray(cond.allOf)) return cond.allOf.every((c) => showIfMatches(container, c));
    if (Array.isArray(cond.anyOf)) return cond.anyOf.some((c) => showIfMatches(container, c));
    if (!cond.key) return true;
    const current = showIfControlValue(container, cond.key);
    if (current === undefined) return true;    // 控制项不在本页 → 保持可见，避免整块消失
    if ('value' in cond) return String(current) === String(cond.value);
    if (Array.isArray(cond.in)) return cond.in.some((v) => String(v) === String(current));
    if (Array.isArray(cond.notIn)) return !cond.notIn.some((v) => String(v) === String(current));
    if (typeof cond.gt === 'number') return Number(current) > cond.gt;
    if (cond.notEmpty) return showIfTruthy(current);
    return true;
}

function applyShowIf(moduleName) {
    const container = document.getElementById('config-container-' + moduleName);
    if (!container) return;
    container.querySelectorAll('.config-item[data-showif]').forEach(function (item) {
        let cond = null;
        try { cond = JSON.parse(item.getAttribute('data-showif') || 'null'); } catch (e) { cond = null; }
        item.style.display = showIfMatches(container, cond) ? '' : 'none';
    });
}
