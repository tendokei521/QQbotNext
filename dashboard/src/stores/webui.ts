import { defineStore } from 'pinia'
import { ref } from 'vue'
import http, { unwrap } from '@/api/http'
import { onSocketMessage } from '@/api/socket'

export interface WebuiConfig {
  logs: {
    show_raw_logs: boolean
    visible_levels: string[]
    max_lines: number
    console_height: number
  }
  single_service: Record<string, boolean>
  multi_group: {
    show_all: boolean
    groups: Record<string, { service_bot_index: number }>
  }
  experimental: {
    show_experimental: boolean
  }
}

export interface ModulePreferences {
  collapsed?: Record<string, boolean>
  [key: string]: any
}

/** 递归合并嵌套的 webui 配置：浅合并会把整个嵌套对象（如 logs）整体替换而丢字段 */
function deepMerge<T>(base: T, patch: any): T {
  if (!base || typeof base !== 'object' || Array.isArray(base) || !patch || typeof patch !== 'object' || Array.isArray(patch)) {
    return (patch === undefined ? base : patch) as T
  }
  const result: Record<string, any> = { ...(base as Record<string, any>) }
  for (const key of Object.keys(patch)) {
    const bv = (base as Record<string, any>)[key]
    const pv = patch[key]
    result[key] = bv && typeof bv === 'object' && !Array.isArray(bv) && pv && typeof pv === 'object' && !Array.isArray(pv)
      ? deepMerge(bv, pv)
      : pv
  }
  return result as T
}

/** webui 配置中心：日志设置 / 单一服务 / 多群管理 / 模块偏好 */
export const useWebuiStore = defineStore('webui', () => {
  const config = ref<WebuiConfig>({
    logs: { show_raw_logs: false, visible_levels: ['info', 'warning', 'error'], max_lines: 50, console_height: 200 },
    single_service: {},
    multi_group: { show_all: false, groups: {} },
    experimental: { show_experimental: false },
  })
  const preferences = ref<ModulePreferences>({})

  async function load() {
    try {
      const res = await http.get<WebuiConfig>('/api/webui/config')
      if (res.data) config.value = deepMerge(config.value, res.data)
    } catch {
      /* 初始默认值兜底 */
    }
    try {
      const res = await http.get<ModulePreferences>('/api/webui/module-preferences')
      if (res.data) preferences.value = res.data
    } catch {
      /* ignore */
    }
  }

  async function saveLogs(patch: Partial<WebuiConfig['logs']>) {
    const res = await http.post('/api/webui/config/logs', patch)
    return unwrap(res.data)
  }

  async function saveSingleService(single_service: Record<string, boolean>) {
    const res = await http.post('/api/webui/single-service', { single_service })
    return unwrap(res.data)
  }

  async function saveMultiGroup(multi_group: WebuiConfig['multi_group']) {
    const res = await http.post('/api/webui/multi-group', { multi_group })
    return unwrap(res.data)
  }

  async function savePreferences(prefs: ModulePreferences) {
    const res = await http.post('/api/webui/module-preferences', { module_preferences: prefs })
    return unwrap(res.data)
  }

  async function saveExperimental(patch: Partial<WebuiConfig['experimental']>) {
    const res = await http.post('/api/webui/config/experimental', patch)
    return unwrap(res.data)
  }

  // WS 实时同步
  onSocketMessage('webui_config_updated', (msg) => {
    if (msg.config) config.value = deepMerge(config.value, msg.config)
  })
  onSocketMessage('single_service_updated', (msg) => {
    if (msg.single_service) config.value.single_service = msg.single_service
  })
  onSocketMessage('multi_group_updated', (msg) => {
    if (msg.multi_group) config.value.multi_group = msg.multi_group
  })

  return {
    config,
    preferences,
    load,
    saveLogs,
    saveSingleService,
    saveMultiGroup,
    savePreferences,
    saveExperimental,
  }
})
