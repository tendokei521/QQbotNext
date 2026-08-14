// ==================== 全局配置 ====================
let webuiConfig = window.WEBUI_CONFIG || {
    logs: { visible_levels: ['info', 'warning', 'error'], max_lines: 50 }
};
let botsData = window.BOTS_DATA || [];
let visibleLevels = webuiConfig.logs?.visible_levels || ['info', 'warning', 'error'];
let maxLogLines = webuiConfig.logs?.max_lines || 50;
let currentBotId = null;

// ==================== 工具函数 ====================
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
        const response = await fetch('/api/bots');
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
        const response = await fetch(`/api/bots/${botIndex}/connect`, { method: 'POST' });
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
        const response = await fetch(`/api/bots/${botIndex}/disconnect`, { method: 'POST' });
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
        const response = await fetch(`/api/bots/${botIndex}/reconnect`, { method: 'POST' });
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
            fetch('/api/bots/config'),
            fetch('/api/bots')
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
        const ownerId = card.querySelector('.bot-owner-id').value.trim();
        const autoConnect = card.querySelector('.bot-auto-connect').checked;
        bots.push({
            ws_url: wsUrl,
            owner_id: ownerId ? parseInt(ownerId) || ownerId : null,
            auto_connect: autoConnect
        });
    });

    try {
        const response = await fetch('/api/bots/config/save', {
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
        const response = await fetch('/api/bots/config/add', { method: 'POST' });
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
        const response = await fetch(`/api/bots/config/delete/${index}`, { method: 'POST' });
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
        const response = await fetch(`/api/bots/${index}/connect`, { method: 'POST' });
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
        const response = await fetch(`/api/bots/${index}/disconnect`, { method: 'POST' });
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
        const response = await fetch(`/api/bots/${index}/reconnect`, { method: 'POST' });
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
    openModal('logs-config-modal');
}

async function saveLogsConfig() {
    const levels = [];
    if (document.getElementById('log-level-debug').checked) levels.push('debug');
    if (document.getElementById('log-level-info').checked) levels.push('info');
    if (document.getElementById('log-level-warning').checked) levels.push('warning');
    if (document.getElementById('log-level-error').checked) levels.push('error');

    const maxLines = parseInt(document.getElementById('log-max-lines').value);

    if (levels.length === 0) {
        showToast('至少选择一个日志级别', 'warning');
        return;
    }

    try {
        const response = await fetch('/api/webui/config/logs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                visible_levels: levels,
                max_lines: maxLines
            })
        });
        const result = await response.json();

        if (response.ok && result.status === 'success') {
            visibleLevels = levels;
            maxLogLines = maxLines;
            webuiConfig.logs = { ...webuiConfig.logs, visible_levels: levels, max_lines: maxLines };
            showToast(result.message, 'success');
            closeModal('logs-config-modal');
            refreshLogs();
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
    document.getElementById('logs-container').innerHTML = '';
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
            setTimeout(() => {
                targetCard.querySelectorAll('textarea.auto-resize').forEach(el => autoResize(el));
                if (pluginFrame && typeof resizePluginPage === 'function') resizePluginPage(pluginFrame);
            }, 100);
        }
    });
});

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
        const response = await fetch(`/api/modules?bot_id=${botId}`);
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
                if (moduleData.permission) {
                    updatePermissionDisplay(moduleName, moduleData.permission);
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
        const response = await fetch(`/api/modules?bot_id=${botId}`);
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

        const response = await fetch(`/api/module/${moduleName}/toggle?bot_id=${botId}`, {
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

async function savePermission(moduleName) {
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

        const response = await fetch(`/api/module/${moduleName}/permission?bot_id=${botId}`, {
            method: 'POST',
            body: formData
        });
        const result = await response.json();

        showToast(result.message, response.ok && result.status === 'success' ? 'success' : 'error');
    } catch (error) {
        showToast('保存失败', 'error');
    }
}

async function saveConfig(moduleName) {
    const configContainer = document.getElementById(`config-container-${moduleName}`);
    if (!configContainer) return;

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
        const response = await fetch(`/api/module/${moduleName}/config?bot_id=${botId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });
        const result = await response.json();

        isRecentOperation(`config-${moduleName}`);
        showToast(result.message, response.ok && result.status === 'success' ? 'success' : 'error');
    } catch (error) {
        showToast('保存失败', 'error');
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
        const response = await fetch(`/api/modules/reload?bot_id=${botId}`, { method: 'POST' });
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

// ==================== 日志 WebSocket ====================
let ws = null;

function connectLogsWebSocket() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws/logs`);

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
            showToast('模块已重新加载', 'info');
            refreshAllModulesData();
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
    }
}

function handleWebuiConfigUpdate(config) {
    webuiConfig = config;
    visibleLevels = config.logs?.visible_levels || ['info', 'warning', 'error'];
    maxLogLines = config.logs?.max_lines || 50;
    updateLogLevelCheckboxes();
    showToast('WebUI 配置已更新', 'info');
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
    console.log(`更新权限显示: ${moduleName}`, permission);
    
    // 使用 ID 选择器（推荐）
    const groupModeSelect = document.getElementById(`group-mode-${moduleName}`);
    if (groupModeSelect) {
        groupModeSelect.value = permission.group_mode;
        console.log(`已更新群组模式: ${permission.group_mode}`);
    } else {
        console.warn(`找不到群组模式元素: group-mode-${moduleName}`);
    }
    
    const groupListInput = document.getElementById(`group-list-${moduleName}`);
    if (groupListInput) {
        groupListInput.value = permission.group_list.join('\n');
        console.log(`已更新群组列表: ${permission.group_list.length} 个群`);
    } else {
        console.warn(`找不到群组列表元素: group-list-${moduleName}`);
    }
    
    const userModeSelect = document.getElementById(`user-mode-${moduleName}`);
    if (userModeSelect) {
        userModeSelect.value = permission.user_mode;
        console.log(`已更新用户模式: ${permission.user_mode}`);
    } else {
        console.warn(`找不到用户模式元素: user-mode-${moduleName}`);
    }
    
    const userListInput = document.getElementById(`user-list-${moduleName}`);
    if (userListInput) {
        userListInput.value = permission.user_list.join('\n');
        console.log(`已更新用户列表: ${permission.user_list.length} 个用户`);
    } else {
        console.warn(`找不到用户列表元素: user-list-${moduleName}`);
    }
}

async function refreshModulePermission(moduleName) {
    const botId = getCurrentBotId();
    try {
        const url = botId ? `/api/modules/${moduleName}?bot_id=${botId}` : `/api/modules/${moduleName}`;
        const response = await fetch(url);
        if (response.ok) {
            const modules = await response.json();
            if (modules[moduleName]) {
                updatePermissionDisplay(moduleName, modules[moduleName].permission);
            }
        }
    } catch (e) {
        console.error('刷新权限失败:', e);
    }
}

function updateLogLevelCheckboxes() {
    const checkboxes = document.querySelectorAll('#logs-config-form input[type="checkbox"]');
    checkboxes.forEach(cb => {
        cb.checked = visibleLevels.includes(cb.value);
    });
}

function updateLogsDisplay(logs) {
    const logsContainer = document.getElementById('logs-container');
    const isBottom = logsContainer.scrollHeight - logsContainer.scrollTop - logsContainer.clientHeight < 50;

    logsContainer.innerHTML = '';
    logs.forEach(log => {
        const div = document.createElement('div');
        div.className = 'log-item';
        const time = document.createElement('span');
        time.className = 'log-time';
        time.textContent = log.timestamp;          // textContent 防 XSS
        const level = document.createElement('span');
        level.className = 'log-level ' + (log.level || '');
        level.textContent = String(log.level || '').toUpperCase();
        const msg = document.createElement('span');
        msg.className = 'log-message';
        msg.textContent = log.message;             // 用户消息原文经日志进入前端，必须 textContent
        div.appendChild(time);
        div.appendChild(level);
        div.appendChild(msg);
        logsContainer.appendChild(div);
    });

    if (isBottom) logsContainer.scrollTop = logsContainer.scrollHeight;
}

async function refreshLogs() {
    try {
        const response = await fetch('/api/logs');
        if (response.ok) {
            const logs = await response.json();
            updateLogsDisplay(logs);
        }
    } catch (e) {}
}

async function loadWebuiConfig() {
    try {
        const response = await fetch('/api/webui/config');
        if (response.ok) {
            webuiConfig = await response.json();
            visibleLevels = webuiConfig.logs?.visible_levels || ['info', 'warning', 'error'];
            maxLogLines = webuiConfig.logs?.max_lines || 50;
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
        const response = await fetch('/api/webui/single-service');
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
        const response = await fetch('/api/webui/single-service', {
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

    fetch('/api/webui/multi-group').then(r => r.json()).then(data => {
        const multiGroup = data.multi_group || { groups: {} };
        const groupsConfig = multiGroup.groups || {};
        let isServiceAccount = false;

        for (const [groupId, gConfig] of Object.entries(groupsConfig)) {
            const serviceBotIndex = gConfig.service_bot_index;
            if (serviceBotIndex !== undefined && serviceBotIndex !== null) {
                const bot = botsData.find(b => b.index === serviceBotIndex);
                if (bot && bot.bot_id == currentBotId) {
                    isServiceAccount = true;
                    break;
                }
            }
        }

        if (!isServiceAccount) {
            warningEl.style.display = 'flex';
        } else {
            warningEl.style.display = 'none';
        }
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
            fetch('/api/webui/multi-group'),
            fetch('/api/bots/groups')
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
        const response = await fetch('/api/webui/multi-group', {
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
