import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import http, { errorMessage } from '@/api/http'

export interface ReadinessCheck {
  id: string
  ok: boolean
  /** blocker 决定整体可用；warn 只提示；info 仅展示 */
  level: 'blocker' | 'warn' | 'info' | string
  title: string
  detail?: string
  action?: { route?: string; label?: string } | null
}

/**
 * 就绪状态：总览卡 / 首启向导 / 启动横幅共用同一份后端判定。
 * 前端只负责展示 + 轮询，不自行推断"能不能回话"。
 */
export const useReadinessStore = defineStore('readiness', () => {
  const checks = ref<ReadinessCheck[]>([])
  const ready = ref(false)
  const setupCompleted = ref(false)
  const loading = ref(false)
  const error = ref('')
  let timer: number | null = null

  const blockers = computed(() =>
    checks.value.filter((check) => !check.ok && check.level === 'blocker'),
  )
  const warnings = computed(() =>
    checks.value.filter((check) => !check.ok && check.level === 'warn'),
  )

  async function load(): Promise<void> {
    loading.value = true
    try {
      const res = await http.get('/api/readiness')
      checks.value = res.data?.checks || []
      ready.value = Boolean(res.data?.ready)
      setupCompleted.value = Boolean(res.data?.setup_completed)
    } catch (err) {
      error.value = errorMessage(err)
    } finally {
      loading.value = false
    }
  }

  // 自适应轮询：未就绪时 5s 一次（用户正在等"差什么"），就绪后降到 30s；
  // 页面不可见（切到后台标签页）时完全暂停，回来再刷一次。
  const ACTIVE_INTERVAL_MS = 5000
  const IDLE_INTERVAL_MS = 30000
  let active = false

  function schedule(): void {
    if (!active) return
    const delay = ready.value ? IDLE_INTERVAL_MS : ACTIVE_INTERVAL_MS
    timer = window.setTimeout(async () => {
      await load()
      schedule()
    }, delay)
  }

  function onVisibilityChange(): void {
    if (document.hidden) {
      if (timer !== null) {
        window.clearTimeout(timer)
        timer = null
      }
      return
    }
    if (active) {
      void load().then(() => schedule())
    }
  }

  function startPolling(intervalMs?: number): void {
    stopPolling()
    active = true
    if (intervalMs) {
      timer = window.setTimeout(async function tick() {
        await load()
        if (active) timer = window.setTimeout(tick, intervalMs)
      }, 0)
    } else {
      void load().then(() => schedule())
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
  }

  function stopPolling(): void {
    active = false
    if (timer !== null) {
      window.clearTimeout(timer)
      timer = null
    }
    document.removeEventListener('visibilitychange', onVisibilityChange)
  }

  return {
    checks,
    ready,
    setupCompleted,
    loading,
    error,
    blockers,
    warnings,
    load,
    startPolling,
    stopPolling,
  }
})
