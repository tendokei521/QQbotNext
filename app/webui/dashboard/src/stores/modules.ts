import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import http, { errorMessage, unwrap } from '@/api/http'
import { onSocketMessage } from '@/api/socket'
import { useBotsStore } from './bots'
import { useNotifyStore } from './notify'

export interface PermissionConfig {
  group_mode: 'whitelist' | 'blacklist'
  group_list: string[]
  user_mode: 'whitelist' | 'blacklist'
  user_list: string[]
}

/** 模块安装来源：哪个预设装的（后端 module_origins 台账；本地手装的模块没有这个字段） */
export interface ModuleOrigin {
  bundle_id: string
  bundle_name: string
  bundle_version: string
  account_id: string
  action: 'installed' | 'upgraded' | string
  installed_at: number
}

export interface ModuleData {
  /** 注册 key（路由参数用；后端响应是 mod_name → data 映射，key 需前端注入） */
  _key: string
  name: string
  name_sign: string
  description: string
  enabled: boolean
  permission: string
  account_id: string | null
  category: string
  tags: string[]
  order: number
  hidden: boolean
  pinned: boolean
  permission_config: PermissionConfig
  config: Record<string, any>
  config_schema: {
    groups?: Record<string, any>
    items?: Record<string, any>
  }
  has_page: boolean
  /** 来源：builtin=内置（local 为旧后端兼容值），installed=第三方 zip（zip 为旧值） */
  source: 'builtin' | 'installed' | 'local' | 'zip'
  version: string
  can_uninstall: boolean
  provides?: string[]
  supersedes?: string[]
  /** 是否声明支持单一服务模式（声明制能力：未声明则无开关、后端拒收） */
  single_service?: boolean
  /** 编目状态：ok / error（装坏）/ conflict / uninstalled（软卸载）/ manifest_missing */
  status?: 'ok' | 'error' | 'conflict' | 'uninstalled' | 'manifest_missing' | string
  /** status=error 时的可读原因 */
  error?: string
  /** 模块根目录（排查用） */
  path?: string
  /** 是否内置模块（决定菜单与删除权限） */
  builtin?: boolean
  /** 内置模块缺 module.json（仍可加载，但版本/显示名是兜底值） */
  manifest_missing?: boolean
  installed_at?: number | null
  /** 安装来源（预设中心安装的模块才有） */
  origin?: ModuleOrigin
}

/** validate-zip 的预检结果：确认安装前给用户看"装到哪、是否覆盖" */
export interface InstallPreview {
  name: string
  display_name: string
  version: string
  source: 'installed' | string
  target: string
  /** 目标已有同名第三方插件（覆盖安装） */
  conflict: boolean
  /** 与内置模块同名（禁止覆盖，必须改名） */
  builtin_conflict?: boolean
  conflict_reason?: string
  existing_version?: string | null
  files?: number
  /** 与内置模块同名等致命冲突时后端给的提示 */
  hint?: string
}

/** install-zip / update 回执：含「安装时自动装依赖」的结果 */
export interface ZipInstallInfo {
  module_name?: string
  version?: string
  loaded?: boolean
  deps_missing?: boolean
  deps?: {
    ok?: boolean
    installed?: string[]
    missing?: string[]
    error?: string
    hint?: string
    stderr?: string
    command?: string[]
  }
}

/** 模块是否内置：优先编目字段，旧后端回退 source=local */
export function isBuiltinModule(mod: Pick<ModuleData, 'builtin' | 'source'>): boolean {
  if (typeof mod.builtin === 'boolean') return mod.builtin
  return mod.source === 'builtin' || mod.source === 'local'
}


export interface UninstalledModuleData {
  module_name: string
  source: 'builtin' | 'installed' | 'local' | 'zip'
  display_name: string
  version: string
  uninstalled_at: number
}

/** Agent 已拥有独立侧边栏入口，不再出现在“功能模块”列表中 */
const HIDDEN_MODULES = new Set(['agent'])

/** 模块数据中心：按当前账号加载 / 开关 / 配置 / 权限 / WS 同步 */
export const useModulesStore = defineStore('modules', () => {
  const modules = ref<Record<string, ModuleData>>({})
  const loading = ref(false)
  const reloading = ref(false)
  const botId = ref<string | null>(null)
  const uninstalledModules = ref<UninstalledModuleData[]>([])

  const list = computed(() =>
    Object.entries(modules.value)
      .filter(([key]) => !HIDDEN_MODULES.has(key))
      .map(([key, m]) => ({ ...m, _key: key })),
  )
  const count = computed(() => list.value.length)
  const enabledCount = computed(() => list.value.filter((m) => m.enabled).length)

  const bots = useBotsStore()

  // 请求序号：丢弃过期响应，避免快速切换账号时旧响覆盖新的模块数据
  let loadSeq = 0

  /** 同步到当前选中账号的作用域 */
  function syncBotScope() {
    botId.value = bots.currentAccountId
  }

  async function load(): Promise<void> {
    const seq = ++loadSeq
    loading.value = true
    try {
      syncBotScope()
      const res = await http.get<Record<string, ModuleData>>('/api/modules', {
        params: { account_id: botId.value },
      })
      if (seq !== loadSeq) return
      modules.value = res.data || {}
    } finally {
      if (seq === loadSeq) loading.value = false
    }
  }

  async function reloadAll(): Promise<void> {
    reloading.value = true
    try {
      syncBotScope()
      const res = await http.post('/api/modules/reload', null, { params: { account_id: botId.value } })
      unwrap(res.data)
      await load()
    } finally {
      reloading.value = false
    }
  }

  async function loadUninstalled(): Promise<void> {
    const res = await http.get<{ ok: boolean; modules: UninstalledModuleData[] }>('/api/modules/uninstalled')
    uninstalledModules.value = res.data?.modules || []
  }

  /** 安装 zip 插件：autoDeps=true 时后端会在加载前用 pip 装声明依赖（需联网） */
  async function installZip(
    file: File,
    force = false,
    autoDeps = false,
    mirror = '',
  ): Promise<ZipInstallInfo | null> {
    const form = new FormData()
    form.append('file', file)
    form.append('force', String(force))
    form.append('auto_deps', String(autoDeps))
    if (mirror.trim()) form.append('mirror', mirror.trim())
    const res = await http.post<{ plugin?: ZipInstallInfo }>('/api/modules/install-zip', form)
    unwrap(res.data)
    // 安装后必须 reloadAll：后端为所有账号补实例，前端同步刷新当前账号
    await Promise.all([reloadAll(), loadUninstalled()])
    return res.data?.plugin || null
  }

  /** dry-run 预检：上传前先知道会装成什么名字/版本、是否覆盖 */
  async function validateZip(file: File): Promise<InstallPreview> {
    const form = new FormData()
    form.append('file', file)
    const res = await http.post<{ ok: boolean; plugin: InstallPreview; hint?: string }>(
      '/api/modules/validate-zip',
      form,
    )
    unwrap(res.data)
    return res.data.plugin
  }

  /** 覆盖安装（更新）指定的第三方模块，配置与数据保留；autoDeps 同安装 */
  async function updateZip(
    name: string,
    file: File,
    autoDeps = false,
    mirror = '',
  ): Promise<ZipInstallInfo | null> {
    const form = new FormData()
    form.append('file', file)
    form.append('auto_deps', String(autoDeps))
    if (mirror.trim()) form.append('mirror', mirror.trim())
    const res = await http.post<{ plugin?: ZipInstallInfo }>(`/api/modules/${name}/update`, form)
    unwrap(res.data)
    await Promise.all([reloadAll(), loadUninstalled()])
    return res.data?.plugin || null
  }

  /** 硬删除第三方模块；purgeData=true 才清理运行时数据目录 */
  async function deleteModule(name: string, purgeData = false): Promise<void> {
    syncBotScope()
    const res = await http.delete(`/api/modules/${name}`, {
      params: { purge_data: purgeData, account_id: botId.value },
    })
    unwrap(res.data)
    await Promise.all([reloadAll(), loadUninstalled()])
  }

  async function uninstall(name: string): Promise<void> {
    syncBotScope()
    const res = await http.post(`/api/modules/${name}/uninstall`, null, {
      params: { account_id: botId.value },
    })
    unwrap(res.data)
    await Promise.all([reloadAll(), loadUninstalled()])
  }

  async function install(name: string): Promise<void> {
    syncBotScope()
    const res = await http.post(`/api/modules/${name}/install`, null, {
      params: { account_id: botId.value },
    })
    unwrap(res.data)
    await Promise.all([reloadAll(), loadUninstalled()])
  }

  async function reloadSingle(name: string): Promise<void> {
    syncBotScope()
    const res = await http.post(`/api/modules/${name}/reload`, null, {
      params: { account_id: botId.value },
    })
    unwrap(res.data)
    await load()
  }

  async function toggle(name: string, enabled: boolean): Promise<void> {
    syncBotScope()
    const form = new FormData()
    form.append('enabled', String(enabled))
    const res = await http.post(`/api/module/${name}/toggle`, form, {
      params: { account_id: botId.value },
    })
    unwrap(res.data)
    const mod = modules.value[name]
    if (mod) mod.enabled = enabled
  }

  async function saveConfig(
    name: string,
    config: Record<string, any>,
    accountId?: string | null,
  ): Promise<void> {
    // accountId 传 undefined = 保存到当前账号；显式传值用于「切号前把旧账号的草稿写回旧账号」，
    // 否则 currentAccountId 已经变成新账号，会把旧草稿写进新账号（串台）。
    if (accountId === undefined) syncBotScope()
    const aid = accountId === undefined ? botId.value : accountId
    const res = await http.post(`/api/module/${name}/config`, config, {
      params: { account_id: aid },
    })
    unwrap(res.data)
  }

  async function savePermission(
    name: string,
    permission: PermissionConfig,
    accountId?: string | null,
  ): Promise<void> {
    if (accountId === undefined) syncBotScope()
    const aid = accountId === undefined ? botId.value : accountId
    const form = new FormData()
    form.append('group_mode', permission.group_mode)
    form.append('group_list', permission.group_list.join('\n'))
    form.append('user_mode', permission.user_mode)
    form.append('user_list', permission.user_list.join('\n'))
    const res = await http.post(`/api/module/${name}/permission`, form, {
      params: { account_id: aid },
    })
    unwrap(res.data)
  }

  /** 更新本地模块对象（WS 消息 / 本地操作回显） */
  function patch(name: string, patchData: Partial<ModuleData>) {
    const mod = modules.value[name]
    if (!mod) return
    Object.assign(mod, patchData)
  }

  /**
   * WS 事件是否属于当前账号。
   *
   * 🔗1 修复：后端历史字段是 `bot_id`（现在同时下发 `account_id`），此前前端只判
   * `msg.account_id`，条件恒真 → A 账号的事件会覆盖 B 账号的 UI，autosave 再把 A 的
   * 配置写进 B（跨账号串台）。这里同时兼容两个字段，且统一转字符串比较。
   */
  function isCurrentAccountEvent(msg: { account_id?: unknown; bot_id?: unknown }): boolean {
    const target = msg.account_id ?? msg.bot_id
    if (target === null || target === undefined || target === '') return true
    return String(target) === String(botId.value)
  }

  // WS 同步：模块配置/权限变更（仅当前账号生效）
  onSocketMessage('module_config_updated', (msg) => {
    if (msg.module && isCurrentAccountEvent(msg)) {
      const mod = modules.value[msg.module]
      if (mod && msg.config) mod.config = msg.config
    }
  })
  onSocketMessage('module_authority_updated', (msg) => {
    if (msg.module && isCurrentAccountEvent(msg)) {
      const mod = modules.value[msg.module]
      if (!mod) return
      if (typeof msg.enabled === 'boolean') mod.enabled = msg.enabled
      if (msg.permission) mod.permission_config = msg.permission
    }
  })
  onSocketMessage('modules_reloaded', () => {
    load()
    loadUninstalled()
  })

  // WS 重连/首次连接成功后重拉模块数据，弥补断线期间的更新
  onSocketMessage('socket_open', () => {
    // 回调里没人 await：失败必须自己收口，否则是 unhandled rejection
    load().catch((err) => {
      useNotifyStore().push(`模块数据刷新失败：${errorMessage(err)}`, 'warning')
    })
  })

  // 切账号：模块列表按账号加载。从账号管理新增账号、首启向导 selectBot 等路径不会经过
  // DefaultLayout 的切号刷新，旧 modules 会残留 → 「功能插件」页展示上一个账号的开关。
  // 与 policy/bundles/responder 同模式：清空旧数据并按新账号重拉。
  watch(
    () => bots.currentAccountId,
    () => {
      // 已同步到同一个账号 = 别处（DefaultLayout）已经发起过刷新，不重复请求
      if (botId.value === bots.currentAccountId) return
      modules.value = {}
      load().catch((err) => {
        useNotifyStore().push(`模块列表刷新失败：${errorMessage(err)}`, 'warning')
      })
    },
  )

  return {
    modules,
    loading,
    reloading,
    botId,
    uninstalledModules,
    list,
    count,
    enabledCount,
    syncBotScope,
    load,
    loadUninstalled,
    reloadAll,
    reloadSingle,
    installZip,
    validateZip,
    updateZip,
    deleteModule,
    uninstall,
    install,
    toggle,
    saveConfig,
    savePermission,
    patch,
  }
})
