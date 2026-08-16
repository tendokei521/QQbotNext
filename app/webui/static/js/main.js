// ==================== 全局配置 ====================
let webuiConfig = window.WEBUI_CONFIG || {
    logs: { show_raw_logs: false, visible_levels: ['info', 'warning', 'error'], max_lines: 50 }
};
let botsData = window.BOTS_DATA || [];
let visibleLevels = webuiConfig.logs?.visible_levels || ['info', 'warning', 'error'];
let maxLogLines = webuiConfig.logs?.max_lines || 50;
let showRawLogs = webuiConfig.logs?.show_raw_logs === true;
let currentBotId = null;

// ==================== 工具函数 ====================
// ==================== 访问令牌（WEBUI_TOKEN） ====================
// GET / 不鉴权，页面渲染时注入 token；所有 API 请求与 WS 连接统一携带
const WEBUI_TOKEN = window.WEBUI_TOKEN || '';

function apiFetch(url, options = {}) {
    options = options || {};
    if (WEBUI_TOKEN) {
        options.headers = Object.assign({}, options.headers || {}, {
            'Authorization': 'Bearer ' + WEBUI_TOKEN
        });
    }
    return window.fetch(url, options);
}

function apiWsUrl(path) {
    if (!WEBUI_TOKEN) return path;
    return path + (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(WEBUI_TOKEN);
}

function autoResize(el) {
    el.style.height = 'auto';
    el.style.height = (el.scrollHeight + 2) + 'px';
}

function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast-item ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('show'));
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ==================== 弹窗控制 ====================
function openModal(modalId) {
    document.getElementById(modalId).classList.add('show');
}

function closeModal(modalId) {
    document.getElementById(modalId).classList.remove('show');
}

document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
            overlay.classList.remove('show');
        }
    });
});

// ==================== 账号管理 ====================
let currentBotIndex = null;

function getCurrentBotIndex() {
    if (currentBotIndex !== null) return currentBotIndex;
    const select = document.getElementById('current-bot-select');
    if (select && select.value) {
        currentBotIndex = parseInt(select.value);
    }
    return currentBotIndex;
}

function getCurrentBotId() {
    const botIndex = getCurrentBotIndex();
    if (botIndex === null) return null;
    
    const bot = botsData.find(b => b.index === botIndex);
    return bot ? bot.bot_id : null;
}

function switchBot() {
    const select = document.getElementById('current-bot-select');
    currentBotIndex = parseInt(select.value);
    localStorage.setItem('qqbot_current_bot_index', String(currentBotIndex));  // 记住选择，刷新后恢复
    updateBotStatusDisplay();
    loadModulesForBot(currentBotIndex);
    refreshAllModulesData();
    reloadDataWidgets();  // 重新拉取 list/dynamic 数据（新账号）
    if (window.initPluginPages) initPluginPages();  // 自定义配置页切换账号
    updateAllSingleServiceWarnings();
}

/** 从 localStorage 恢复上次选择的账号索引；无记录或索引失效时回退 0。
 *  必须在 initAllConfigWidgets 之前调用，这样 list/dynamic 会按所选账号拉数据。 */
function restoreBotSelection() {
    let idx = parseInt(localStorage.getItem('qqbot_current_bot_index'), 10);
    if (isNaN(idx) || !botsData.some(b => b.index === idx)) {
        idx = (botsData && botsData.length) ? botsData[0].index : null;
    }
    if (idx === null) return;
    currentBotIndex = idx;
    const select = document.getElementById('current-bot-select');
    if (select) select.value = String(idx);
    currentBotId = getCurrentBotId();
    updateBotStatusDisplay();
}

function updateBotStatusDisplay() {
    const botIndex = getCurrentBotIndex();
    if (botIndex === null) return;

    const bot = botsData.find(b => b.index === botIndex);
    if (!bot) return;

    const statusIndicator = document.getElementById('bot-status-indicator');
    const statusText = document.getElementById('bot-status-text');
    const btnConnect = document.getElementById('btn-connect');
    const btnDisconnect = document.getElementById('btn-disconnect');

    statusIndicator.className = `bot-status-indicator ${bot.status}`;

    const displayBotId = bot.bot_id || `配置#${bot.index}`;
    statusText.textContent = `${getStatusText(bot.status)} | ${displayBotId}`;

    if (bot.status === 'connected') {
        btnConnect.style.display = 'none';
        btnDisconnect.style.display = 'flex';
    } else {
        btnConnect.style.display = 'flex';
        btnDisconnect.style.display = 'none';
    }
}

function getStatusText(status) {
    const statusMap = {
        'connected': '已连接',
        'disconnected': '离线',
        'connecting': '连接中...',
        'reconnecting': '重连中...',
        'error': '错误'
    };
    return statusMap[status] || '未知';
}

async function refreshBotsStatus() {
    try {
        const response = await apiFetch('/api/bots');
        if (response.ok) {
            const data = await response.json();
            botsData = data.bots || [];
            updateBotStatusDisplay();
            //showToast('状态已刷新', 'info');
        }
    } catch (error) {
        showToast('刷新失败', 'error');
    }
}

async function connectBot() {
    const botIndex = getCurrentBotIndex();
    if (botIndex === null) {
        showToast('请先选择账号', 'warning');
        return;
    }

    try {
        const response = await apiFetch(`/api/bots/${botIndex}/connect`, { method: 'POST' });
        const result = await response.json();

        if (response.ok && result.status === 'success') {
            showToast(result.message, 'success');
            await refreshBotsStatus();
            updateBotStatusDisplay();
        } else {
            showToast(result.message || '连接失败', 'error');
        }
    } catch (error) {
        showToast('请求失败', 'error');
    }
}

async function disconnectBot() {
    const botIndex = getCurrentBotIndex();
    if (botIndex === null) {
        showToast('请先选择账号', 'warning');
        return;
    }

    try {
        const response = await apiFetch(`/api/bots/${botIndex}/disconnect`, { method: 'POST' });
        const result = await response.json();

        if (response.ok && result.status === 'success') {
            showToast(result.message, 'success');
            await refreshBotsStatus();
            updateBotStatusDisplay();


        } else {
            showToast(result.message || '操作失败', 'error');
        }
    } catch (error) {
        showToast('请求失败', 'error');
    }
}

async function reconnectBot() {
    const botIndex = getCurrentBotIndex();
    if (botIndex === null) {
        showToast('请先选择账号', 'warning');
        return;
    }

    try {
        const response = await apiFetch(`/api/bots/${botIndex}/reconnect`, { method: 'POST' });
        const result = await response.json();

        if (response.ok && result.status === 'success') {
            showToast(result.message, 'info');
            await refreshBotsStatus();
            updateBotStatusDisplay();
        } else {
            showToast(result.message || '重连失败', 'error');
        }
    } catch (error) {
        showToast('请求失败', 'error');
    }
}

// ==================== 账号管理弹窗 (多卡片式) ====================
function openWsConfigModal() {
    loadBotCards();
    openModal('ws-config-modal');
}

async function loadBotCards() {
    try {
        const [configResp, statusResp] = await Promise.all([
            apiFetch('/api/bots/config'),
            apiFetch('/api/bots')
        ]);
        const configData = await configResp.json();
        const statusData = await statusResp.json();
        const botsConfig = configData.bots || [];
        botsData = statusData.bots || [];

        const container = document.getElementById('bot-cards-container');
        container.innerHTML = '';

        botsConfig.forEach((cfg, index) => {
            const botInfo = botsData.find(b => b.index === index) || {};
            const status = botInfo.status || 'disconnected';
            const botId = botInfo.bot_id || null;
            const loginInfo = botInfo.login_info || {};

            const card = document.createElement('div');
            card.className = 'bot-config-card';
            card.dataset.index = index;

            const loginInfoHtml = (loginInfo && loginInfo.user_id)
                ? `<div class="bot-card-login-info"><div class="bot-card-info-item"><span class="info-label">登录账号</span><span class="info-value">${loginInfo.nickname || ''} (${loginInfo.user_id})</span></div></div>`
                : '';

            card.innerHTML = `
                <div class="bot-card-header">
                    <div class="bot-card-title">
                        <i class="fas fa-robot"></i>
                        <span>账号 #${index}</span>
                    </div>
                    <div class="bot-card-actions">
                        <span class="bot-card-status ${status}">
                            <i class="fas fa-circle"></i> ${status}
                        </span>
                        <button class="btn-icon-small btn-danger" onclick="deleteBotConfig(${index})" title="删除此账号配置">
                            <i class="fas fa-trash"></i>
                        </button>
                    </div>
                </div>
                <div class="bot-card-body">
                    <div class="bot-card-field">
                        <label>WebSocket 地址</label>
                        <input type="text" class="form-control bot-ws-url" value="${cfg.ws_url || ''}" placeholder="ws://...">
                    </div>
                    <div class="bot-card-field">
                        <label>Access Token</label>
                        <input type="text" class="form-control bot-access-token" value="${cfg.access_token || ''}" placeholder="无 Token" autocomplete="off">
                        <div class="bot-card-hint">保存后立即生效；留空并保存 = 清除 Token</div>
                    </div>
                    <div class="bot-card-field">
                        <label>Owner ID</label>
                        <input type="text" class="form-control bot-owner-id" value="${cfg.owner_id || ''}" placeholder="管理员QQ号">
                    </div>
                    <div class="bot-card-field bot-card-field-switch">
                        <div class="bot-switch-row">
                            <span>启动时自动连接</span>
                            <label class="switch switch-auto-connect">
                                <input type="checkbox" class="bot-auto-connect" ${cfg.auto_connect ? 'checked' : ''}>
                                <span class="slider"></span>
                            </label>
                        </div>
                    </div>
                    <div class="bot-card-info">
                        <div class="bot-card-info-item">
                            <span class="info-label">Bot ID</span>
                            <span class="info-value">${botId || '未连接'}</span>
                        </div>
                        <div class="bot-card-info-item">
                            <span class="info-label">索引</span>
                            <span class="info-value">#${index}</span>
                        </div>
                    </div>
                    ${loginInfoHtml}
                </div>
                <div class="bot-card-footer">
                    <button class="btn btn-sm btn-success" onclick="connectBotByIndex(${index})">
                        <i class="fas fa-plug"></i> 连接
                    </button>
                    <button class="btn btn-sm btn-danger" onclick="disconnectBotByIndex(${index})">
                        <i class="fas fa-unlink"></i> 断开
                    </button>
                    <button class="btn btn-sm btn-warning" onclick="reconnectBotByIndex(${index})">
                        <i class="fas fa-redo"></i> 重连
                    </button>
                </div>
            `;
            container.appendChild(card);
        });

        if (botsConfig.length === 0) {
            container.innerHTML = '<div class="bot-cards-empty"><i class="fas fa-inbox"></i><p>暂无账号配置，点击上方"新增账号"添加</p></div>';
        }
    } catch (error) {
        showToast('加载账号配置失败', 'error');
    }
}

async function saveBotConfigs() {
    const cards = document.querySelectorAll('#bot-cards-container .bot-config-card');
    const bots = [];
    cards.forEach(card => {
        const wsUrl = card.querySelector('.bot-ws-url').value.trim();
        const tokenInput = card.querySelector('.bot-access-token');
        const ownerId = card.querySelector('.bot-owner-id').value.trim();
        const autoConnect = card.querySelector('.bot-auto-connect').checked;
        bots.push({
            ws_url: wsUrl,
            // 配置接口回显真实 token：输入框当前值即最终值（空串 = 清除）
            access_token: tokenInput ? tokenInput.value.trim() : '',
            owner_id: ownerId ? parseInt(ownerId) || ownerId : null,
            auto_connect: autoConnect
        });
    });

    try {
        const response = await apiFetch('/api/bots/config/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bots })
        });
        const result = await response.json();
        if (response.ok && result.status === 'success') {
            showToast('配置已保存，建议刷新页面', 'success');
        } else {
            showToast(result.message || '保存失败', 'error');
        }
    } catch (error) {
        showToast('保存失败', 'error');
    }
}

async function addBotConfig() {
    try {
        const response = await apiFetch('/api/bots/config/add', { method: 'POST' });
        const result = await response.json();
        if (response.ok && result.status === 'success') {
            showToast(result.message, 'success');
            await loadBotCards();
        } else {
            showToast(result.message || '添加失败', 'error');
        }
    } catch (error) {
        showToast('添加失败', 'error');
    }
}

async function deleteBotConfig(index) {
    if (!confirm(`确定删除账号 #${index} 的配置？`)) return;
    try {
        const response = await apiFetch(`/api/bots/config/delete/${index}`, { method: 'POST' });
        const result = await response.json();
        if (response.ok && result.status === 'success') {
            showToast(result.message, 'success');
            await loadBotCards();
        } else {
            showToast(result.message || '删除失败', 'error');
        }
    } catch (error) {
        showToast('删除失败', 'error');
    }
}

async function connectBotByIndex(index) {
    try {
        const response = await apiFetch(`/api/bots/${index}/connect`, { method: 'POST' });
        const result = await response.json();
        if (response.ok && result.status === 'success') {
            showToast(result.message, 'success');
            await loadBotCards();
            await refreshBotsStatus();
            updateBotStatusDisplay();
        } else {
            showToast(result.message || '连接失败', 'error');
        }
    } catch (error) {
        showToast('请求失败', 'error');
    }
}

async function disconnectBotByIndex(index) {
    try {
        const response = await apiFetch(`/api/bots/${index}/disconnect`, { method: 'POST' });
        const result = await response.json();
        if (response.ok && result.status === 'success') {
            showToast(result.message, 'success');
            await loadBotCards();
            await refreshBotsStatus();
            updateBotStatusDisplay();
        } else {
            showToast(result.message || '操作失败', 'error');
        }
    } catch (error) {
        showToast('请求失败', 'error');
    }
}

async function reconnectBotByIndex(index) {
    try {
        const response = await apiFetch(`/api/bots/${index}/reconnect`, { method: 'POST' });
        const result = await response.json();
        if (response.ok && result.status === 'success') {
            showToast(result.message, 'info');
            await loadBotCards();
            await refreshBotsStatus();
            updateBotStatusDisplay();
        } else {
            showToast(result.message || '重连失败', 'error');
        }
    } catch (error) {
        showToast('请求失败', 'error');
    }
}

// ==================== 日志配置弹窗 ====================
function openLogsConfigModal() {
    const levels = visibleLevels || ['info', 'warning', 'error'];
    document.getElementById('log-level-debug').checked = levels.includes('debug');
    document.getElementById('log-level-info').checked = levels.includes('info');
    document.getElementById('log-level-warning').checked = levels.includes('warning');
    document.getElementById('log-level-error').checked = levels.includes('error');
    document.getElementById('log-max-lines').value = maxLogLines;
    document.getElementById('log-show-raw').checked = showRawLogs;
    openModal('logs-config-modal');
}

async function saveLogsConfig() {
    const levels = [];
    if (document.getElementById('log-level-debug').checked) levels.push('debug');
    if (document.getElementById('log-level-info').checked) levels.push('info');
    if (document.getElementById('log-level-warning').checked) levels.push('warning');
    if (document.getElementById('log-level-error').checked) levels.push('error');

    const maxLines = parseInt(document.getElementById('log-max-lines').value);
    const raw = document.getElementById('log-show-raw').checked;

    if (levels.length === 0) {
        showToast('至少选择一个日志级别', 'warning');
        return;
    }

    try {
        const response = await apiFetch('/api/webui/config/logs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                show_raw_logs: raw,
                visible_levels: levels,
                max_lines: maxLines
            })
        });
        const result = await response.json();

        if (response.ok && result.status === 'success') {
            visibleLevels = levels;
            maxLogLines = maxLines;
            showRawLogs = raw;
            webuiConfig.logs = { ...webuiConfig.logs, show_raw_logs: raw, visible_levels: levels, max_lines: maxLines };
            showToast(result.message, 'success');
            closeModal('logs-config-modal');
            refreshLogs();
            reconnectLogsWebSocket();
        } else {
            showToast(result.message || '保存失败', 'error');
        }
    } catch (error) {
        showToast('请求失败', 'error');
    }
}

// ==================== 控制台控制 ====================
function toggleConsole() {
    const console = document.getElementById('console-wrapper');
    console.classList.toggle('collapsed');
}

function clearLogs() {
    logCache = [];
    pendingLogCount = 0;
    document.getElementById('logs-container').innerHTML = '';
    updatePauseBtn();
    updateLogFilterCount(0);
    showToast('日志已清空', 'info');
}

// ==================== 模块管理 ====================
document.querySelectorAll('.module-card-btn').forEach(item => {
    item.addEventListener('click', function(e) {
        if (e.target.closest('.switch')) return;
        document.querySelectorAll('.module-item').forEach(i => i.classList.remove('active'));
        this.classList.add('active');
        document.querySelectorAll('.config-card').forEach(card => card.style.display = 'none');
        const modName = this.getAttribute('data-module');
        const targetCard = document.getElementById(`config-${modName}`);
        if (targetCard) {
            targetCard.style.display = 'block';
            // 隐藏的 iframe 测量为 0，显示后重新按内容自适应高度
            const pluginFrame = targetCard.querySelector('[id^="plugin-page-"]');
            if (pluginFrame && typeof resizePluginPage === 'function') resizePluginPage(pluginFrame);
            if (modName === 'agent') loadAgentPanels();
            setTimeout(() => {
                targetCard.querySelectorAll('textarea.auto-resize').forEach(el => autoResize(el));
                if (pluginFrame && typeof resizePluginPage === 'function') resizePluginPage(pluginFrame);
            }, 100);
        }
    });
});

// ==================== 模块侧边栏增强 ====================
let moduleCollapsed = {};
let moduleGridView = false;
let allModuleItems = [];

function loadModuleCollapsed() {
    try {
        moduleCollapsed = JSON.parse(localStorage.getItem('qqbot_module_collapsed') || '{}');
    } catch (e) {
        moduleCollapsed = {};
    }
}

function saveModuleCollapsed() {
    try {
        localStorage.setItem('qqbot_module_collapsed', JSON.stringify(moduleCollapsed));
    } catch (e) {}
}

function renderModuleList() {
    const container = document.getElementById('module-list');
    if (!container) return;
    const query = (document.getElementById('module-search')?.value || '').trim().toLowerCase();
    const items = allModuleItems;

    const visibleItems = items.filter(item => {
        const name = (item.getAttribute('data-name') || '').toLowerCase();
        const sign = (item.getAttribute('data-sign') || '').toLowerCase();
        const tags = (item.getAttribute('data-tags') || '').toLowerCase();
        if (query && !name.includes(query) && !sign.includes(query) && !tags.includes(query)) return false;
        return true;
    });

    const fragment = document.createDocumentFragment();

    if (!query) {
        const byCat = {};
        visibleItems.forEach(item => {
            const cat = item.getAttribute('data-category') || '未分类';
            if (!byCat[cat]) byCat[cat] = [];
            byCat[cat].push(item);
        });

        const groups = Object.keys(byCat).sort().map(cat => [cat, byCat[cat]]);

        groups.forEach(([title, list]) => {
            const group = document.createElement('div');
            group.className = 'module-group' + (moduleCollapsed[title] ? ' collapsed' : '');
            const header = document.createElement('div');
            header.className = 'module-group-header';
            header.onclick = function() { toggleModuleGroup(this); };
            header.innerHTML = `<span>${title}</span><span class="module-group-count">${list.length}</span>`;
            const body = document.createElement('div');
            body.className = 'module-group-body';
            list.forEach(item => body.appendChild(item));
            group.appendChild(header);
            group.appendChild(body);
            fragment.appendChild(group);
        });

        if (!groups.length) {
            const empty = document.createElement('div');
            empty.className = 'module-empty';
            empty.textContent = '没有可用模块';
            fragment.appendChild(empty);
        }
    } else {
        visibleItems.forEach(item => fragment.appendChild(item));
        if (!visibleItems.length) {
            const empty = document.createElement('div');
            empty.className = 'module-empty';
            empty.textContent = '没有匹配的模块';
            fragment.appendChild(empty);
        }
    }

    container.replaceChildren(fragment);
}

function toggleModuleGroup(header) {
    const group = header.parentElement;
    const title = header.querySelector('span')?.textContent || '';
    group.classList.toggle('collapsed');
    if (group.classList.contains('collapsed')) moduleCollapsed[title] = true;
    else delete moduleCollapsed[title];
    saveModuleCollapsed();
}

function filterModules() {
    renderModuleList();
}

function toggleModuleView() {
    const list = document.getElementById('module-list');
    if (!list) return;
    moduleGridView = !moduleGridView;
    list.classList.toggle('module-grid', moduleGridView);
}

/**
 * 刷新当前账号的所有模块数据（开关、权限、配置）
 */
async function refreshAllModulesData(silent = false) {
    const botId = getCurrentBotId();
    if (!botId) {
        // 无账号时不刷新，静默返回
        return;
    }
    try {
        const response = await apiFetch(`/api/modules?bot_id=${botId}`);
        if (response.ok) {
            const modules = await response.json();
            for (const [moduleName, moduleData] of Object.entries(modules)) {
                // 0. 更新 bot_id 标签
                const badge = document.querySelector(`#config-${moduleName} .bot-id-badge`);
                if (badge && moduleData.bot_id) {
                    badge.textContent = `Bot ${moduleData.bot_id}`;
                }
                // 1. 更新启用开关
                const toggle = document.getElementById(`switch-${moduleName}`);
                if (toggle && toggle.checked !== moduleData.enabled) {
                    toggle.checked = moduleData.enabled;
                }
                // 2. 更新权限控件（使用已有的 updatePermissionDisplay）
                if (moduleData.permission_config) {
                    updatePermissionDisplay(moduleName, moduleData.permission_config);
                }
                // 3. 更新配置输入框
                const container = document.getElementById(`config-container-${moduleName}`);
                if (container && moduleData.config) {
                    Object.entries(moduleData.config).forEach(([key, value]) => {
                        // 小组件类型由 widget.set 接收
                        const w = getWidget(moduleName, key);
                        if (w) { w.set(value); return; }
                        const input = container.querySelector(`[id="config-${moduleName}-${key}"]`);
                        if (input) {
                            updateInputValue(input, value);
                        }
                    });
                    applyShowIf(moduleName);
                }
            }
            // 回填完成 = 与服务器一致，清除未保存标记
            markAllModulesClean();
            if (!silent) showToast('模块数据已同步', 'success');
        } else {
            console.warn('获取模块数据失败:', response.status);
        }
    } catch (error) {
        console.error('刷新模块数据出错:', error);
        showToast('刷新模块数据失败', 'error');
    }
}

async function loadModulesForBot(botId) {
    if (!botId) return; // 无连接 Bot 时静默返回
    try {
        const response = await apiFetch(`/api/modules?bot_id=${botId}`);
        if (response.ok) {
            const modules = await response.json();
            console.log('Loaded modules for bot', botId, modules);
        }
    } catch (error) {
        console.error('加载模块失败:', error);
    }
}

async function toggleModule(moduleName) {
    const checkbox = document.getElementById(`switch-${moduleName}`);
    const enabled = checkbox.checked;
    const botId = getCurrentBotId();

    try {
        const formData = new FormData();
        formData.append('enabled', enabled);
        if (botId) formData.append('bot_id', botId);

        const response = await apiFetch(`/api/module/${moduleName}/toggle?bot_id=${botId}`, {
            method: 'POST',
            body: formData
        });
        const result = await response.json();

        if (response.ok && result.status === 'success') {
            isRecentOperation(`toggle-${moduleName}`);
            isRecentOperation(`authority-${moduleName}`);
            showToast(result.message, 'success');
        } else {
            showToast(result.message || '操作失败', 'error');
            checkbox.checked = !enabled;
        }
    } catch (error) {
        showToast('请求失败', 'error');
        checkbox.checked = !enabled;
    }
}

async function savePermission(moduleName, opts) {
    const silent = !!(opts && opts.silent);
    const groupMode = document.getElementById(`group-mode-${moduleName}`).value;
    const groupList = document.getElementById(`group-list-${moduleName}`).value;
    const userMode = document.getElementById(`user-mode-${moduleName}`).value;
    const userList = document.getElementById(`user-list-${moduleName}`).value;
    const botId = getCurrentBotId();

    try {
        const formData = new FormData();
        formData.append('group_mode', groupMode);
        formData.append('group_list', groupList);
        formData.append('user_mode', userMode);
        formData.append('user_list', userList);
        if (botId) formData.append('bot_id', botId);

        const response = await apiFetch(`/api/module/${moduleName}/permission?bot_id=${botId}`, {
            method: 'POST',
            body: formData
        });
        const result = await response.json();
        if (response.ok && result.status === 'success') {
            if (!silent) showToast(result.message, 'success');
            return true;
        }
        if (!silent) showToast(result.message || '保存失败', 'error');
        return false;
    } catch (error) {
        if (!silent) showToast('保存失败', 'error');
        return false;
    }
}

async function saveConfig(moduleName, opts) {
    const silent = !!(opts && opts.silent);
    const configContainer = document.getElementById(`config-container-${moduleName}`);
    if (!configContainer) return true;

    const config = {};
    const botId = getCurrentBotId();

    configContainer.querySelectorAll('.config-item').forEach(item => {
        const key = item.getAttribute('data-config-key');
        const type = item.getAttribute('data-config-type') || 'auto';
        // 小组件类型（string_list/list/dynamic/repeater）由 widget 收集值
        const w = getWidget(moduleName, key);
        if (w) { Object.assign(config, w.get()); return; }
        const el = item.querySelector(`[id^="config-${moduleName}-"]`);
        if (!el) return;

        switch (type) {
            case 'boolean':
                config[key] = el.checked;
                break;
            case 'integer':
                config[key] = parseInt(el.value) || 0;
                break;
            case 'float':
            case 'number':
                config[key] = parseFloat(el.value) || 0;
                break;
            case 'list':
                config[key] = el.value.split('\n').map(s => s.trim()).filter(s => s);
                break;
            case 'select':
            case 'textarea':
            case 'password':
            case 'string':
            default:
                if (el.type === 'checkbox') {
                    config[key] = el.checked;
                } else if (el.type === 'number') {
                    config[key] = parseFloat(el.value);
                } else {
                    config[key] = el.value;
                }
        }
    });

    try {
        const response = await apiFetch(`/api/module/${moduleName}/config?bot_id=${botId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });
        const result = await response.json();

        isRecentOperation(`config-${moduleName}`);
        if (response.ok && result.status === 'success') {
            if (!silent) showToast(result.message, 'success');
            return true;
        }
        if (!silent) showToast(result.message || '保存失败', 'error');
        return false;
    } catch (error) {
        if (!silent) showToast('保存失败', 'error');
        return false;
    }
}

async function reloadModules() {
    const botId = getCurrentBotId();
    if (botId === null) {
        showToast('请先选择账号', 'warning');
        return;
    }
    showToast('正在重新加载模块...', 'info');
    try {
        const response = await apiFetch(`/api/modules/reload?bot_id=${botId}`, { method: 'POST' });
        const result = await response.json();

        if (response.ok && result.status === 'success') {
            showToast(result.message, 'success');
        } else {
            showToast(result.message || '重载失败', 'error');
        }
    } catch (error) {
        showToast('请求失败', 'error');
    }
}

// ==================== 自动保存（统一保存心智） ====================
// 配置/权限修改后 2s 防抖自动保存；卡片标题与权限区显示保存状态徽标。
// 手动「保存配置」按钮保留为立即保存兜底（点击即保存）。

const _autoSave = {};
const AUTOSAVE_DELAY = 2000;

function _saveState(mod) {
    if (!_autoSave[mod]) _autoSave[mod] = { timer: null, dirty: false };
    return _autoSave[mod];
}

function updateSaveStatus(mod, state) {
    const el = document.getElementById(`save-status-${mod}`);
    const textMap = {
        dirty: '● 未保存更改',
        saving: '⏳ 保存中…',
        saved: '✓ 已保存 ' + new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
        error: '✗ 保存失败，请重试',
    };
    const clsMap = { dirty: 'save-status-dirty', saving: 'save-status-saving', saved: 'save-status-saved', error: 'save-status-error' };
    const text = textMap[state] || '';
    if (el) { el.textContent = text; el.className = 'save-status ' + (clsMap[state] || ''); }
}

/** 模块配置/权限被修改：标记未保存并调度自动保存（输入事件 / 权限编辑器调用）。 */
function markModuleDirty(mod) {
    const st = _saveState(mod);
    st.dirty = true;
    updateSaveStatus(mod, 'dirty');
    clearTimeout(st.timer);
    st.timer = setTimeout(() => { doAutoSave(mod); }, AUTOSAVE_DELAY);
}

/** 立即保存某模块的配置（自动保存与手动按钮共用）。 */
async function doAutoSave(mod) {
    const st = _saveState(mod);
    clearTimeout(st.timer);
    st.timer = null;
    if (!st.dirty) return true;
    updateSaveStatus(mod, 'saving');
    const okConfig = await saveConfig(mod, { silent: true });
    if (okConfig) {
        st.dirty = false;
        updateSaveStatus(mod, 'saved');
        return true;
    }
    updateSaveStatus(mod, 'error');
    return false;
}

/** 清除某模块的未保存标记（数据回填同步后调用）。 */
function markModuleClean(mod) {
    const st = _saveState(mod);
    st.dirty = false;
    clearTimeout(st.timer);
    st.timer = null;
    updateSaveStatus(mod, '');
}

/** 手动「保存配置」按钮：强制立即保存（无论是否有未保存更改）。 */
function forceSave(mod) {
    const st = _saveState(mod);
    st.dirty = true;
    doAutoSave(mod);
}

function markAllModulesClean() {
    Object.keys(_autoSave).forEach(mod => markModuleClean(mod));
}

// ==================== Agent 特殊兼容面板 ====================
function agentBotQuery() {
    const botId = getCurrentBotId();
    return botId ? `?bot_id=${botId}` : '';
}

function agentEsc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
        {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]
    ));
}

async function loadAgentPanels() {
    await Promise.all([loadAgentTasks(), loadAgentProactive()]);
}

async function loadAgentTasks() {
    const statusEl = document.getElementById('agent-task-status');
    if (!getCurrentBotId()) {
        if (statusEl) statusEl.textContent = '请先选择账号';
        return;
    }
    if (statusEl) statusEl.textContent = '加载中…';
    try {
        const res = await apiFetch(`/api/agent/tasks${agentBotQuery()}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        renderAgentTasks(data.tasks || []);
        if (statusEl) statusEl.textContent = '';
    } catch (e) {
        console.error('加载 Agent 任务失败:', e);
        if (statusEl) statusEl.textContent = '加载失败';
    }
}

function renderAgentTasks(tasks) {
    const body = document.getElementById('agent-task-body');
    if (!body) return;
    if (!tasks.length) {
        body.innerHTML = '<tr><td colspan="7" style="color:#718096;">—</td></tr>';
        return;
    }
    body.innerHTML = tasks.map(t => {
        const taskId = t.task_id || t.id || '';
        const next = t.next_trigger_time
            ? new Date(t.next_trigger_time * 1000).toLocaleString()
            : '—';
        return `<tr>
            <td>${agentEsc(taskId)}</td>
            <td>${agentEsc(t.session_id || '')}</td>
            <td>${agentEsc(t.repeat || '')}</td>
            <td>${agentEsc(next)}</td>
            <td>${agentEsc(t.fired_count ?? '')}</td>
            <td>${agentEsc(t.content || '')}</td>
            <td>
                <button class="btn-save" style="padding:2px 8px;font-size:12px;" onclick="triggerAgentTask('${agentEsc(taskId)}')">立即触发</button>
                <button class="btn-save" style="padding:2px 8px;font-size:12px;background:#a33;" onclick="cancelAgentTask('${agentEsc(taskId)}')">取消</button>
            </td>
        </tr>`;
    }).join('');
}

async function addAgentTask() {
    const type = document.getElementById('agent-task-type').value;
    const target = document.getElementById('agent-task-target').value.trim();
    const trigger = document.getElementById('agent-task-trigger').value.trim();
    const content = document.getElementById('agent-task-content').value.trim();
    if (!target || !trigger || !content) {
        showToast('请填写目标、时间表达式和内容', 'error');
        return;
    }
    try {
        const res = await apiFetch(`/api/agent/tasks${agentBotQuery()}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                is_group: type === 'group',
                target,
                trigger,
                content,
            }),
        });
        const data = await res.json();
        if (!res.ok) {
            showToast(data.message || '添加失败', 'error');
            return;
        }
        showToast('任务已添加', 'success');
        document.getElementById('agent-task-trigger').value = '';
        document.getElementById('agent-task-content').value = '';
        await loadAgentTasks();
    } catch (e) {
        console.error('添加 Agent 任务失败:', e);
        showToast('添加失败', 'error');
    }
}

async function triggerAgentTask(taskId) {
    try {
        const res = await apiFetch(`/api/agent/tasks/${encodeURIComponent(taskId)}/trigger${agentBotQuery()}`, {
            method: 'POST',
        });
        const data = await res.json();
        if (!res.ok) {
            showToast(data.message || '触发失败', 'error');
            return;
        }
        showToast(data.message || '已触发', 'success');
        await loadAgentTasks();
    } catch (e) {
        console.error('触发 Agent 任务失败:', e);
        showToast('触发失败', 'error');
    }
}

async function cancelAgentTask(taskId) {
    try {
        const res = await apiFetch(`/api/agent/tasks/${encodeURIComponent(taskId)}/cancel${agentBotQuery()}`, {
            method: 'POST',
        });
        const data = await res.json();
        if (!res.ok) {
            showToast(data.message || '取消失败', 'error');
            return;
        }
        showToast(data.message || '已取消', 'success');
        await loadAgentTasks();
    } catch (e) {
        console.error('取消 Agent 任务失败:', e);
        showToast('取消失败', 'error');
    }
}

async function loadAgentProactive() {
    const statusEl = document.getElementById('agent-proactive-status');
    if (!getCurrentBotId()) {
        if (statusEl) statusEl.textContent = '请先选择账号';
        return;
    }
    if (statusEl) statusEl.textContent = '加载中…';
    try {
        const res = await apiFetch(`/api/agent/proactive/status${agentBotQuery()}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        renderAgentProactive(data.sessions || []);
        if (statusEl) statusEl.textContent = '';
    } catch (e) {
        console.error('加载 Agent 主动状态失败:', e);
        if (statusEl) statusEl.textContent = '加载失败';
    }
}

function renderAgentProactive(sessions) {
    const body = document.getElementById('agent-proactive-body');
    if (!body) return;
    if (!sessions.length) {
        body.innerHTML = '<tr><td colspan="7" style="color:#718096;">—</td></tr>';
        return;
    }
    body.innerHTML = sessions.map(s => {
        const next = s.next_trigger_time
            ? new Date(s.next_trigger_time * 1000).toLocaleString()
            : '—';
        return `<tr>
            <td>${agentEsc(s.session_id || '')}</td>
            <td>${agentEsc(s.type || '')}</td>
            <td>${s.enabled ? '✅' : '❌'}</td>
            <td>${agentEsc(s.unanswered ?? '')}</td>
            <td>${agentEsc(next)}</td>
            <td>${agentEsc(s.timer || '')}</td>
            <td>
                <button class="btn-save" style="padding:2px 8px;font-size:12px;" onclick="triggerAgentProactive('${agentEsc(s.session_id || '')}')">立即触发</button>
            </td>
        </tr>`;
    }).join('');
}

async function triggerAgentProactive(sessionId) {
    try {
        const res = await apiFetch(`/api/agent/proactive/trigger${agentBotQuery()}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId }),
        });
        const data = await res.json();
        if (!res.ok) {
            showToast(data.message || '触发失败', 'error');
            return;
        }
        showToast(data.message || '已触发', 'success');
        await loadAgentProactive();
    } catch (e) {
        console.error('触发 Agent 主动消息失败:', e);
        showToast('触发失败', 'error');
    }
}

// ==================== 日志 WebSocket ====================
let ws = null;

function currentLogMode() {
    return showRawLogs ? 'raw' : 'simple';
}

function reconnectLogsWebSocket() {
    if (ws) {
        ws.onclose = null;
        ws.close();
        ws = null;
    }
    connectLogsWebSocket();
}

function connectLogsWebSocket() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(apiWsUrl(`${wsProtocol}//${window.location.host}/ws/logs?mode=${currentLogMode()}`));

    ws.onmessage = function(event) {
        try {
            const data = JSON.parse(event.data);

            if (data.type) {
                handleConfigUpdate(data);
                return;
            }

            if (Array.isArray(data)) {
                updateLogsDisplay(data);
            }
        } catch (e) {}
    };

    ws.onclose = function() {
        setTimeout(connectLogsWebSocket, 3000);
    };
}

// ==================== 动态配置更新 ====================

function handleConfigUpdate(data) {
    switch (data.type) {
        case 'webui_config_updated':
            handleWebuiConfigUpdate(data.config);
            break;
        case 'module_config_updated':
            if (data.bot_id === getCurrentBotId() || data.bot_id === null) {
                handleModuleConfigUpdate(data.module, data.config);
            }
            break;
        case 'module_authority_updated':
            if (data.bot_id === getCurrentBotId() || data.bot_id === null) {
                handleModuleAuthorityUpdate(data.module, data);
            }
            break;
        case 'permission_updated':
            if (data.bot_id === getCurrentBotId() || data.bot_id === null) {
                refreshModulePermission(data.module);
            }
            break;
        case 'modules_reloaded':
            // 模块重载/登录装配完成 → 静默同步模块数据（手动刷新已有「正在重新加载」提示）
            refreshAllModulesData(true);
            break;
        case 'single_service_updated':
            if (data.single_service) {
                singleServiceConfig = data.single_service;
                for (const [modName, enabled] of Object.entries(singleServiceConfig)) {
                    const toggle = document.getElementById(`single-service-switch-${modName}`);
                    if (toggle) {
                        toggle.checked = enabled;
                    }
                }
                updateAllSingleServiceWarnings();
            }
            break;
        case 'multi_group_updated':
            if (data.multi_group) {
                multiGroupData = data.multi_group;
                if (document.getElementById('multi-group-modal').classList.contains('show')) {
                    renderMultiGroupList();
                }
                updateAllSingleServiceWarnings();
            }
            break;
        case 'bot_status_updated':
            handleBotStatusUpdate(data.bot || {});
            break;
    }
}

function handleWebuiConfigUpdate(config) {
    const prevRaw = showRawLogs;
    webuiConfig = config;
    visibleLevels = config.logs?.visible_levels || ['info', 'warning', 'error'];
    maxLogLines = config.logs?.max_lines || 50;
    showRawLogs = config.logs?.show_raw_logs === true;
    updateLogLevelCheckboxes();
    renderLogsAll();
    if (prevRaw !== showRawLogs) reconnectLogsWebSocket();
    showToast('WebUI 配置已更新', 'info');
}

/** Bot 连接状态实时更新（WS 推送，无需手动刷新）。 */
function handleBotStatusUpdate(bot) {
    if (!bot || bot.index === undefined) return;
    const idx = botsData.findIndex(b => b.index === bot.index);
    const oldStatus = idx >= 0 ? botsData[idx].status : null;
    const merged = Object.assign({}, idx >= 0 ? botsData[idx] : {}, bot);
    if (idx >= 0) botsData[idx] = merged; else botsData.push(merged);

    // 当前选中账号 → 刷新顶栏状态显示
    if (bot.index === getCurrentBotIndex()) updateBotStatusDisplay();
    // 账号管理弹窗内卡片 → 轻量更新状态徽标
    updateBotCardStatus(merged);
    // 状态真实变化 → 提示（error 带原因）
    if (oldStatus && oldStatus !== bot.status) {
        const detail = bot.last_error ? `: ${bot.last_error}` : '';
        const type = bot.status === 'error' ? 'error' : (bot.status === 'connected' ? 'success' : 'info');
        showToast(`Bot #${bot.index} ${getStatusText(bot.status)}${detail}`, type);
    }
}

/** 账号管理弹窗内的状态徽标轻量更新（不重建整个卡片）。 */
function updateBotCardStatus(bot) {
    const card = document.querySelector(`#bot-cards-container .bot-config-card[data-index="${bot.index}"]`);
    if (!card) return;
    const statusEl = card.querySelector('.bot-card-status');
    if (statusEl) {
        statusEl.className = 'bot-card-status ' + (bot.status || '');
        statusEl.innerHTML = `<i class="fas fa-circle"></i> ${bot.status || ''}`;
    }
}

const _recentManualOperations = {};
const _operationDebounceMs = 2000;

function isRecentOperation(key) {
    const now = Date.now();
    if (_recentManualOperations[key] && (now - _recentManualOperations[key]) < _operationDebounceMs) {
        return true;
    }
    _recentManualOperations[key] = now;
    return false;
}

function handleModuleConfigUpdate(moduleName, config) {
    if (isRecentOperation(`config-${moduleName}`)) {}

    const container = document.getElementById(`config-container-${moduleName}`);
    if (!container) return;

    Object.entries(config).forEach(([key, value]) => {
        const input = container.querySelector(`[id="config-${moduleName}-${key}"]`);
        if (input) {
            updateInputValue(input, value);
        }
    });
}

function handleModuleAuthorityUpdate(moduleName, data) {
    const toggle = document.querySelector(`input[onchange="toggleModule('${moduleName}', this.checked)"]`);
    if (toggle && toggle.checked !== data.enabled) {
        toggle.checked = data.enabled;
    }

    if (data.permission) {
        updatePermissionDisplay(moduleName, data.permission);
    }

    const isToggleOnly = !data.permission ||
        (isRecentOperation(`toggle-${moduleName}`) && !isRecentOperation(`authority-${moduleName}`));

    if (!isRecentOperation(`authority-${moduleName}`) && !isRecentOperation(`toggle-${moduleName}`)) {
        if (isToggleOnly) {
            showToast(`模块 ${moduleName} 已${data.enabled ? '启用' : '禁用'}`, 'success');
        } else {
            showToast(`模块 ${moduleName} 权限已更新`, 'success');
        }
    }
}

function updateInputValue(input, value) {
    if (input.type === 'checkbox') {
        input.checked = Boolean(value);
    } else if (input.tagName === 'SELECT') {
        input.value = String(value);
    } else if (input.tagName === 'TEXTAREA') {
        if (input.getAttribute('data-type') === 'list' && Array.isArray(value)) {
            input.value = value.join('\n');
        } else {
            input.value = String(value);
        }
        autoResize(input);
    } else {
        input.value = String(value);
    }

    input.classList.add('config-updated');
    setTimeout(() => input.classList.remove('config-updated'), 1000);
}

function updatePermissionDisplay(moduleName, permission) {
    const groupModeSelect = document.getElementById(`group-mode-${moduleName}`);
    if (groupModeSelect) {
        groupModeSelect.value = permission.group_mode;
    }

    const groupListInput = document.getElementById(`group-list-${moduleName}`);
    if (groupListInput) {
        groupListInput.value = permission.group_list.join('\n');
    }

    const userModeSelect = document.getElementById(`user-mode-${moduleName}`);
    if (userModeSelect) {
        userModeSelect.value = permission.user_mode;
    }

    const userListInput = document.getElementById(`user-list-${moduleName}`);
    if (userListInput) {
        userListInput.value = permission.user_list.join('\n');
    }
}

async function refreshModulePermission(moduleName) {
    const botId = getCurrentBotId();
    try {
        const url = botId ? `/api/modules/${moduleName}?bot_id=${botId}` : `/api/modules/${moduleName}`;
        const response = await apiFetch(url);
        if (response.ok) {
            const modules = await response.json();
            if (modules[moduleName]) {
                updatePermissionDisplay(moduleName, modules[moduleName].permission_config);
            }
        }
    } catch (e) {
        console.error('刷新权限失败:', e);
    }
}

function updateLogLevelCheckboxes() {
    // 日志设置弹窗的级别复选框按 id 同步（原 #logs-config-form 选择器不存在，永不生效）
    ['debug', 'info', 'warning', 'error'].forEach(level => {
        const cb = document.getElementById(`log-level-${level}`);
        if (cb) cb.checked = visibleLevels.includes(level);
    });
}

// ==================== 控制台日志（增量渲染 + 关键字过滤 + 暂停） ====================
let logCache = [];        // 服务端推送的原始日志（含被过滤掉的）
let logFilterText = '';   // 关键字过滤（匹配消息/级别/时间戳）
let logPaused = false;    // 暂停自动滚动与增量渲染
let pendingLogCount = 0;  // 暂停期间新到的条数

function _logKey(log) {
    return (log.timestamp || '') + '|' + (log.level || '') + '|' + (log.message || '');
}

function _logMatches(log) {
    if (!logFilterText) return true;
    const kw = logFilterText.toLowerCase();
    return String(log.message || '').toLowerCase().includes(kw)
        || String(log.level || '').toLowerCase().includes(kw)
        || String(log.timestamp || '').toLowerCase().includes(kw);
}

/** 简洁日志模式：隐藏普通 API 成功日志，保留消息交互/通知/系统/错误。 */
function _isSimpleLog(log) {
    if (showRawLogs) return true;
    const msg = String(log.message || '');
    const level = String(log.level || '').toLowerCase();
    const isApiError = ['warning', 'error'].includes(level);
    const isUserApiLog = msg.includes('[发送->]') || msg.includes('[请求->]');
    if (msg.startsWith('[API]') && !isUserApiLog && !isApiError) return false;
    if (!isApiError && (msg.includes('API(->)') || msg.includes('API(<-)'))) return false;
    return true;
}

/** 创建单条日志 DOM（textContent 防 XSS：用户消息原文会进日志）。 */
function _buildLogItem(log) {
    const div = document.createElement('div');
    div.className = 'log-item';
    const time = document.createElement('span');
    time.className = 'log-time';
    time.textContent = log.timestamp;
    const level = document.createElement('span');
    level.className = 'log-level ' + (log.level || '');
    level.textContent = String(log.level || '').toUpperCase();
    const msg = document.createElement('span');
    msg.className = 'log-message';
    msg.textContent = log.message;
    div.appendChild(time);
    div.appendChild(level);
    div.appendChild(msg);
    return div;
}

/** 全量重渲染（过滤激活 / 暂停恢复 / 服务器窗口重置时）。 */
function renderLogsAll() {
    const logsContainer = document.getElementById('logs-container');
    const isBottom = !logPaused && (logsContainer.scrollHeight - logsContainer.scrollTop - logsContainer.clientHeight < 50);
    logsContainer.innerHTML = '';
    let shown = 0;
    for (const log of logCache) {
        if (_logMatches(log) && _isSimpleLog(log)) { logsContainer.appendChild(_buildLogItem(log)); shown++; }
    }
    updateLogFilterCount(shown);
    if (isBottom) logsContainer.scrollTop = logsContainer.scrollHeight;
}

/** 增量渲染：新数组与缓存尾部对齐求差，只追加新增行（不再全量重绘）。 */
function updateLogsDisplay(newArr) {
    const arr = Array.isArray(newArr) ? newArr : [];
    const firstBatch = logCache.length === 0;  // 首次推送：清掉服务端渲染的初始日志，避免重复

    // diff：从尾部对齐新旧数组（服务端窗口 = 最近 N 条，旧行仍在窗口内时尾部必然相同）
    let i = arr.length - 1, j = logCache.length - 1;
    while (i >= 0 && j >= 0 && _logKey(arr[i]) === _logKey(logCache[j])) { i--; j--; }
    const added = arr.slice(0, i + 1);
    const noCommonTail = logCache.length > 0 && i >= arr.length - 1;  // 无公共尾部（文件轮转/窗口跳变）→ 全量
    logCache = arr.slice();

    if (logPaused) {
        pendingLogCount += added.length;
        updatePauseBtn();
        return;
    }
    if (noCommonTail || logFilterText || firstBatch) {
        renderLogsAll();
        return;
    }

    // 常规增量：仅追加新增行，保持滚动跟随
    const logsContainer = document.getElementById('logs-container');
    const isBottom = logsContainer.scrollHeight - logsContainer.scrollTop - logsContainer.clientHeight < 50;
    for (const log of added) {
        if (_isSimpleLog(log)) logsContainer.appendChild(_buildLogItem(log));
    }
    if (isBottom) logsContainer.scrollTop = logsContainer.scrollHeight;
    updateLogFilterCount(logCache.length);
}

/** 过滤输入（HTML oninput 触发）。 */
function onLogFilterInput(value) {
    logFilterText = String(value || '').trim();
    renderLogsAll();
}

/** 暂停/继续（HTML onclick 触发）。 */
function toggleLogPause() {
    logPaused = !logPaused;
    if (!logPaused) {
        pendingLogCount = 0;
        renderLogsAll();
    }
    updatePauseBtn();
}

function updatePauseBtn() {
    const btn = document.getElementById('log-pause-btn');
    if (!btn) return;
    if (logPaused) {
        btn.innerHTML = `<i class="fas fa-play"></i> 继续${pendingLogCount ? ` (${pendingLogCount})` : ''}`;
    } else {
        btn.innerHTML = '<i class="fas fa-pause"></i> 暂停';
    }
}

function updateLogFilterCount(shown) {
    const el = document.getElementById('log-filter-count');
    if (!el) return;
    if (logFilterText) {
        el.textContent = `${shown}/${logCache.length} 条匹配`;
        el.style.display = '';
    } else {
        el.style.display = 'none';
    }
}

async function refreshLogs() {
    try {
        const response = await apiFetch(`/api/logs?mode=${currentLogMode()}`);
        if (response.ok) {
            const logs = await response.json();
            updateLogsDisplay(logs);
        }
    } catch (e) {}
}

async function loadWebuiConfig() {
    try {
        const response = await apiFetch('/api/webui/config');
        if (response.ok) {
            webuiConfig = await response.json();
            visibleLevels = webuiConfig.logs?.visible_levels || ['info', 'warning', 'error'];
            maxLogLines = webuiConfig.logs?.max_lines || 50;
            showRawLogs = webuiConfig.logs?.show_raw_logs === true;
        }
    } catch (e) {}
}

// ==================== 控制台拖动调整大小 ====================

function initConsoleResize() {
    const resizeHandle = document.getElementById('console-resize-handle');
    const consoleWrapper = document.getElementById('console-wrapper');
    const consoleBody = consoleWrapper.querySelector('.console-body');

    if (!resizeHandle || !consoleWrapper || !consoleBody) return;

    let isResizing = false;
    let startY = 0;
    let startHeight = 0;
    const minHeight = 100;
    const maxHeight = 600;
    const headerHeight = 40;

    resizeHandle.addEventListener('mousedown', function(e) {
        if (consoleWrapper.classList.contains('collapsed')) return;

        isResizing = true;
        startY = e.clientY;
        startHeight = consoleBody.offsetHeight;

        document.body.style.cursor = 'ns-resize';
        document.body.style.userSelect = 'none';
        resizeHandle.style.background = 'rgba(66, 153, 225, 0.5)';

        e.preventDefault();
    });

    document.addEventListener('mousemove', function(e) {
        if (!isResizing) return;

        const deltaY = startY - e.clientY;
        let newHeight = startHeight + deltaY;
        newHeight = Math.max(minHeight, Math.min(maxHeight, newHeight));
        consoleBody.style.height = newHeight + 'px';
        localStorage.setItem('console_height', newHeight);
    });

    document.addEventListener('mouseup', function() {
        if (!isResizing) return;

        isResizing = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        resizeHandle.style.background = '';
    });

    const savedHeight = localStorage.getItem('console_height');
    if (savedHeight) {
        const height = parseInt(savedHeight, 10);
        if (height >= minHeight && height <= maxHeight) {
            consoleBody.style.height = height + 'px';
        }
    }
}

// ==================== 单一服务模式 ====================

let singleServiceConfig = {};

async function loadSingleServiceConfig() {
    try {
        const response = await apiFetch('/api/webui/single-service');
        if (response.ok) {
            const data = await response.json();
            singleServiceConfig = data.single_service || {};
            for (const modName of Object.keys(singleServiceConfig)) {
                const toggle = document.getElementById(`single-service-switch-${modName}`);
                if (toggle) {
                    toggle.checked = singleServiceConfig[modName];
                }
            }
            updateAllSingleServiceWarnings();
        }
    } catch (e) {
        console.error('加载单一服务配置失败:', e);
    }
}

async function toggleSingleService(moduleName) {
    const toggle = document.getElementById(`single-service-switch-${moduleName}`);
    const enabled = toggle.checked;

    singleServiceConfig[moduleName] = enabled;

    try {
        const response = await apiFetch('/api/webui/single-service', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ single_service: singleServiceConfig })
        });
        const result = await response.json();
        if (response.ok && result.status === 'success') {
            updateSingleServiceWarning(moduleName);
        } else {
            showToast(result.message || '保存失败', 'error');
            toggle.checked = !enabled;
            singleServiceConfig[moduleName] = !enabled;
        }
    } catch (error) {
        showToast('请求失败', 'error');
        toggle.checked = !enabled;
        singleServiceConfig[moduleName] = !enabled;
    }
}

function updateSingleServiceWarning(moduleName) {
    const warningEl = document.getElementById(`single-service-warning-${moduleName}`);
    if (!warningEl) return;

    const enabled = singleServiceConfig[moduleName];
    if (!enabled) {
        warningEl.style.display = 'none';
        return;
    }

    const currentBotId = getCurrentBotId();
    if (!currentBotId) {
        warningEl.style.display = 'none';
        return;
    }
    const currentBot = botsData.find(b => b.bot_id == currentBotId);
    if (!currentBot) {
        warningEl.style.display = 'none';
        return;
    }
    const currentIndex = currentBot.index;

    // 对齐后端 is_single_service_skipped：仅同群 ≥2 个在线 Bot 且该群指定了服务账号时生效
    Promise.all([
        apiFetch('/api/webui/multi-group').then(r => r.json()),
        apiFetch('/api/bots/groups').then(r => r.json()),
    ]).then(([configData, groupsData]) => {
        const groupsConfig = (configData.multi_group || { groups: {} }).groups || {};
        const botsGroups = groupsData.bots_groups || {};

        // 找出「当前账号不会触发」的群：
        //   1. 群配置了 service_bot_index；2. 服务账号不是当前账号；3. 群内在线 Bot ≥ 2
        const affectedGroups = [];
        for (const [gid, gConfig] of Object.entries(groupsConfig)) {
            const serviceBotIndex = gConfig.service_bot_index;
            if (serviceBotIndex === undefined || serviceBotIndex === null) continue;
            if (Number(serviceBotIndex) === Number(currentIndex)) continue;  // 当前账号是指定服务账号 → 不提示

            let onlineInGroup = 0;
            for (const [idx, bg] of Object.entries(botsGroups)) {
                const bot = botsData.find(b => b.index == idx);
                if (!bot || bot.status !== 'connected') continue;
                if ((bg.groups || []).includes(Number(gid))) onlineInGroup++;
            }
            if (onlineInGroup >= 2) affectedGroups.push(gid);
        }

        if (affectedGroups.length === 0) {
            warningEl.style.display = 'none';
            return;
        }
        const span = warningEl.querySelector('span');
        if (span) {
            span.textContent = `当前账号非本模块指定服务账号，该模块在群${affectedGroups.join('、')}下不会触发`;
        }
        warningEl.style.display = 'flex';
    }).catch(() => {
        warningEl.style.display = 'none';
    });
}

function updateAllSingleServiceWarnings() {
    const modules = document.querySelectorAll('[id^="single-service-warning-"]');
    modules.forEach(el => {
        const modName = el.id.replace('single-service-warning-', '');
        updateSingleServiceWarning(modName);
    });
}

// ==================== 多群管理 ====================

let multiGroupData = { show_all: false, groups: {} };
let botsGroupsData = {};

function openMultiGroupModal() {
    openModal('multi-group-modal');
    loadMultiGroupData();
}

function closeMultiGroupModal() {
    closeModal('multi-group-modal');
}

async function loadMultiGroupData() {
    const container = document.getElementById('multi-group-container');
    container.innerHTML = '<div class="multi-group-loading"><i class="fas fa-spinner fa-spin"></i> 加载中...</div>';

    try {
        const [configResp, groupsResp] = await Promise.all([
            apiFetch('/api/webui/multi-group'),
            apiFetch('/api/bots/groups')
        ]);

        const configData = await configResp.json();
        const groupsData = await groupsResp.json();

        multiGroupData = configData.multi_group || { show_all: false, groups: {} };
        botsGroupsData = groupsData.bots_groups || {};

        document.getElementById('multi-group-show-all').checked = multiGroupData.show_all || false;

        renderMultiGroupList();
    } catch (error) {
        container.innerHTML = '<div class="multi-group-empty"><i class="fas fa-exclamation-circle"></i>加载失败</div>';
        showToast('加载多群数据失败', 'error');
    }
}

function renderMultiGroupList() {
    const container = document.getElementById('multi-group-container');
    const showAll = document.getElementById('multi-group-show-all').checked;

    const allBotIds = Object.keys(botsGroupsData).map(Number);

    const botIndexToLabel = {};
    allBotIds.forEach(idx => {
        const botData = botsGroupsData[idx];
        botIndexToLabel[idx] = botData.bot_id ? `Bot ${botData.bot_id}` : `账号 #${idx}`;
    });

    const groupMap = {};
    for (const [botIndexStr, botData] of Object.entries(botsGroupsData)) {
        const botIndex = parseInt(botIndexStr);
        const groups = botData.groups || [];
        const groupsInfo = botData.groups_info || [];

        groups.forEach((groupId, i) => {
            if (!groupMap[groupId]) {
                groupMap[groupId] = {
                    group_id: groupId,
                    group_name: '',
                    bots: []
                };
            }

            let groupName = '';
            if (groupsInfo[i]) {
                groupName = groupsInfo[i].group_name || '';
            }

            groupMap[groupId].group_name = groupName || groupMap[groupId].group_name;
            if (!groupMap[groupId].bots.includes(botIndex)) {
                groupMap[groupId].bots.push(botIndex);
            }
        });
    }

    let entries = Object.entries(groupMap);

    if (!showAll) {
        entries = entries.filter(([_, g]) => g.bots.length > 1);
    }

    entries.sort((a, b) => b[1].bots.length - a[1].bots.length);

    if (entries.length === 0) {
        const msg = showAll ? '暂无已获取的群' : '暂无同时存在多个账号的群';
        container.innerHTML = `<div class="multi-group-empty"><i class="fas fa-inbox"></i><p>${msg}</p></div>`;
        return;
    }

    container.innerHTML = '';

    entries.forEach(([groupId, groupInfo]) => {
        const row = document.createElement('div');
        row.className = 'multi-group-row';

        const isSingleBot = groupInfo.bots.length <= 1;
        const currentConfig = multiGroupData.groups[groupId] || {};
        const currentServiceBotIndex = currentConfig.service_bot_index;
        const showAllChecked = showAll;

        let botOptionsHtml = '';
        const sortedBots = [...groupInfo.bots].sort((a, b) => a - b);
        sortedBots.forEach(botIndex => {
            const label = botIndexToLabel[botIndex] || `账号 #${botIndex}`;
            const selected = (currentServiceBotIndex === botIndex) ? 'selected' : '';
            botOptionsHtml += `<option value="${botIndex}" ${selected}>${label}</option>`;
        });

        const groupNameDisplay = groupInfo.group_name || `群 ${groupId}`;

        row.innerHTML = `
            <div class="multi-group-row-left">
                <span class="group-name" title="${groupNameDisplay}">${groupNameDisplay}</span>
                <span class="group-id">(${groupId})</span>
                <span class="group-bots">${groupInfo.bots.length}个账号</span>
            </div>
            <div class="multi-group-row-right">
                <span class="multi-group-service-label">服务账号</span>
                <select class="multi-group-service-select" data-group-id="${groupId}" ${isSingleBot ? 'disabled' : ''}>
                    <option value="">-- 请选择 --</option>
                    ${botOptionsHtml}
                </select>
            </div>
        `;

        container.appendChild(row);

        const select = row.querySelector('.multi-group-service-select');
        if (!isSingleBot && currentServiceBotIndex !== undefined && currentServiceBotIndex !== null) {
            select.value = currentServiceBotIndex;
        }

        if (isSingleBot) {
            if (sortedBots.length > 0) {
                select.value = sortedBots[0];
            }
        }

        select.addEventListener('change', function() {
            const gId = this.getAttribute('data-group-id');
            const value = this.value;
            saveMultiGroupRow(gId, value);
        });
    });
}

function toggleMultiGroupShowAll() {
    multiGroupData.show_all = document.getElementById('multi-group-show-all').checked;
    saveMultiGroupConfig(false);
    renderMultiGroupList();
}

async function saveMultiGroupRow(groupId, serviceBotIndex) {
    if (!multiGroupData.groups) {
        multiGroupData.groups = {};
    }

    if (serviceBotIndex === '') {
        delete multiGroupData.groups[groupId];
    } else {
        multiGroupData.groups[groupId] = {
            service_bot_index: parseInt(serviceBotIndex)
        };
    }

    await saveMultiGroupConfig(true);
}

async function saveMultiGroupConfig(quiet) {
    try {
        const response = await apiFetch('/api/webui/multi-group', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ multi_group: multiGroupData })
        });
        const result = await response.json();
        if (response.ok && result.status === 'success') {
            if (!quiet) {
                //showToast('多群管理配置已保存', 'success');
            }
            updateAllSingleServiceWarnings();
        } else {
            if (!quiet) showToast(result.message || '保存失败', 'error');
        }
    } catch (error) {
        if (!quiet) showToast('保存失败', 'error');
    }
}

// ==================== 配置分组折叠 ====================

function toggleConfigGroup(headerEl) {
    if (!headerEl.classList.contains('collapsible')) return;
    headerEl.classList.toggle('collapsed');
}



// ==================== 启动 ====================
document.addEventListener('DOMContentLoaded', function() {
    // 恢复上次选择的账号（而非默认 #0）
    restoreBotSelection();
    if (!currentBotIndex && botsData && botsData.length > 0) {
        currentBotId = botsData[0].bot_id;
        updateBotStatusDisplay();
    }

    connectLogsWebSocket();

    // 捕获初始模块节点，供侧边栏分组/搜索使用
    allModuleItems = Array.from(document.querySelectorAll('#module-list .module-item'));

    // 模块侧边栏：恢复折叠状态并分组渲染
    loadModuleCollapsed();
    renderModuleList();

    // 快捷键：/ 聚焦模块搜索框
    document.addEventListener('keydown', function(e) {
        const tag = document.activeElement && document.activeElement.tagName;
        if (e.key === '/' && tag !== 'INPUT' && tag !== 'TEXTAREA') {
            e.preventDefault();
            const search = document.getElementById('module-search');
            if (search) search.focus();
        }
    });

    const firstMod = document.querySelector('.module-card-btn.active');
    if (!firstMod) {
        const mod = document.querySelector('.module-card-btn');
        if(mod) mod.click();
    }

    document.querySelectorAll('textarea.auto-resize').forEach(el => autoResize(el));
    loadWebuiConfig();
    loadSingleServiceConfig();
    initConsoleResize();
    initAllConfigWidgets();
    // widget 已按所选账号初始化，静默同步一次配置（不弹 toast）
    refreshAllModulesData(true);
});
