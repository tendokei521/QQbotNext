<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useBotsStore, type BotStatus } from '@/stores/bots'
import { useModulesStore } from '@/stores/modules'
import { useLogsStore } from '@/stores/logs'
import { useWebuiStore } from '@/stores/webui'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import EmptyState from '@/components/EmptyState.vue'

const bots = useBotsStore()
const modules = useModulesStore()
const logs = useLogsStore()
const webui = useWebuiStore()
const notify = useNotifyStore()
const router = useRouter()

const refreshing = ref(false)

const STATUS_TEXT: Record<BotStatus, string> = {
  connected: '已连接',
  disconnected: '离线',
  connecting: '连接中…',
  reconnecting: '重连中…',
  error: '错误',
}

const statusColor = (s: BotStatus) =>
  s === 'connected' ? 'success' : s === 'connecting' || s === 'reconnecting' ? 'warning' : 'error'

function isSimpleLog(row: { level: string; message: string }) {
  if (webui.config.logs.show_raw_logs) return true
  const msg = String(row.message || '')
  const level = String(row.level || '').toLowerCase()
  const isApiError = ['warning', 'error'].includes(level)
  const isUserApiLog = msg.includes('[发送->]') || msg.includes('[请求->]')
  if (msg.startsWith('[API]') && !isUserApiLog && !isApiError) return false
  if (!isApiError && (msg.includes('API(->)') || msg.includes('API(<-)'))) return false
  return true
}

const previewLogs = computed(() => logs.filtered.filter(isSimpleLog).slice(-12).reverse())

const pinnedModules = computed(() =>
  modules.list
    .filter((m) => m.pinned || m.enabled)
    .sort((a, b) => a.order - b.order)
    .slice(0, 8),
)

const statCards = computed(() => [
  { label: 'Bot 账号', value: bots.botCount, icon: 'mdi-robot-outline', tile: 'primary' },
  { label: '已连接', value: bots.connectedCount, icon: 'mdi-power-plug', tile: 'success' },
  { label: '功能模块', value: modules.count, icon: 'mdi-cube-outline', tile: 'info' },
  { label: '已启用模块', value: modules.enabledCount, icon: 'mdi-toggle-switch', tile: 'warning' },
])

const isLoading = computed(() => bots.loading || modules.loading || refreshing.value)

async function refreshAll() {
  refreshing.value = true
  try {
    await Promise.all([bots.fetchBots(), modules.load()])
    notify.push('状态已刷新', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    refreshing.value = false
  }
}

async function connectCurrent() {
  if (bots.currentIndex === null) {
    notify.push('请先选择账号', 'warning')
    return
  }
  try {
    await bots.connect(bots.currentIndex)
    notify.push('已发送连接请求', 'success')
    await bots.fetchBots()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

onMounted(() => {
  if (!modules.list.length) modules.load()
  if (!logs.logs.length) logs.refresh()
})
</script>

<template>
  <div>
    <div class="app-page-header">
      <div>
        <h1 class="app-page-title">总览</h1>
        <div class="app-page-subtitle">QQBot Next 运行状态一览</div>
      </div>
      <v-btn variant="tonal" prepend-icon="mdi-refresh" :loading="refreshing" @click="refreshAll">刷新状态</v-btn>
    </div>

    <!-- 统计卡 -->
    <v-row class="mt-1">
      <v-col v-for="card in statCards" :key="card.label" cols="12" sm="6" lg="3">
        <v-card variant="outlined" class="stat-card app-card-hover">
          <v-card-text class="d-flex flex-column pa-5" style="min-height: 128px">
            <span class="app-icon-tile" :class="`app-icon-tile--${card.tile}`">
              <v-icon :icon="card.icon" size="22" />
            </span>
            <div class="stat-label">{{ card.label }}</div>
            <div class="stat-value">{{ card.value }}</div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 骨架屏（首载） -->
    <v-row v-if="isLoading" class="mt-1">
      <v-col cols="12" lg="5">
        <v-skeleton-loader type="list-item-two-line, list-item-two-line, list-item-two-line, actions" class="rounded-xl" />
      </v-col>
      <v-col cols="12" lg="7">
        <v-skeleton-loader type="card-heading, list-item-three-line, actions" class="rounded-xl" />
      </v-col>
    </v-row>

    <template v-else>
      <v-row class="mt-1">
        <v-col cols="12" lg="5">
          <v-card variant="outlined" class="app-card-fill">
            <v-card-title>
              <v-icon icon="mdi-robot" class="mr-2" color="primary" /> 账号状态
            </v-card-title>
            <v-card-text>
              <EmptyState
                v-if="bots.bots.length === 0"
                icon="mdi-robot-off-outline"
                title="暂无账号配置"
                description="请到「账号管理」添加 WebSocket 连接"
              >
                <v-btn size="small" color="primary" variant="tonal" to="/bots">去添加账号</v-btn>
              </EmptyState>
              <v-list v-else density="compact" class="bg-transparent">
                <v-list-item v-for="b in bots.bots" :key="b.index" class="bot-row">
                  <template #prepend>
                    <v-icon :icon="b.status === 'connected' ? 'mdi-check-circle' : 'mdi-alert-circle'" :color="statusColor(b.status)" size="small" />
                  </template>
                  <v-list-item-title>
                    {{ b.bot_id ? `Bot ${b.bot_id}` : `Bot #${b.index}` }}
                  </v-list-item-title>
                  <v-list-item-subtitle>
                    {{ STATUS_TEXT[b.status] }}{{ b.login_info?.nickname ? ` · ${b.login_info.nickname}` : '' }}{{ b.last_error ? ` · ${b.last_error}` : '' }}
                  </v-list-item-subtitle>
                  <template #append>
                    <v-chip size="x-small" :color="statusColor(b.status)" variant="tonal">{{ STATUS_TEXT[b.status] }}</v-chip>
                  </template>
                </v-list-item>
              </v-list>
            </v-card-text>
            <v-card-actions>
              <v-btn v-if="bots.currentIndex !== null && bots.currentBot?.status !== 'connected'" color="primary" variant="tonal" prepend-icon="mdi-plug" size="small" @click="connectCurrent">
                连接当前账号
              </v-btn>
              <v-btn variant="text" size="small" to="/bots">管理账号 →</v-btn>
            </v-card-actions>
          </v-card>
        </v-col>

        <v-col cols="12" lg="7">
          <v-card variant="outlined" class="app-card-fill">
            <v-card-title>
              <v-icon icon="mdi-cube-outline" class="mr-2" color="primary" /> 常用模块
              <v-spacer />
              <v-btn variant="text" size="small" to="/modules">全部模块 →</v-btn>
            </v-card-title>
            <v-card-text>
              <EmptyState
                v-if="pinnedModules.length === 0"
                icon="mdi-cube-off-outline"
                title="暂无模块数据"
                description="连接 Bot 后模块会自动装配"
              >
                <v-btn size="small" variant="tonal" to="/modules">查看模块</v-btn>
              </EmptyState>
              <div v-else class="module-grid">
                <v-btn
                  v-for="m in pinnedModules"
                  :key="m._key"
                  variant="outlined"
                  class="module-chip"
                  :color="m.enabled ? 'primary' : undefined"
                  @click="router.push(`/modules/${m._key}`)"
                >
                  <v-icon :icon="m.enabled ? 'mdi-toggle-switch' : 'mdi-toggle-switch-off-outline'" size="small" class="mr-1" />
                  {{ m.name }}
                </v-btn>
              </div>
            </v-card-text>
            <v-card-actions>
              <v-btn variant="text" size="small" @click="logs.clear()">清空本地日志</v-btn>
            </v-card-actions>
          </v-card>
        </v-col>
      </v-row>

      <v-row class="mt-1">
        <v-col cols="12">
          <v-card variant="outlined">
            <v-card-title>
              <v-icon icon="mdi-console" class="mr-2" color="primary" /> 最近日志
              <v-spacer />
              <v-btn variant="text" size="small" @click="logs.clear()">清空</v-btn>
            </v-card-title>
            <v-card-text class="log-preview">
              <EmptyState v-if="previewLogs.length === 0" icon="mdi-text-box-outline" title="暂无日志" description="日志将实时显示在底部控制台" />
              <div v-for="(row, i) in previewLogs" :key="`${row.timestamp}-${i}`" class="log-row">
                <span class="log-time">{{ row.timestamp }}</span>
                <span class="log-level" :class="`log-level-${row.level}`">{{ row.level.toUpperCase() }}</span>
                <span class="log-message">{{ row.message }}</span>
              </div>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>
    </template>
  </div>
</template>

<style scoped>
.stat-card {
  border-radius: 16px;
}

.stat-label {
  margin-top: 14px;
  font-size: 13px;
  font-weight: 500;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.stat-value {
  margin-top: 4px;
  font-size: clamp(26px, 2.2vw, 34px);
  font-weight: 700;
  letter-spacing: -0.03em;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
}

.h-full {
  height: 100%;
}

.bot-row {
  border-radius: 10px;
}

.module-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.module-chip {
  border-radius: 999px !important;
}

.log-preview {
  max-height: 320px;
  overflow-y: auto;
  font-family: var(--app-font-mono);
  font-size: 12.5px;
}

.log-row {
  display: flex;
  gap: 10px;
  padding: 2px 0;
  border-bottom: 1px dashed rgba(var(--v-theme-on-surface), 0.05);
}

.log-time {
  color: rgba(var(--v-theme-on-surface), 0.42);
  flex-shrink: 0;
}

.log-level {
  flex-shrink: 0;
  min-width: 52px;
  text-align: center;
  padding: 0 6px;
  border-radius: 999px;
  font-size: 10.5px;
  font-weight: 700;
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
</style>
