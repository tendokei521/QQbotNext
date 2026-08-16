import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import http, { unwrap } from '@/api/http'
import { onSocketMessage } from '@/api/socket'
import { useBotsStore } from './bots'

export interface PermissionConfig {
  group_mode: 'whitelist' | 'blacklist'
  group_list: string[]
  user_mode: 'whitelist' | 'blacklist'
  user_list: string[]
}

export interface ModuleData {
  /** 注册 key（路由参数用；后端响应是 mod_name → data 映射，key 需前端注入） */
  _key: string
  name: string
  name_sign: string
  description: string
  enabled: boolean
  permission: string
  bot_id: number | null
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
}

/** Agent 已拥有独立侧边栏入口，不再出现在“功能模块”列表中 */
const HIDDEN_MODULES = new Set(['agent'])

/** 模块数据中心：按当前账号加载 / 开关 / 配置 / 权限 / WS 同步 */
export const useModulesStore = defineStore('modules', () => {
  const modules = ref<Record<string, ModuleData>>({})
  const loading = ref(false)
  const reloading = ref(false)
  const botId = ref<number | null>(null)

  const list = computed(() =>
    Object.entries(modules.value)
      .filter(([key]) => !HIDDEN_MODULES.has(key))
      .map(([key, m]) => ({ ...m, _key: key })),
  )
  const count = computed(() => list.value.length)
  const enabledCount = computed(() => list.value.filter((m) => m.enabled).length)

  const bots = useBotsStore()

  /** 同步到当前选中账号的作用域 */
  function syncBotScope() {
    botId.value = bots.currentBot?.bot_id ?? null
  }

  async function load(): Promise<void> {
    loading.value = true
    try {
      syncBotScope()
      const res = await http.get<Record<string, ModuleData>>('/api/modules', {
        params: { bot_id: botId.value },
      })
      modules.value = res.data || {}
    } finally {
      loading.value = false
    }
  }

  async function reloadAll(): Promise<void> {
    reloading.value = true
    try {
      syncBotScope()
      const res = await http.post('/api/modules/reload', null, { params: { bot_id: botId.value } })
      unwrap(res.data)
      await load()
    } finally {
      reloading.value = false
    }
  }

  async function toggle(name: string, enabled: boolean): Promise<void> {
    syncBotScope()
    const form = new FormData()
    form.append('enabled', String(enabled))
    const res = await http.post(`/api/module/${name}/toggle`, form, {
      params: { bot_id: botId.value },
    })
    unwrap(res.data)
    const mod = modules.value[name]
    if (mod) mod.enabled = enabled
  }

  async function saveConfig(name: string, config: Record<string, any>): Promise<void> {
    syncBotScope()
    const res = await http.post(`/api/module/${name}/config`, config, {
      params: { bot_id: botId.value },
    })
    unwrap(res.data)
  }

  async function savePermission(name: string, permission: PermissionConfig): Promise<void> {
    syncBotScope()
    const form = new FormData()
    form.append('group_mode', permission.group_mode)
    form.append('group_list', permission.group_list.join('\n'))
    form.append('user_mode', permission.user_mode)
    form.append('user_list', permission.user_list.join('\n'))
    const res = await http.post(`/api/module/${name}/permission`, form, {
      params: { bot_id: botId.value },
    })
    unwrap(res.data)
  }

  /** 更新本地模块对象（WS 消息 / 本地操作回显） */
  function patch(name: string, patchData: Partial<ModuleData>) {
    const mod = modules.value[name]
    if (!mod) return
    Object.assign(mod, patchData)
  }

  // WS 同步：模块配置/权限变更（仅当前 bot 生效）
  onSocketMessage('module_config_updated', (msg) => {
    if (msg.module && (msg.bot_id === null || msg.bot_id === botId.value)) {
      const mod = modules.value[msg.module]
      if (mod && msg.config) mod.config = msg.config
    }
  })
  onSocketMessage('module_authority_updated', (msg) => {
    if (msg.module && (msg.bot_id === null || msg.bot_id === botId.value)) {
      const mod = modules.value[msg.module]
      if (!mod) return
      if (typeof msg.enabled === 'boolean') mod.enabled = msg.enabled
      if (msg.permission) mod.permission_config = msg.permission
    }
  })
  onSocketMessage('modules_reloaded', () => {
    load()
  })

  return {
    modules,
    loading,
    reloading,
    botId,
    list,
    count,
    enabledCount,
    syncBotScope,
    load,
    reloadAll,
    toggle,
    saveConfig,
    savePermission,
    patch,
  }
})
