<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useBotsStore, displayName, resolveAccount, resolvedBotId, type BotStatus } from '@/stores/bots'
import { useModulesStore } from '@/stores/modules'
import { useLogsStore } from '@/stores/logs'
import { useWebuiStore } from '@/stores/webui'
import { useNotifyStore } from '@/stores/notify'
import { useReadinessStore } from '@/stores/readiness'
import { errorMessage } from '@/api/http'
import { cleanLogMessage, cleanLogTimestamp } from '@/utils/log'
import EmptyState from '@/components/EmptyState.vue'
import ReadinessCard from '@/components/readiness/ReadinessCard.vue'
import EventMonitorDialog from '@/components/bots/EventMonitorDialog.vue'

const bots = useBotsStore()
const modules = useModulesStore()
const logs = useLogsStore()
const webui = useWebuiStore()
const notify = useNotifyStore()
const readiness = useReadinessStore()
const router = useRouter()

const refreshing = ref(false)
const connecting = ref(false)

// 诊断台：针对某个账号看协议端事件、发测试消息
const monitorOpen = ref(false)
const monitorAccount = ref<{ id: string; name: string } | null>(null)

function openMonitor(bot: { id: string }) {
  monitorAccount.value = { id: bot.id, name: displayName(bot as any) }
  monitorOpen.value = true
}

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
  { label: 'QQ 账号', value: bots.botCount, icon: 'mdi-robot-outline', tile: 'primary' },
  { label: '已连接', value: bots.connectedCount, icon: 'mdi-power-plug', tile: 'success' },
  { label: '功能插件', value: modules.count, icon: 'mdi-cube-outline', tile: 'info' },
  { label: '已启用', value: modules.enabledCount, icon: 'mdi-toggle-switch', tile: 'warning' },
])

const isLoading = computed(() => bots.loading || modules.loading || refreshing.value)

async function refreshAll() {
  refreshing.value = true
  try {
    // 「刷新状态」要连就绪卡一起刷：否则账号变了，横幅还停在旧结论
    await Promise.all([bots.fetchBots(), modules.load(), logs.refresh(), readiness.load()])
    notify.push('状态已刷新', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    refreshing.value = false
  }
}

async function connectCurrent() {
  if (connecting.value) return
  if (bots.currentId === null) {
    notify.push('请先选择账号', 'warning')
    return
  }
  connecting.value = true
  try {
    await bots.connect(bots.currentId)
    notify.push('已发送连接请求', 'success')
    await Promise.all([bots.fetchBots(), readiness.load()])
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    connecting.value = false
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
        <div class="app-page-subtitle">账号、模型与功能运行情况</div>
      </div>
      <v-btn variant="tonal" prepend-icon="mdi-refresh" :loading="refreshing" @click="refreshAll">刷新状态</v-btn>
    </div>

    <!-- 运行状态：与启动横幅、首启向导共用 /api/readiness -->
    <ReadinessCard />

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
        <v-skeleton-loader type="heading, list-item-three-line, actions" class="rounded-xl" />
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
                title="还没有账号"
                description="先去「账号管理」添加一个 QQ 号，再把它连起来"
              >
                <v-btn size="small" color="primary" variant="tonal" to="/bots">去添加账号</v-btn>
              </EmptyState>
              <v-list v-else density="compact" class="bg-transparent">
                <v-list-item v-for="b in bots.bots" :key="b.id" class="bot-row">
                  <template #prepend>
                    <v-icon :icon="b.status === 'connected' ? 'mdi-check-circle' : 'mdi-alert-circle'" :color="statusColor(b.status)" size="small" />
                  </template>
                  <v-list-item-title>
                    {{ displayName(b) }}{{ resolvedBotId(b) ? ` · QQ ${resolvedBotId(b)}` : '' }}
                  </v-list-item-title>
                  <v-list-item-subtitle>
                    {{ resolveAccount(b)?.nickname || (b.last_error ? b.last_error : '尚未连接') }}
                  </v-list-item-subtitle>
                  <template #append>
                    <div class="d-flex align-center gap-1">
                      <v-btn
                        size="x-small"
                        variant="text"
                        icon="mdi-stethoscope"
                        title="诊断：查看协议端事件、发测试消息"
                        @click.stop="openMonitor(b)"
                      />
                      <v-chip size="x-small" :color="statusColor(b.status)" variant="tonal">{{ STATUS_TEXT[b.status] }}</v-chip>
                    </div>
                  </template>
                </v-list-item>
              </v-list>
            </v-card-text>
            <v-card-actions>
              <v-btn v-if="bots.currentId !== null && bots.currentBot?.status !== 'connected'" color="primary" variant="tonal" prepend-icon="mdi-plug" size="small" :loading="connecting" :disabled="connecting" @click="connectCurrent">
                连接当前账号
              </v-btn>
              <v-btn variant="text" size="small" to="/bots">管理账号 →</v-btn>
            </v-card-actions>
          </v-card>
        </v-col>

        <v-col cols="12" lg="7">
          <v-card variant="outlined" class="app-card-fill">
            <v-card-title>
              <v-icon icon="mdi-cube-outline" class="mr-2" color="primary" /> 常用功能
              <v-spacer />
              <v-btn variant="text" size="small" to="/modules">全部功能 →</v-btn>
            </v-card-title>
            <v-card-text>
              <EmptyState
                v-if="pinnedModules.length === 0"
                icon="mdi-cube-off-outline"
                title="功能还没加载"
                description="连接账号后，功能会自动就绪"
              >
                <v-btn size="small" variant="tonal" to="/modules">查看功能</v-btn>
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
              <EmptyState v-if="previewLogs.length === 0" icon="mdi-text-box-outline" title="暂无日志" description="机器人运行时的动态会显示在这里" />
              <div v-for="(row, i) in previewLogs" :key="`${row.timestamp}-${i}`" class="log-row">
                <span class="log-time">{{ cleanLogTimestamp(row.timestamp) }}</span>
                <span class="log-level" :class="`log-level-${row.level}`">{{ row.level.toUpperCase() }}</span>
                <span class="log-message">{{ cleanLogMessage(row.message) }}</span>
              </div>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>
    </template>

    <EventMonitorDialog
      v-model="monitorOpen"
      :account-id="monitorAccount?.id ?? null"
      :account-name="monitorAccount?.name"
    />
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
