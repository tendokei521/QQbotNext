import { defineStore } from 'pinia'
import { ref } from 'vue'

export type NotifyKind = 'success' | 'error' | 'info' | 'warning'

interface NotifyItem {
  id: number
  kind: NotifyKind
  text: string
}

let seq = 0

/** 全局通知（Snackbar/Toast） */
export const useNotifyStore = defineStore('notify', () => {
  const items = ref<NotifyItem[]>([])
  const visible = ref(false)
  const current = ref<NotifyItem | null>(null)

  function push(text: string, kind: NotifyKind = 'info', duration = 3000) {
    const item: NotifyItem = { id: ++seq, kind, text }
    items.value.push(item)
    current.value = item
    visible.value = true
    setTimeout(() => {
      visible.value = false
      const idx = items.value.findIndex((i) => i.id === item.id)
      if (idx >= 0) items.value.splice(idx, 1)
    }, duration)
  }

  return { items, visible, current, push }
})
