<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useLogsStore } from '@/stores/logs'
import { useWebuiStore } from '@/stores/webui'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import { cleanLogMessage, cleanLogTimestamp, isTechnicalLog } from '@/utils/log'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import LogExportDialog from '@/components/log/LogExportDialog.vue'

const logs = useLogsStore()
const webui = useWebuiStore()
const notify = useNotifyStore()

const settingsOpen = ref(false)
const exportOpen = ref(false)
const clearConfirmOpen = ref(false)
const filterInput = ref('')
const bodyEl = ref<HTMLDivElement | null>(null)

/** 级别快捷筛选：默认全部；用户找问题时一键只看错误/警告 */
type LevelFilter = 'all' | 'error' | 'warning'
const LEVEL_FILTERS: { value: LevelFilter; title: string }[] = [
  { value: 'all', title: '全部' },
  { value: 'error', title: '错误' },
  { value: 'warning', title: '警告' },
]
const levelFilter = ref<LevelFilter>('all')

/** 技术日志开关：复用显示设置里的 show_raw_logs（启动/连接等内部细节默认收起） */
const showTechnical = computed<boolean>({
  get: () => Boolean(webui.config.logs.show_raw_logs),
  set: (value) => {
    webui.config.logs.show_raw_logs = value
    webui.saveLogs({ show_raw_logs: value }).catch((err) => {
      notify.push(errorMessage(err), 'error')
    })
  },
})

function isSimpleLog(row: { level: string; message: string }) {
  if (webui.config.logs.show_raw_logs) return true
  const msg = String(row.message || '')
  const level = String(row.level || '').toLowerCase()
  // 启动/连接/鉴权这类内部细节默认不展示（警告与错误不受影响）
  if (isTechnicalLog(level, msg)) return false
  const isApiError = ['warning', 'error'].includes(level)
  const isUserApiLog = msg.includes('[发送->]') || msg.includes('[请求->]')
  if (msg.startsWith('[API]') && !isUserApiLog && !isApiError) return false
  if (!isApiError && (msg.includes('API(->)') || msg.includes('API(<-)'))) return false
  return true
}

/** 「显示行数」设置（设置页可改）：以前固定 slice(-1000)，设置项形同虚设 */
const rowLimit = computed(() => {
  const n = Math.floor(Number(webui.config.logs.max_lines))
  if (!Number.isFinite(n) || n <= 0) return 200
  return Math.min(Math.max(n, 10), 2000)
})

const visibleRows = computed(() => {
  const filter = levelFilter.value
  const rows = logs.filtered.filter((row) => {
    if (!isSimpleLog(row)) return false
    if (filter === 'all') return true
    return String(row.level || '').toLowerCase() === filter
  })
  return rows.slice(-rowLimit.value)
})

/** 级别文案与「设置 → 日志」保持一致，界面上不出现英文枚举 */
const LEVEL_LABELS: Record<string, string> = {
  debug: '调试',
  info: '普通',
  warning: '警告',
  error: '错误',
}

function levelLabel(level: string): string {
  const key = String(level || '').toLowerCase()
  return LEVEL_LABELS[key] || level
}

// 弹窗草稿：打开时从 store 快照，保存时才写回，取消可直接丢弃（不再即时改 live 配置）
const editLevels = ref<string[]>([])
const editMaxLines = ref(50)
const editShowRawLogs = ref(false)

function openSettings() {
  editLevels.value = [...webui.config.logs.visible_levels]
  editMaxLines.value = webui.config.logs.max_lines
  editShowRawLogs.value = webui.config.logs.show_raw_logs
  settingsOpen.value = true
}

function onFilterInput() {
  logs.setFilter(filterInput.value)
}

async function saveLogsSettings() {
  try {
    await webui.saveLogs({
      show_raw_logs: editShowRawLogs.value,
      visible_levels: editLevels.value,
      max_lines: editMaxLines.value,
    })
    settingsOpen.value = false
    notify.push('日志设置已保存', 'success')
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

function confirmClear() {
  logs.clear()
  clearConfirmOpen.value = false
}

// 新日志到达时：仅在用户本来就停在底部附近时跟随滚动，避免打断回看。
// 判定必须基于「DOM 更新前」的位置（由 scroll 监听维护 followTail）：
// 更新后列表可能被整帧替换 / 浏览器把 scrollTop 钳位，此时现测会把
// 「用户本来就在底部」误判成「已经上滚」，于是既贴不回底部、又漏掉后续跟随。
let followTail = true
let scrollEl: HTMLDivElement | null = null

function isNearBottom(el: HTMLDivElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

function syncFollowTail() {
  const el = bodyEl.value
  if (el) followTail = isNearBottom(el)
}

async function followTailAfterUpdate() {
  await nextTick()
  const el = bodyEl.value
  if (!el || !followTail) return
  el.scrollTop = el.scrollHeight
  followTail = isNearBottom(el)
}

// 触发信号取「末行」而不是行数：累积缓冲到 MAX_CACHE 后行数恒定，
// 只看行数会漏掉新日志、从此不再跟随。
const tailToken = computed(() => {
  const rows = visibleRows.value
  const last = rows[rows.length - 1]
  return last ? `${rows.length}|${last.timestamp}|${last.level}|${last.message}` : ''
})

watch(tailToken, () => {
  if (!logs.paused) void followTailAfterUpdate()
})

onMounted(() => {
  scrollEl = bodyEl.value
  scrollEl?.addEventListener('scroll', syncFollowTail, { passive: true })
  if (!logs.logs.length) logs.refresh()
})

onUnmounted(() => {
  scrollEl?.removeEventListener('scroll', syncFollowTail)
  scrollEl = null
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
          placeholder="搜索日志…（账号名 / 关键字）"
          class="filter-input"
          clearable
          @update:model-value="onFilterInput"
        />
        <div class="level-seg">
          <v-btn-toggle
            v-model="levelFilter"
            density="compact"
            variant="outlined"
            divided
            mandatory
          >
            <v-btn v-for="opt in LEVEL_FILTERS" :key="opt.value" :value="opt.value" size="small">
              {{ opt.title }}
            </v-btn>
          </v-btn-toggle>
        </div>
        <v-switch
          v-model="showTechnical"
          label="技术日志"
          color="primary"
          density="compact"
          hide-details
          class="tech-switch"
          title="开启后显示启动、连接、鉴权等技术细节"
        />
        <v-btn
          size="small"
          variant="text"
          icon="mdi-file-export-outline"
          title="导出日志文件"
          @click="exportOpen = true"
        />
        <v-menu location="bottom end">
          <template #activator="{ props }">
            <v-btn v-bind="props" size="small" variant="text" icon="mdi-dots-vertical" title="更多日志操作" />
          </template>
          <v-list density="compact" min-width="180">
            <v-list-item
              :prepend-icon="logs.paused ? 'mdi-play' : 'mdi-pause'"
              :title="logs.paused ? '继续接收日志' : '暂停接收日志'"
              @click="logs.togglePause()"
            />
            <v-list-item prepend-icon="mdi-cog-outline" title="日志显示设置" @click="openSettings" />
            <v-list-item prepend-icon="mdi-delete-outline" title="清空日志显示" @click="clearConfirmOpen = true" />
          </v-list>
        </v-menu>
      </div>
    </div>

    <div ref="bodyEl" class="console-body">
      <div v-if="visibleRows.length === 0" class="empty-tip">
        <template v-if="!showTechnical && logs.logs.length">
          当前没有普通日志；打开工具栏的「技术日志」可查看启动与连接细节。
        </template>
        <template v-else>当前筛选下没有日志</template>
      </div>
      <div v-for="(row, i) in visibleRows" :key="`${row.timestamp}-${i}`" class="log-row">
        <span class="log-time">{{ cleanLogTimestamp(row.timestamp) }}</span>
        <!-- 普通日志不渲染级别标签：整屏「普通」只有噪音，异常才需要跳出来 -->
        <span
          v-if="row.level !== 'info'"
          class="log-level"
          :class="`log-level-${row.level}`"
        >{{ levelLabel(row.level) }}</span>
        <span class="log-message" :title="row.message">{{ cleanLogMessage(row.message) }}</span>
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
            v-model="editLevels"
            :label="levelLabel(lv)"
            :value="lv"
            density="compact"
            hide-details
            color="primary"
          />
          <v-text-field
            v-model.number="editMaxLines"
            label="显示行数"
            type="number"
            min="10"
            max="2000"
            density="compact"
            class="mt-3"
            hide-details
          />
          <v-switch
            v-model="editShowRawLogs"
            label="显示技术日志"
            color="primary"
            density="compact"
            class="mt-2"
            hide-details
          />
          <div class="text-caption mt-1" style="color: rgba(var(--v-theme-on-surface), 0.55)">
            默认关闭；开启后显示启动、连接等完整技术细节
          </div>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="settingsOpen = false">取消</v-btn>
          <v-btn color="primary" variant="tonal" @click="saveLogsSettings">保存</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <ConfirmDialog
      v-model="clearConfirmOpen"
      title="清空日志显示"
      message="只清空界面里显示的最近日志，不影响已保存的日志文件。"
      :details="['需要留档时，请先导出日志文件', '清空后新产生的日志仍会继续显示']"
      danger
      confirm-text="清空"
      @confirm="confirmClear"
    >
      <v-btn
        size="small"
        variant="tonal"
        prepend-icon="mdi-file-export-outline"
        @click="exportOpen = true; clearConfirmOpen = false"
      >
        先导出
      </v-btn>
    </ConfirmDialog>

    <LogExportDialog v-model="exportOpen" />
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
  min-height: 48px;
  padding: 6px 14px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  flex-wrap: wrap;
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
  flex-wrap: wrap;
}

.filter-input {
  width: 220px;
  max-width: 40vw;
}

.level-seg {
  flex: 0 0 auto;
}

.level-seg :deep(.v-btn) {
  font-size: 12px;
  min-width: 48px;
}

.tech-switch {
  flex: 0 0 auto;
  margin: 0;
}

.tech-switch :deep(.v-label) {
  font-size: 12px;
  opacity: 0.85;
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

@media (max-width: 900px) {
  .console-header {
    flex-direction: column;
    align-items: stretch;
  }

  .console-title {
    justify-content: space-between;
  }

  .filter-input {
    flex: 1 1 180px;
    width: auto;
    max-width: none;
  }
}
</style>
