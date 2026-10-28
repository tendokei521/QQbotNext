<script setup lang="ts">
import { onBeforeUnmount, onMounted, watch } from 'vue'
import { useTheme } from 'vuetify'
import { useThemeStore } from '@/stores/theme'
import { useNotifyStore } from '@/stores/notify'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { registerDraftFlush } from '@/api/http'

const theme = useTheme()
const themeStore = useThemeStore()
const notify = useNotifyStore()
const agent = useAgentConfigStore()

// 会话过期会整页跳登录：跳之前把 Agent 配置的自动保存草稿先落盘，避免 2s 防抖窗口内的编辑丢失
let unregisterDraftFlush: (() => void) | null = null
onMounted(() => {
  unregisterDraftFlush = registerDraftFlush(() => agent.flush())
})
onBeforeUnmount(() => {
  unregisterDraftFlush?.()
  unregisterDraftFlush = null
})

// 同步 Vuetify 主题（含切换）
watch(
  () => themeStore.theme,
  (v) => {
    theme.global.name.value = v
  },
  { immediate: true },
)
</script>

<template>
  <v-app>
    <router-view />
    <!--
      通知由 notify store 的队列驱动：每条独立计时，关掉一条再显示下一条。
      timeout="-1" 表示关闭时机完全交给 store，避免 Vuetify 自己的定时器与队列打架。
    -->
    <v-snackbar
      :model-value="notify.visible"
      :color="notify.current?.kind ?? 'info'"
      location="top"
      :timeout="-1"
      rounded="pill"
      @update:model-value="(v: boolean) => { if (!v) notify.dismiss() }"
    >
      {{ notify.current?.text }}
      <template #actions>
        <v-btn variant="text" size="small" @click="notify.dismiss()">关闭</v-btn>
      </template>
    </v-snackbar>
  </v-app>
</template>
