<script setup lang="ts">
/**
 * 「工具总览」—— 一个页面说清所有工具现在能不能用、为什么。
 *
 * 数据来自 `GET /api/agent/tools`（后端唯一判定点），所以这里显示的状态与真实行为一致。
 * 开关写 `POST /api/agent/tools/toggle`（唯一写入总表）。
 *
 * 注意：工具状态是按账号判定的，必须先 `agent.load()` 拿到当前账号再请求；
 * 之前直接拿 botId=null 去请求，后端报「账号不存在」，页面永远 0 工具。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { errorMessage, http } from '@/api/http'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'
import { useModulesStore } from '@/stores/modules'
import { toolDisplayBrief, toolDisplayTitle } from './toolDisplay'
import EmptyState from '@/components/EmptyState.vue'

interface ToolState {
  name: string
  source: string
  owner: string
  available: boolean
  reason: string
  reason_text: string
  detail?: string
}

const agent = useAgentConfigStore()
const bots = useBotsStore()
const modules = useModulesStore()
const tools = ref<ToolState[]>([])
/** 工具状态接口不返回 description：按来源再拉一次清单补中文名 */
const descriptionByTool = ref<Record<string, string>>({})
const loading = ref(false)
const ready = ref(false)
const error = ref('')
const keyword = ref('')
const onlyDisabled = ref(false)
const busy = ref<string>('')

const SOURCE_LABEL: Record<string, string> = {
  onebot: 'OneBot',
  module: '插件',
  system: '系统',
  mcp: '外部',
}

/** 工具状态接口不返回 description：按来源补拉清单，拼出中文名给「工具」列用 */
const DESC_ENDPOINTS: { source: string; url: string }[] = [
  { source: 'system', url: '/api/agent/system/tools' },
  { source: 'onebot', url: '/api/agent/onebot/tools' },
  { source: 'mcp', url: '/api/agent/mcp/tools' },
  { source: 'module', url: '/api/agent/module/tools' },
]

async function loadDescriptions() {
  if (!agent.botId) return
  const results = await Promise.allSettled(
    DESC_ENDPOINTS.map((item) =>
      http.get<{ tools: { name: string; description?: string }[] }>(item.url, {
        params: { account_id: agent.botId },
      }),
    ),
  )
  const next: Record<string, string> = {}
  results.forEach((result, index) => {
    if (result.status !== 'fulfilled') return
    const source = DESC_ENDPOINTS[index].source
    for (const tool of result.value.data?.tools || []) {
      if (tool?.name) next[`${source}:${tool.name}`] = String(tool.description || '')
    }
  })
  descriptionByTool.value = next
}

/** 给展示辅助补上描述（工具状态本身不带 description） */
function toolForDisplay(tool: ToolState): { name: string; description?: string } {
  return { name: tool.name, description: descriptionByTool.value[`${tool.source}:${tool.name}`] || '' }
}

function titleOf(tool: ToolState): string {
  return toolDisplayTitle(toolForDisplay(tool))
}

function briefOf(tool: ToolState): string {
  return toolDisplayBrief(toolForDisplay(tool), 40)
}

/** 归属列说人话：框架内置 / 中文插件名；取不到就给「插件」 */
function ownerLabel(tool: ToolState): string {
  if (tool.owner === 'framework') return '系统内置'
  const pluginName = modules.displayNameOf(tool.owner)
  if (pluginName) return pluginName
  if (tool.source === 'mcp') return tool.owner ? `外部服务：${tool.owner}` : '外部服务'
  return '插件'
}

/** 状态文案里可能拼了内部模块名/旧术语，展示前换成中文与统一叫法 */
function reasonText(tool: ToolState): string {
  const withOwner =
    tool.owner === 'framework' || !tool.owner
      ? tool.reason_text
      : tool.reason_text.split(tool.owner).join(ownerLabel(tool))
  return withOwner.split('模块未启用').join('插件未启用')
}

/** 没有任何账号：工具状态无从判定，先引导去账号管理 */
const noAccount = computed(() => agent.botId === null)

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return tools.value.filter((t) => {
    if (onlyDisabled.value && t.available) return false
    if (!kw) return true
    return (
      t.name.toLowerCase().includes(kw) ||
      titleOf(t).toLowerCase().includes(kw) ||
      t.owner.toLowerCase().includes(kw) ||
      ownerLabel(t).toLowerCase().includes(kw) ||
      (SOURCE_LABEL[t.source] ?? t.source).toLowerCase().includes(kw)
    )
  })
})

const stats = computed(() => {
  const total = tools.value.length
  const on = tools.value.filter((t) => t.available).length
  return { total, on, off: total - on }
})

/** 空表原因分三种：还没读完账号 / 没有匹配 / 这个来源就是没有工具 */
const emptyText = computed(() => {
  if (!ready.value) return '正在读取账号与工具状态'
  if (!tools.value.length) return '还没有可用的工具'
  if (onlyDisabled.value) return '所有工具当前都可用'
  return '没有匹配的工具'
})

async function load() {
  if (!agent.botId) {
    tools.value = []
    return
  }
  loading.value = true
  error.value = ''
  try {
    const resp = await http.get<{ tools: ToolState[] }>('/api/agent/tools', {
      params: { account_id: agent.botId },
    })
    tools.value = resp.data.tools ?? []
  } catch (e) {
    tools.value = []
    error.value = errorMessage(e)
  } finally {
    loading.value = false
  }
}

async function toggle(tool: ToolState) {
  busy.value = tool.name
  try {
    await http.post('/api/agent/tools/toggle', {
      name: tool.name,
      source: tool.source,
      enabled: !tool.available,
    })
    await load()
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    busy.value = ''
  }
}

async function init() {
  await agent.load()
  ready.value = true
  if (noAccount.value) return
  await Promise.all([load(), loadPluginNames(), loadDescriptions()])
}

/** 归属列需要插件中文名：列表没加载过就补拉一次（失败时归属列显示「插件」） */
async function loadPluginNames() {
  try {
    if (!modules.list.length) await modules.load()
  } catch {
    /* 名称映射失败不影响工具状态展示 */
  }
}

onMounted(init)

// 切账号：工具状态是账号维度的，按新账号重拉
watch(
  () => bots.currentAccountId,
  async () => {
    await agent.load(true)
    descriptionByTool.value = {}
    await Promise.all([load(), loadDescriptions()])
  },
)
</script>

<template>
  <div>
    <div class="app-toolbar">
      <v-text-field
        v-model="keyword"
        prepend-inner-icon="mdi-magnify"
        placeholder="搜索工具"
        density="compact"
        variant="outlined"
        hide-details
        clearable
        class="overview-search"
        style="max-width: 240px"
      />
      <span class="app-muted">可用 {{ stats.on }} / 共 {{ stats.total }}</span>
      <v-spacer />
      <div class="d-flex align-center gap-2">
        <span class="text-body-2">只看不可用</span>
        <v-switch v-model="onlyDisabled" density="compact" hide-details inset color="primary" />
      </div>
      <v-btn
        icon="mdi-refresh"
        variant="text"
        size="small"
        title="重新加载"
        :loading="loading"
        :disabled="!ready || noAccount"
        @click="load"
      />
    </div>

    <v-alert v-if="ready && noAccount" type="info" variant="tonal" density="compact" class="mb-3">
      <div class="d-flex align-center flex-wrap gap-2">
        <span class="text-caption">先在账号管理里添加或选中一个账号，工具状态按账号判定。</span>
        <v-btn size="small" variant="text" prepend-icon="mdi-account-outline" to="/bots">去账号管理</v-btn>
      </div>
    </v-alert>

    <v-alert v-else-if="error" type="error" variant="tonal" density="compact" class="mb-3">
      <div class="d-flex align-center flex-wrap gap-2">
        <span class="text-caption">{{ error }}</span>
        <v-btn size="small" variant="text" prepend-icon="mdi-refresh" @click="load">重试</v-btn>
      </div>
    </v-alert>

    <div class="overview-list">
      <div v-for="tool in filtered" :key="`${tool.source}:${tool.name}`" class="overview-row">
        <div class="overview-main">
          <div class="overview-title">{{ titleOf(tool) }}</div>
          <div v-if="briefOf(tool)" class="overview-brief app-line-clamp-1">{{ briefOf(tool) }}</div>
          <div class="overview-meta">
            <v-chip size="x-small" variant="tonal">{{ SOURCE_LABEL[tool.source] ?? tool.source }}</v-chip>
            <span class="app-muted">{{ ownerLabel(tool) }}</span>
            <span v-if="tool.available" class="state-on">
              <v-icon icon="mdi-check-circle" size="14" class="mr-1" />可用
            </span>
            <span v-else class="state-off">
              <v-icon icon="mdi-minus-circle-outline" size="14" class="mr-1" />{{ reasonText(tool) }}
            </span>
            <v-btn
              v-if="tool.reason === 'module_disabled'"
              size="x-small"
              variant="text"
              color="primary"
              to="/modules"
            >
              去插件页启用
            </v-btn>
          </div>
        </div>
        <v-btn
          size="small"
          variant="text"
          :loading="busy === tool.name"
          :disabled="tool.source === 'onebot' && !tool.available && tool.reason === 'module_disabled'"
          @click="toggle(tool)"
        >
          {{ tool.available ? '停用' : '启用' }}
        </v-btn>
      </div>

      <EmptyState
        v-if="!filtered.length"
        icon="mdi-radar"
        :title="emptyText"
        description="插件启用后，它提供的工具会出现在这里"
      >
        <v-btn size="small" variant="tonal" @click="load">重新加载</v-btn>
      </EmptyState>
    </div>
  </div>
</template>

<style scoped>
.overview-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.overview-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 10px;
}

.overview-main {
  flex: 1 1 auto;
  min-width: 0;
}

.overview-title {
  font-size: 14px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.overview-brief {
  margin-top: 2px;
  font-size: 12.5px;
  line-height: 1.5;
  color: rgba(var(--v-theme-on-surface), 0.62);
}

.overview-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 10px;
  margin-top: 4px;
}

.state-on {
  color: #16a34a;
  font-size: 12px;
  display: inline-flex;
  align-items: center;
}

.state-off {
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 12px;
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 2px;
}

@media (max-width: 720px) {
  .overview-row {
    align-items: flex-start;
  }

  .overview-search {
    max-width: none !important;
    flex: 1 1 100%;
  }
}
</style>
