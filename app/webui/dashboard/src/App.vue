<script setup lang="ts">
import { defineComponent, h, onBeforeUnmount, onErrorCaptured, onMounted, ref, watch } from 'vue'
import { useTheme } from 'vuetify'
import { useThemeStore } from '@/stores/theme'
import { useNotifyStore } from '@/stores/notify'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { registerDraftFlush } from '@/api/http'

const theme = useTheme()
const themeStore = useThemeStore()
const notify = useNotifyStore()
const agent = useAgentConfigStore()

/**
 * 页面级错误边界：子页面渲染/生命周期抛异常时保住应用外壳，给「重试本页」而不是整页白屏。
 * 捕获后不再上报全局错误处理，避免用户收到两条重复提示。
 */
const ErrorBoundary = defineComponent({
  name: 'AppErrorBoundary',
  setup(_, { slots }) {
    const failed = ref(false)
    onErrorCaptured((err) => {
      console.error('[ui] 页面渲染异常', err)
      failed.value = true
      return false
    })
    return () =>
      failed.value
        ? h(
            'div',
            { style: 'padding: 56px 16px; text-align: center;' },
            [
              h('div', { style: 'font-size: 16px; font-weight: 600; margin-bottom: 8px;' }, '页面出错了'),
              h('div', { style: 'opacity: .7; margin-bottom: 16px;' }, '当前页面渲染中断，其余功能不受影响。'),
              h(
                'button',
                {
                  type: 'button',
                  onClick: () => {
                    failed.value = false
                  },
                  style:
                    'padding: 6px 18px; border-radius: 999px; border: 1px solid rgba(0,0,0,.2); background: transparent; cursor: pointer;',
                },
                '重试本页',
              ),
            ],
          )
        : slots.default?.()
  },
})

// 同步 Vuetify 主题（含切换）
watch(
  () => themeStore.theme,
  (v) => {
    theme.global.name.value = v
  },
  { immediate: true },
)

// 会话过期会整页跳登录：跳之前把 Agent 配置的自动保存草稿先落盘，避免 2s 防抖窗口内的编辑丢失
let unregisterDraftFlush: (() => void) | null = null
onMounted(() => {
  unregisterDraftFlush = registerDraftFlush(() => agent.flush())
})
onBeforeUnmount(() => {
  unregisterDraftFlush?.()
  unregisterDraftFlush = null
})
</script>

<template>
  <v-app>
    <ErrorBoundary>
      <router-view />
    </ErrorBoundary>
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
