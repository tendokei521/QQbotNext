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

  function startPolling(intervalMs = 5000): void {
    stopPolling()
    void load()
    timer = window.setInterval(() => void load(), intervalMs)
  }

  function stopPolling(): void {
    if (timer !== null) {
      window.clearInterval(timer)
      timer = null
    }
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
