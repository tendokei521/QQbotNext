<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useBotsStore, displayTitle, resolveAccount, type BotStatus } from '@/stores/bots'
import { useModulesStore } from '@/stores/modules'
import { useLogsStore } from '@/stores/logs'
import { useWebuiStore } from '@/stores/webui'
import { useNotifyStore } from '@/stores/notify'
import { useReadinessStore } from '@/stores/readiness'
import { errorMessage } from '@/api/http'
import { cleanLogMessage, cleanLogTimestamp, isTechnicalLog } from '@/utils/log'
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

// 诊断台：针对某个账号看协议端事件、发测试消息
const monitorOpen = ref(false)
const monitorAccount = ref<{ id: string; name: string } | null>(null)

function openMonitor(bot: { id: string; name?: string }) {
  monitorAccount.value = { id: bot.id, name: displayTitle(bot as any) }
  monitorOpen.value = true
}

/** 账号行点进「账号管理」并高亮这个账号；连接/断开只在那里操作 */
function openAccount(accountId: string) {
  bots.selectBot(accountId)
  router.push({ path: '/bots', query: { highlight: accountId } })
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

// 总览只展示「机器人做了什么」：启动地址/鉴权/预加载这类技术行交给日志页。
// isTechnicalLog 的提示词按清理后的正文匹配（原始行还带 [Main] 等前缀）；
// warning/error 永不隐藏——真出问题不会被吞。规则只在 utils/log.ts 维护一份。
function isOverviewTechnical(row: { level: string; message: string }): boolean {
  return isTechnicalLog(row.level, cleanLogMessage(row.message))
}

const previewLogs = computed(() =>
  logs.filtered.filter((row) => isSimpleLog(row) && !isOverviewTechnical(row)).slice(-12).reverse(),
)

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

onMounted(() => {
  // 这两个 store 的加载可能 reject：不 catch 会冒到全局错误兜底（误报「页面出现异常」）
  if (!modules.list.length) {
    modules.load().catch((err) => {
      notify.push(`功能插件加载失败：${errorMessage(err)}`, 'error')
    })
  }
  if (!logs.logs.length) {
    logs.refresh().catch((err) => {
      notify.push(`日志加载失败：${errorMessage(err)}`, 'warning')
    })
  }
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

    <!-- 统计卡：紧凑横排（390 下 2×2），不再是四张占满屏的大卡 -->
    <div class="stat-grid">
      <v-card v-for="card in statCards" :key="card.label" variant="outlined" class="stat-card">
        <div class="stat-card__inner">
          <span class="app-icon-tile" :class="`app-icon-tile--${card.tile}`">
            <v-icon :icon="card.icon" size="20" />
          </span>
          <div class="stat-card__text">
            <span class="stat-label">{{ card.label }}</span>
            <span class="stat-value">{{ card.value }}</span>
          </div>
        </div>
      </v-card>
    </div>

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
      <div class="overview-split">
        <v-card variant="outlined" class="app-card-fill">
          <v-card-title>
            <v-icon icon="mdi-robot" class="mr-2" color="primary" /> 账号状态
            <v-spacer />
            <v-btn variant="text" size="small" to="/bots">账号管理 →</v-btn>
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
              <v-list-item
                v-for="b in bots.bots"
                :key="b.id"
                class="bot-row"
                @click="openAccount(b.id)"
              >
                <template #prepend>
                  <v-icon :icon="b.status === 'connected' ? 'mdi-check-circle' : 'mdi-alert-circle'" :color="statusColor(b.status)" size="small" />
                </template>
                <v-list-item-title class="text-body-2">
                  {{ displayTitle(b) }}
                </v-list-item-title>
                <v-list-item-subtitle class="text-caption">
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
        </v-card>

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
                size="small"
                class="module-chip"
                :color="m.enabled ? 'primary' : undefined"
                @click="router.push(`/modules/${m._key}`)"
              >
                <v-icon :icon="m.enabled ? 'mdi-toggle-switch' : 'mdi-toggle-switch-off-outline'" size="small" class="mr-1" />
                {{ m.name }}
              </v-btn>
            </div>
          </v-card-text>
          <v-card-actions v-if="pinnedModules.length > 0">
            <span class="app-muted">更多功能在「功能插件」里开关</span>
          </v-card-actions>
        </v-card>
      </div>

      <v-card variant="outlined" class="overview-logs">
        <v-card-title>
          <v-icon icon="mdi-console" class="mr-2" color="primary" /> 最近日志
          <v-spacer />
          <v-btn variant="text" size="small" append-icon="mdi-arrow-right" to="/logs">查看全部日志</v-btn>
        </v-card-title>
        <v-card-text class="log-preview">
          <EmptyState
            v-if="previewLogs.length === 0"
            icon="mdi-text-box-outline"
            title="暂无需要关注的日志"
            description="机器人运行时的动态会显示在这里"
          >
            <v-btn size="small" variant="tonal" to="/logs">查看全部日志</v-btn>
          </EmptyState>
          <div v-for="(row, i) in previewLogs" :key="`${row.timestamp}-${i}`" class="log-row">
            <span class="log-time">{{ cleanLogTimestamp(row.timestamp) }}</span>
            <!-- 普通日志不占级别标签：一排「INFO」只会制造噪音，异常才需要跳出来 -->
            <span
              v-if="row.level !== 'info'"
              class="log-level"
              :class="`log-level-${row.level}`"
            >{{ row.level === 'warning' ? '警告' : row.level === 'error' ? '错误' : '调试' }}</span>
            <span class="log-message">{{ cleanLogMessage(row.message) }}</span>
          </div>
        </v-card-text>
      </v-card>
    </template>

    <EventMonitorDialog
      v-model="monitorOpen"
      :account-id="monitorAccount?.id ?? null"
      :account-name="monitorAccount?.name"
    />
  </div>
</template>

<style scoped>
/* 统计卡：紧凑横排；390 下 2×2，不再单列占满屏 */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}

.stat-card {
  border-radius: 14px;
}

.stat-card__inner {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
}

.stat-card__text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.stat-label {
  font-size: 12px;
  font-weight: 500;
  color: rgba(var(--v-theme-on-surface), 0.6);
  white-space: nowrap;
}

.stat-value {
  margin-top: 2px;
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.15;
  font-variant-numeric: tabular-nums;
}

/* 账号状态 / 常用功能：同一行等高，不再是 5:7 一大一小 */
.overview-split {
  display: grid;
  grid-template-columns: minmax(0, 5fr) minmax(0, 7fr);
  gap: 12px;
  align-items: stretch;
  margin-bottom: 12px;
}

.overview-logs {
  display: block;
}

.bot-row {
  border-radius: 10px;
  cursor: pointer;
}

.bot-row:hover {
  background: rgba(var(--v-theme-on-surface), 0.04);
}

.module-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.module-chip {
  border-radius: 999px !important;
}

@media (max-width: 959px) {
  .stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .overview-split {
    grid-template-columns: minmax(0, 1fr);
  }
}

.log-preview {
  max-height: 280px;
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
