import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import http from '@/api/http'
import { onLogSnapshot, onSocketMessage, setLogMode } from '@/api/socket'
import { useWebuiStore } from './webui'

export interface LogItem {
  timestamp: string
  level: string
  message: string
}

const MAX_CACHE = 1000

function itemKey(item: LogItem): string {
  return `${item.timestamp}|${item.level}|${item.message}`
}

function currentMode(): 'simple' | 'raw' {
  return useWebuiStore().config.logs.show_raw_logs ? 'raw' : 'simple'
}

/** 日志控制台状态：WS 快照增量渲染 / 过滤 / 暂停 / 清空 */
export const useLogsStore = defineStore('logs', () => {
  // cache = 服务端最近快照（不断更新）；logs = 已渲染行（增量追加）
  const cache = ref<LogItem[]>([])
  const logs = ref<LogItem[]>([])
  const paused = ref(false)
  const filterText = ref('')
  const pendingCount = ref(0)

  const filtered = computed(() => {
    const kw = filterText.value.trim().toLowerCase()
    let rows = logs.value
    if (kw) {
      rows = rows.filter(
        (l) =>
          l.message.toLowerCase().includes(kw) ||
          l.level.toLowerCase().includes(kw) ||
          l.timestamp.includes(kw),
      )
    }
    return rows
  })

  function applySnapshot(items: LogItem[]) {
    if (paused.value) {
      // 暂停时只维护 cache 与待处理计数
      cache.value = items
      pendingCount.value = Math.max(0, items.length - logs.value.length)
      return
    }
    const prev = cache.value
    const prevKeys = new Set(prev.map(itemKey))
    // 从尾部对齐：跳过与旧快照重复的尾部行，仅追加新增
    let i = items.length - 1
    while (i >= 0 && prevKeys.has(itemKey(items[i]))) i--
    if (i < 0) {
      // 与上一帧无任何尾部重叠（如大 burst 冲满窗口 / 服务端缓冲重置）：
      // 整帧替换而不是丢弃，避免一批新日志静默消失
      logs.value = items
    } else {
      const fresh = items.slice(0, i + 1)
      if (fresh.length) {
        logs.value = [...logs.value, ...fresh].slice(-MAX_CACHE)
      }
    }
    cache.value = items
    pendingCount.value = 0
  }

  function togglePause() {
    paused.value = !paused.value
    if (!paused.value) {
      // 恢复时以最新快照为准全量重绘
      logs.value = [...cache.value]
      pendingCount.value = 0
    }
  }

  function setFilter(text: string) {
    filterText.value = text
  }

  function clear() {
    logs.value = []
    cache.value = []
    pendingCount.value = 0
  }

  /** 重新拉取全量日志（修改级别/模式后调用） */
  async function refresh() {
    const mode = currentMode()
    setLogMode(mode)
    try {
      const res = await http.get<LogItem[]>('/api/logs', { params: { mode } })
      cache.value = res.data || []
      // 暂停期间不覆盖已冻结的视图，恢复时以上面最新 cache 全量重绘
      if (!paused.value) logs.value = [...cache.value]
    } catch {
      /* 网络异常时等待下一帧 WS 快照即可 */
    }
  }

  // WS 每秒快照
  onLogSnapshot((items: LogItem[]) => applySnapshot(items))

  // 日志配置变更 → 按新级别/行数重拉
  onSocketMessage('webui_config_updated', () => {
    refresh()
  })

  return {
    cache,
    logs,
    paused,
    filterText,
    pendingCount,
    filtered,
    applySnapshot,
    togglePause,
    setFilter,
    clear,
    refresh,
  }
})
