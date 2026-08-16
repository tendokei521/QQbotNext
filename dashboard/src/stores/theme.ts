import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

type ThemeName = 'light' | 'dark'

const STORAGE_KEY = 'qqbot_ui_theme'

/** 主题状态：明暗切换 + localStorage 持久化 */
export const useThemeStore = defineStore('theme', () => {
  const stored = localStorage.getItem(STORAGE_KEY) as ThemeName | null
  const prefersDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
  const theme = ref<ThemeName>(stored ?? (prefersDark ? 'dark' : 'light'))

  watch(theme, (v) => localStorage.setItem(STORAGE_KEY, v))

  const isDark = computed(() => theme.value === 'dark')

  function toggle() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
  }

  function set(name: ThemeName) {
    theme.value = name
  }

  return { theme, isDark, toggle, set }
})
