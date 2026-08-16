<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useLogsStore } from '@/stores/logs'
import { useWebuiStore } from '@/stores/webui'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'

const logs = useLogsStore()
const webui = useWebuiStore()
const notify = useNotifyStore()

const settingsOpen = ref(false)
const filterInput = ref('')
const bodyEl = ref<HTMLDivElement | null>(null)

const visibleRows = computed(() => logs.filtered.slice(-1000))

// 可写 computed：webui.load() 覆盖配置后仍绑定到最新值
const levels = computed<string[]>({
  get: () => webui.config.logs.visible_levels,
  set: (v) => {
    webui.config.logs.visible_levels = v
  },
})
const maxLines = computed<number>({
  get: () => webui.config.logs.max_lines,
  set: (v) => {
    webui.config.logs.max_lines = v
  },
})

function onFilterInput() {
  logs.setFilter(filterInput.value)
}

async function saveLogsSettings() {
  try {
    await webui.saveLogs({
      visible_levels: levels.value,
      max_lines: maxLines.value,
    })
    settingsOpen.value = false
    notify.push('日志设置已保存', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

// 新日志到达时：仅在用户本来就停在底部附近时跟随滚动，避免打断回看
async function scrollToBottomIfNeeded() {
  await nextTick()
  const el = bodyEl.value
  if (!el) return
  const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
  if (distanceFromBottom < 80) el.scrollTop = el.scrollHeight
}

watch(() => visibleRows.value.length, () => {
  if (!logs.paused) void scrollToBottomIfNeeded()
})

onMounted(() => {
  if (!logs.logs.length) logs.refresh()
})
</script>

<template>
  <div class="log-console">
    <div class="console-header">
      <div class="console-title">
        <v-icon icon="mdi-console" size="small" color="primary" />
        <span>控制台日志</span>
        <v-chip v-if="logs.pendingCount > 0" size="x-small" color="warning" variant="flat">
          +{{ logs.pendingCount }}
        </v-chip>
      </div>
      <div class="console-actions">
        <v-text-field
          v-model="filterInput"
          density="compact"
          variant="outlined"
          hide-details
          placeholder="过滤日志…（如 #0 / 关键字）"
          class="filter-input"
          clearable
          @update:model-value="onFilterInput"
        />
        <v-btn
          size="small"
          variant="tonal"
          :color="logs.paused ? 'warning' : undefined"
          :icon="logs.paused ? 'mdi-play' : 'mdi-pause'"
          :title="logs.paused ? '继续接收日志' : '暂停接收日志'"
          @click="logs.togglePause()"
        />
        <v-btn size="small" variant="tonal" icon="mdi-cog-outline" title="日志显示设置" @click="settingsOpen = true" />
        <v-btn size="small" variant="tonal" icon="mdi-delete-outline" title="清空日志" @click="logs.clear()" />
      </div>
    </div>

    <div ref="bodyEl" class="console-body">
      <div v-if="visibleRows.length === 0" class="empty-tip">暂无日志</div>
      <div v-for="(row, i) in visibleRows" :key="`${row.timestamp}-${i}`" class="log-row">
        <span class="log-time">{{ row.timestamp }}</span>
        <span class="log-level" :class="`log-level-${row.level}`">{{ row.level.toUpperCase() }}</span>
        <span class="log-message">{{ row.message }}</span>
      </div>
    </div>

    <v-dialog v-model="settingsOpen" max-width="460">
      <v-card>
        <v-card-title class="pa-4 pb-1">
          <v-icon icon="mdi-filter-outline" class="mr-2" color="primary" /> 日志显示设置
        </v-card-title>
        <v-card-text>
          <div class="text-subtitle-2 mb-1">显示级别</div>
          <v-checkbox
            v-for="lv in ['debug', 'info', 'warning', 'error']"
            :key="lv"
            v-model="levels"
            :label="lv"
            :value="lv"
            density="compact"
            hide-details
            color="primary"
          />
          <v-text-field
            v-model.number="maxLines"
            label="显示行数"
            type="number"
            min="10"
            max="200"
            density="compact"
            class="mt-3"
            hide-details
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="settingsOpen = false">取消</v-btn>
          <v-btn color="primary" variant="tonal" @click="saveLogsSettings">保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.log-console {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: rgb(var(--v-theme-surface));
}

.console-header {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  height: 48px;
  padding: 0 14px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.console-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
}

.console-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.filter-input {
  width: 260px;
  max-width: 40vw;
}

.console-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 10px 14px;
  font-family: 'Cascadia Code', Consolas, 'Courier New', monospace;
  font-size: 12.5px;
  line-height: 1.7;
}

.empty-tip {
  color: rgba(var(--v-theme-on-surface), 0.45);
  text-align: center;
  padding: 20px;
  font-family: inherit;
}

.log-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 1px 0;
  border-bottom: 1px dashed rgba(var(--v-theme-on-surface), 0.05);
}

.log-time {
  color: rgba(var(--v-theme-on-surface), 0.42);
  flex-shrink: 0;
}

.log-level {
  flex-shrink: 0;
  display: inline-block;
  min-width: 52px;
  text-align: center;
  padding: 0 6px;
  border-radius: 999px;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.3px;
}

.log-level-debug {
  color: rgb(var(--v-theme-info));
  background: rgba(var(--v-theme-info), 0.14);
}

.log-level-info {
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.14);
}

.log-level-warning {
  color: rgb(var(--v-theme-warning));
  background: rgba(var(--v-theme-warning), 0.16);
}

.log-level-error {
  color: rgb(var(--v-theme-error));
  background: rgba(var(--v-theme-error), 0.14);
}

.log-message {
  word-break: break-all;
  white-space: pre-wrap;
}

@media (max-width: 720px) {
  .console-header {
    flex-direction: column;
    align-items: stretch;
    height: auto;
    padding: 10px 12px;
  }

  .console-title {
    justify-content: space-between;
  }

  .console-actions {
    flex-wrap: wrap;
  }

  .filter-input {
    flex: 1 1 180px;
    max-width: none;
  }
}
</style>
