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
import { toolDisplayTitle } from './toolDisplay'

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
  mcp: 'MCP',
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

/** 归属列说人话：框架内置 / 中文插件名；取不到就给「未知插件」 */
function ownerLabel(tool: ToolState): string {
  if (tool.owner === 'framework') return '框架内置'
  const pluginName = modules.displayNameOf(tool.owner)
  if (pluginName) return pluginName
  if (tool.source === 'mcp') return tool.owner ? `外部服务：${tool.owner}` : '外部服务'
  return '未知插件'
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
      toolDisplayTitle(toolForDisplay(t)).toLowerCase().includes(kw) ||
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
  if (!ready.value) return '正在读取账号与工具状态…'
  if (!tools.value.length) return '还没有可用的工具：插件已启用后，工具会出现在这里'
  if (onlyDisabled.value) return '所有工具当前都可用'
  return '没有匹配的工具，换个关键词试试'
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

/** 归属列需要模块中文名：列表没加载过就补拉一次（失败时归属列显示未知插件） */
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
    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center flex-wrap gap-2">
        <v-icon icon="mdi-format-list-bulleted-triangle" class="mr-2" color="primary" />
        工具总览
        <v-chip v-if="agent.botId" size="small" color="success" variant="tonal">可用 {{ stats.on }}</v-chip>
        <v-chip v-if="agent.botId" size="small" variant="tonal">共 {{ stats.total }}</v-chip>
        <v-spacer />
        <v-text-field
          v-model="keyword"
          prepend-inner-icon="mdi-magnify"
          placeholder="搜索工具 / 插件"
          density="compact"
          variant="outlined"
          hide-details
          clearable
          style="max-width: 240px"
        />
        <v-switch
          v-model="onlyDisabled"
          label="只看不可用"
          density="compact"
          hide-details
          inset
          color="primary"
        />
        <v-btn
          size="small"
          variant="text"
          prepend-icon="mdi-refresh"
          :loading="loading"
          :disabled="!ready || noAccount"
          @click="load"
        >
          重新加载
        </v-btn>
      </v-card-title>
      <v-card-text class="text-body-2" style="opacity: 0.8">
        这里显示的是实际生效状态：插件已启用、插件内开关已打开、工具没有被关闭、依赖已就绪。
      </v-card-text>
    </v-card>

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

    <v-card variant="outlined" class="overview-table-card">
      <div class="overview-table-scroll">
        <v-table density="comfortable" class="overview-table">
          <thead>
            <tr>
              <th style="min-width: 220px">工具</th>
              <th style="width: 100px">来源</th>
              <th style="min-width: 150px">归属</th>
              <th style="min-width: 210px">状态</th>
              <th style="width: 130px" class="text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="tool in filtered" :key="`${tool.source}:${tool.name}`">
              <td class="tool-cell">
                <div class="tool-title">{{ toolDisplayTitle(toolForDisplay(tool)) }}</div>
                <div class="tool-name" :title="`工具标识：${tool.name}`">{{ tool.name }}</div>
              </td>
              <td>
                <v-chip size="x-small" variant="tonal">{{ SOURCE_LABEL[tool.source] ?? tool.source }}</v-chip>
              </td>
              <td class="text-caption">{{ ownerLabel(tool) }}</td>
              <td>
                <span v-if="tool.available" class="state-on">
                  <v-icon icon="mdi-check-circle" size="14" class="mr-1" />可用
                </span>
                <span v-else class="state-off">
                  <v-icon icon="mdi-minus-circle-outline" size="14" class="mr-1" />{{ reasonText(tool) }}
                  <v-chip
                    v-if="tool.reason === 'module_disabled'"
                    size="x-small"
                    variant="text"
                    color="primary"
                    class="ml-2"
                    to="/modules"
                  >
                    去插件页启用
                  </v-chip>
                </span>
              </td>
              <td class="text-right">
                <v-btn
                  size="x-small"
                  variant="text"
                  :loading="busy === tool.name"
                  :disabled="tool.source === 'onebot' && !tool.available && tool.reason === 'module_disabled'"
                  @click="toggle(tool)"
                >
                  {{ tool.available ? '停用此工具' : '启用此工具' }}
                </v-btn>
              </td>
            </tr>
            <tr v-if="!filtered.length">
              <td colspan="5" class="text-center text-caption py-6" style="opacity: 0.6">
                {{ emptyText }}
              </td>
            </tr>
          </tbody>
        </v-table>
      </div>
    </v-card>
  </div>
</template>

<style scoped>
.overview-table-scroll {
  width: 100%;
  overflow-x: auto;
}

.overview-table {
  min-width: 780px;
}

.overview-table :deep(td),
.overview-table :deep(th) {
  white-space: nowrap;
}

.overview-table :deep(td.tool-cell) {
  white-space: normal;
}

.tool-cell {
  padding-block: 8px;
}

.tool-title {
  font-size: 13.5px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.tool-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  cursor: help;
  overflow-wrap: anywhere;
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
}
</style>
