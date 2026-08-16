<script setup lang="ts">
import { onMounted } from 'vue'
import { useLogsStore } from '@/stores/logs'
import LogConsole from '@/components/LogConsole.vue'

const logs = useLogsStore()

onMounted(() => {
  if (!logs.logs.length) logs.refresh()
})
</script>

<template>
  <div class="logs-page">
    <div class="app-page-header">
      <div>
        <h1 class="app-page-title">日志</h1>
        <div class="app-page-subtitle">实时运行日志：过滤、暂停与级别设置</div>
      </div>
      <div class="d-flex gap-2">
        <v-btn variant="tonal" prepend-icon="mdi-refresh" @click="logs.refresh">刷新</v-btn>
        <v-btn variant="tonal" prepend-icon="mdi-delete-outline" @click="logs.clear">清空</v-btn>
      </div>
    </div>

    <v-card variant="outlined" class="log-card">
      <LogConsole />
    </v-card>
  </div>
</template>

<style scoped>
.logs-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 160px);
  min-height: 380px;
}

.logs-page .app-page-header {
  flex: 0 0 auto;
}

.log-card {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: 16px;
}

.log-card :deep(.log-console) {
  flex: 1 1 auto;
}
</style>
