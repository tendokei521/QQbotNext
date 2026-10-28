import { defineStore } from 'pinia'
import { ref } from 'vue'

export type NotifyKind = 'success' | 'error' | 'info' | 'warning'

interface NotifyItem {
  id: number
  kind: NotifyKind
  text: string
  /** 本条停留时长（毫秒） */
  duration: number
}

let seq = 0

/** 两条通知之间的间隔：等上一条退场动画结束，避免文字直接跳变 */
const GAP_MS = 180

/**
 * 全局通知（Snackbar/Toast）。
 *
 * 以前每次 push 都把 current 换成新通知，但旧通知的定时器仍会到点关掉 visible，
 * 于是「连发两条时第一条的定时器把第二条提前关了」，第二条只显示一半时长。
 * 现在改为队列：每条通知显示时各自独立计时，关掉一条再取下一条。
 */
export const useNotifyStore = defineStore('notify', () => {
  const queue = ref<NotifyItem[]>([])
  const current = ref<NotifyItem | null>(null)
  const visible = ref(false)
  let timer: number | null = null
  let gapTimer: number | null = null

  function clearTimers() {
    if (timer !== null) {
      window.clearTimeout(timer)
      timer = null
    }
    if (gapTimer !== null) {
      window.clearTimeout(gapTimer)
      gapTimer = null
    }
  }

  function showNext() {
    if (visible.value || !queue.value.length) return
    const item = queue.value.shift()!
    current.value = item
    visible.value = true
    if (timer !== null) window.clearTimeout(timer)
    timer = window.setTimeout(() => dismiss(), item.duration)
  }

  /** 关闭当前通知；队列里还有就按间隔显示下一条。 */
  function dismiss() {
    clearTimers()
    if (!visible.value) return
    visible.value = false
    current.value = null
    if (queue.value.length) {
      gapTimer = window.setTimeout(() => {
        gapTimer = null
        showNext()
      }, GAP_MS)
    }
  }

  function push(text: string, kind: NotifyKind = 'info', duration = 3000) {
    const message = String(text ?? '').trim()
    if (!message) return
    queue.value.push({ id: ++seq, kind, text: message, duration })
    // 当前没有通知在显示：立即上屏；否则排队，等当前这条自然结束
    if (!visible.value) showNext()
  }

  return { queue, current, visible, push, dismiss }
})
