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
    if (!items.length) {
      // 服务端窗口为空（日志被清空 / 过滤后无内容）：清掉已渲染行
      logs.value = []
      cache.value = items
      pendingCount.value = 0
      return
    }
    // 增量判定以「上一帧的最后一行」为锚点：
    // 服务端每秒推最近 N 行，旧行还在窗口里时锚点必然还在窗口内，锚点之后的才是真正的新行。
    // 注意不能拿新旧数组做「尾部逐位对齐」：新行是追加在窗口末尾的，一有新日志尾部就必然不同，
    // 会把整帧当「无重叠」而整帧替换，把累积的滚动历史冲掉（旧版 UI 正是这个问题）。
    const anchor = prev.length ? itemKey(prev[prev.length - 1]) : ''
    let fresh = items
    if (anchor) {
      let idx = -1
      for (let t = items.length - 1; t >= 0; t--) {
        if (itemKey(items[t]) === anchor) {
          idx = t
          break
        }
      }
      // 锚点不在窗口内 = 一秒内新增超过窗口容量（整窗翻页）/ 日志轮转：
      // 此时整窗都是尚未渲染的行，整段追加，绝不用窗口覆盖已累积的历史
      fresh = idx >= 0 ? items.slice(idx + 1) : items
    }
    if (fresh.length) {
      logs.value = [...logs.value, ...fresh].slice(-MAX_CACHE)
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
