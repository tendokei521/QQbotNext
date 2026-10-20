<script setup lang="ts">
import { watch } from 'vue'
import { useTheme } from 'vuetify'
import { useThemeStore } from '@/stores/theme'
import { useNotifyStore } from '@/stores/notify'

const theme = useTheme()
const themeStore = useThemeStore()
const notify = useNotifyStore()

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
    <v-snackbar
      v-model="notify.visible"
      :color="notify.current?.kind ?? 'info'"
      location="top"
      timeout="3000"
      rounded="pill"
    >
      {{ notify.current?.text }}
    </v-snackbar>
  </v-app>
</template>
