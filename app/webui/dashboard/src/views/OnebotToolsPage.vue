<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useBotsStore } from '@/stores/bots'
import { useToolsStore } from '@/stores/tools'
import { useNotifyStore } from '@/stores/notify'
import { PERMISSION_LABELS, permissionLabel } from '@/agent/labels'
import EmptyState from '@/components/EmptyState.vue'
import ToolRow from '@/components/tools/ToolRow.vue'
import { toolDisplayTitle } from '@/components/tools/toolDisplay'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'

interface OneBotTool {
  name: string
  description: string
  parameters: Record<string, any>
  risk: 'read' | 'send' | 'admin'
  permission: string
  scopes: string[]
  base_permission: string
  base_scopes: string[]
  sensitivity: 'normal' | 'high' | 'critical'
  category: string
  doc_url: string
  enabled: boolean
  blocked: boolean
  blocked_reason: string
  warning: string
}

interface ToolOverride {
  permission?: string
  scopes?: string[]
}

const bots = useBotsStore()
const toolsStore = useToolsStore()
const notify = useNotifyStore()

/** 策略键现在是 agent 本体配置；总开关（onebot_tools_enable）只在「高级选项」页编辑 */
const agentConfig = ref<Record<string, any>>({})

async function loadAgentConfig() {
  if (!bots.currentAccountId) return
  try {
    const res = await http.get<{ config: Record<string, any> }>('/api/agent/config', {
      params: { account_id: bots.currentAccountId },
    })
    agentConfig.value = res.data?.config || {}
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

/** 只提交改动的键（agent 配置按 key 合并） */
async function patchAgentConfig(patch: Record<string, unknown>) {
  if (!bots.currentAccountId) return
  agentConfig.value = { ...agentConfig.value, ...patch }
  try {
    await http.post(
      '/api/agent/config',
      { config: patch },
      { params: { account_id: bots.currentAccountId } },
    )
  } catch (err) {
    notify.push(errorMessage(err), 'error')
    await loadAgentConfig()
  }
}

const tools = ref<OneBotTool[]>([])
const loading = ref(false)

const DOC_CATEGORY_ORDER = [
  '流式传输扩展',
  '流式接口',
  '群组扩展',
  '核心接口',
  '群组接口',
  '扩展接口',
  '系统接口',
  '用户接口',
  '系统扩展',
  '消息扩展',
  '文件接口',
  'Go-CQHTTP',
  '消息接口',
  '文件扩展',
  '用户扩展',
  '频道接口',
  'AI 扩展',
]

const toolGroups = computed(() => {
  const map = new Map<string, OneBotTool[]>()
  for (const tool of tools.value) {
    const category = tool.category || '未分类'
    if (!map.has(category)) map.set(category, [])
    map.get(category)!.push(tool)
  }
  return Array.from(map.entries())
    .sort((a, b) => {
      const ia = DOC_CATEGORY_ORDER.indexOf(a[0])
      const ib = DOC_CATEGORY_ORDER.indexOf(b[0])
      return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib)
    })
    .map(([category, items]) => ({ category, items }))
})

const emptyText = computed(() =>
  !bots.currentAccountId ? '还没有账号，先去账号管理添加一个' : '还没有 OneBot 工具',
)

/** 总开关只在「高级选项」编辑；这里只读它来决定控件是否可编辑 */
const enabled = computed<boolean>(() => agentConfig.value.onebot_tools_enable !== false)

const overrides = computed<Record<string, ToolOverride>>(
  () => agentConfig.value.onebot_tool_overrides || {},
)

function toolPermission(tool: OneBotTool): string {
  return overrides.value[tool.name]?.permission || tool.base_permission || tool.permission
}

function toolScopes(tool: OneBotTool): string[] {
  return overrides.value[tool.name]?.scopes || tool.base_scopes || tool.scopes
}

function updateOverride(tool: OneBotTool, partial: ToolOverride) {
  const current = overrides.value[tool.name] || {}
  const next = { ...overrides.value, [tool.name]: { ...current, ...partial } }
  void patchAgentConfig({ onebot_tool_overrides: next })
}

function setToolPermission(tool: OneBotTool, permission: string) {
  updateOverride(tool, { permission })
}

function isSensitiveWide(tool: OneBotTool): boolean {
  if (!['high', 'critical'].includes(tool.sensitivity)) return false
  return ['everyone', 'member'].includes(toolPermission(tool))
}

const busyTool = ref('')
const resetOpen = ref(false)
const resetting = ref(false)

/** 逐工具开关与「工具总览」共用同一写入口：POST /api/agent/tools/toggle（source=onebot） */
async function toggleTool(tool: OneBotTool) {
  if (busyTool.value) return
  busyTool.value = tool.name
  try {
    await http.post('/api/agent/tools/toggle', {
      name: tool.name,
      source: 'onebot',
      enabled: !tool.enabled,
    })
    await loadTools()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    busyTool.value = ''
  }
}

/** 「恢复默认」：清空名单/权限覆盖，并把被停用的 OneBot 工具全部打开 */
async function doResetToggles() {
  if (resetting.value) return
  resetting.value = true
  try {
    await patchAgentConfig({
      onebot_tools_allowed: [],
      onebot_tools_denied: [],
      onebot_tool_overrides: {},
    })
    for (const tool of tools.value.filter((item) => !item.enabled)) {
      await http.post('/api/agent/tools/toggle', {
        name: tool.name,
        source: 'onebot',
        enabled: true,
      })
    }
    await loadTools()
    notify.push('已恢复默认', 'success')
    resetOpen.value = false
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    resetting.value = false
  }
}

function isCollapsed(category: string): boolean {
  return !!toolsStore.collapsedOf('onebot')[category]
}

function toggleCategory(category: string) {
  const next = { ...toolsStore.collapsedOf('onebot'), [category]: !isCollapsed(category) }
  toolsStore.setCollapsed('onebot', next)
}

function riskColor(risk: string): string {
  return risk === 'read' ? 'success' : risk === 'send' ? 'info' : 'warning'
}

function riskLabel(risk: string): string {
  return risk === 'read' ? '只读' : risk === 'send' ? '消息' : '管理'
}

/** 触发权限可选项：与「回复与权限」共用同一套叫法；OneBot 侧只有这 5 档 */
const ONEBOT_PERMISSION_KEYS = ['everyone', 'member', 'group_admin', 'group_owner', 'owner']
const PERMISSION_ITEMS = ONEBOT_PERMISSION_KEYS.map((value) => ({
  value,
  title: PERMISSION_LABELS[value] || value,
}))

const SCOPE_LABELS: Record<string, string> = {
  '*': '全部',
  group: '群聊',
  private: '私聊',
}

/** 可用范围：后端给的是 `*` / `group` / `private`，界面上说人话。 */
function scopeLabels(scopes: string[]): string {
  return scopes.map((scope) => SCOPE_LABELS[scope] || scope).join(' / ')
}

async function loadTools() {
  if (!bots.currentAccountId) return
  loading.value = true
  try {
    // 工具清单来自 OneBot 端点；开关状态以「/api/agent/tools」统一判定为准，
    // 这样与「工具总览」里停用/启用同一工具时两边状态一致。
    const [toolsRes, stateRes] = await Promise.all([
      http.get<{ ok: boolean; log_enabled: boolean; tools: OneBotTool[] }>('/api/agent/onebot/tools', {
        params: { account_id: bots.currentAccountId },
      }),
      http.get<{ tools: { name: string; source: string; available: boolean }[] }>('/api/agent/tools', {
        params: { account_id: bots.currentAccountId },
      }),
    ])
    const available = new Map<string, boolean>()
    for (const item of stateRes.data?.tools || []) {
      if (item.source === 'onebot') available.set(item.name, !!item.available)
    }
    tools.value = (toolsRes.data?.tools || []).map((tool) => ({
      ...tool,
      enabled: available.has(tool.name) ? !!available.get(tool.name) : !!tool.enabled,
    }))
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loading.value = false
  }
}

/** 策略配置在 agent 配置里：进入时读一次（含总开关状态） */
function restoreScroll() {
  const top = toolsStore.scrollTop.onebot || 0
  if (top) window.scrollTo(0, top)
}

function saveScroll() {
  toolsStore.setScroll('onebot', window.scrollY || 0)
}

onMounted(async () => {
  await loadAgentConfig()
  loadTools()
  restoreScroll()
  window.addEventListener('scroll', saveScroll, { passive: true })
})

onUnmounted(() => {
  saveScroll()
  window.removeEventListener('scroll', saveScroll)
})

watch(
  () => bots.currentAccountId,
  async () => {
    await loadAgentConfig()
    loadTools()
  },
)
</script>

<template>
  <div>
    <div class="app-toolbar">
      <v-spacer />
      <v-btn
        variant="text"
        size="small"
        prepend-icon="mdi-open-in-new"
        href="https://napcat.apifox.cn/"
        target="_blank"
        rel="noopener"
      >
        接口文档
      </v-btn>
      <v-btn variant="tonal" size="small" prepend-icon="mdi-restore" :disabled="!enabled" @click="resetOpen = true">
        恢复默认
      </v-btn>
    </div>

    <v-progress-linear v-if="loading" indeterminate color="primary" class="mb-3" />

    <div v-for="group in toolGroups" :key="group.category" class="tool-group">
      <div
        class="tool-category"
        role="button"
        tabindex="0"
        :aria-expanded="!isCollapsed(group.category)"
        @click="toggleCategory(group.category)"
        @keydown.enter.prevent="toggleCategory(group.category)"
        @keydown.space.prevent="toggleCategory(group.category)"
      >
        <v-icon :icon="isCollapsed(group.category) ? 'mdi-chevron-right' : 'mdi-chevron-down'" size="small" />
        <span class="app-section-title">{{ group.category }}</span>
        <span class="tool-category-count">{{ group.items.length }}</span>
      </div>
      <div v-show="!isCollapsed(group.category)" class="tool-list">
        <ToolRow
          v-for="tool in group.items"
          :key="tool.name"
          :tool="tool"
          :disabled="!enabled"
          :switch-loading="busyTool === tool.name"
          @toggle="toggleTool(tool)"
        >
          <template #badges>
            <v-chip size="x-small" variant="tonal" :color="riskColor(tool.risk)">{{ riskLabel(tool.risk) }}</v-chip>
            <a
              v-if="tool.doc_url"
              :href="tool.doc_url"
              target="_blank"
              rel="noopener"
              class="tool-doc-link"
              @click.stop
            >
              文档
            </a>
          </template>
          <template #meta>
            <span class="app-muted">
              权限：{{ permissionLabel(toolPermission(tool)) }} · 范围：{{ scopeLabels(toolScopes(tool)) }}
            </span>
          </template>
          <template #detail>
            <div class="tool-policy">
              <v-select
                :model-value="toolPermission(tool)"
                :disabled="!enabled"
                :items="PERMISSION_ITEMS"
                label="触发权限"
                variant="outlined"
                density="compact"
                hide-details
                class="tool-permission"
                @update:model-value="(v: any) => setToolPermission(tool, v)"
              />
              <span class="app-muted">可用范围：{{ scopeLabels(toolScopes(tool)) }}</span>
            </div>
            <v-alert v-if="isSensitiveWide(tool)" type="warning" variant="tonal" density="compact" class="mt-2">
              「{{ toolDisplayTitle(tool) }}」属于敏感操作，默认只给「{{ permissionLabel(tool.base_permission) }}」，现已放宽到「{{ permissionLabel(toolPermission(tool)) }}」。
            </v-alert>
          </template>
        </ToolRow>
      </div>
    </div>

    <EmptyState
      v-if="!tools.length && !loading"
      icon="mdi-robot-industrial"
      :title="emptyText"
      description="账号连接后这里会列出可调用的 QQ 接口"
    />

    <ConfirmDialog
      v-model="resetOpen"
      title="恢复 OneBot 工具默认设置"
      danger
      confirm-text="恢复默认"
      :loading="resetting"
      message="会清空逐工具的自定义设置，并把被停用的 OneBot 工具重新打开。"
      :details="[
        '清空「允许的工具」「禁止的工具」两份名单',
        '清空逐工具的触发权限与可用范围覆盖',
        '被关掉的 OneBot 工具会全部重新启用',
        '不可撤销，建议先确认当前设置确实想丢弃',
      ]"
      @confirm="doResetToggles"
    />
  </div>
</template>

<style scoped>
.tool-group {
  margin-bottom: 6px;
}

.tool-category {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 2px;
  cursor: pointer;
  user-select: none;
}

.tool-category:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
  border-radius: 6px;
}

.tool-category-count {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  background: rgba(var(--v-theme-on-surface), 0.06);
  border-radius: 999px;
  padding: 1px 8px;
}

.tool-doc-link {
  font-size: 12px;
  color: rgb(var(--v-theme-primary));
  text-decoration: none;
}

.tool-doc-link:hover {
  text-decoration: underline;
}

.tool-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 2px 0 8px;
}

.tool-policy {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 10px;
}

.tool-permission {
  max-width: 180px;
}
</style>
