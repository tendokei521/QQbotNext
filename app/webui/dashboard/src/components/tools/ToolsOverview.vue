<script setup lang="ts">
/**
 * 「工具总览」—— 一个页面说清所有工具现在能不能用、为什么。
 *
 * 数据来自 `GET /api/agent/tools`（后端唯一判定点 `resolve_tool_state`），
 * 所以这里显示的状态与真实行为一致，不再由前端自己推断。
 *
 * 开关写 `POST /api/agent/tools/toggle`（唯一写入总表 `tools_disabled`）。
 */
import { computed, onMounted, ref } from 'vue'
import { errorMessage, http } from '@/api/http'
import { useAgentConfigStore } from '@/stores/agentConfig'

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
const tools = ref<ToolState[]>([])
const loading = ref(false)
const error = ref('')
const keyword = ref('')
const onlyDisabled = ref(false)
const busy = ref<string>('')

const SOURCE_LABEL: Record<string, string> = {
  onebot: 'OneBot',
  module: '模块',
  system: '系统',
  mcp: 'MCP',
}

const filtered = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return tools.value.filter((t) => {
    if (onlyDisabled.value && t.available) return false
    if (!kw) return true
    return (
      t.name.toLowerCase().includes(kw) ||
      t.owner.toLowerCase().includes(kw) ||
      (SOURCE_LABEL[t.source] ?? t.source).toLowerCase().includes(kw)
    )
  })
})

const stats = computed(() => {
  const total = tools.value.length
  const on = tools.value.filter((t) => t.available).length
  return { total, on, off: total - on }
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const resp = await http.get<{ tools: ToolState[] }>('/api/agent/tools', {
      params: { bot_id: agent.botId ?? undefined },
    })
    tools.value = resp.data.tools ?? []
  } catch (e: any) {
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
  } catch (e: any) {
    error.value = errorMessage(e)
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div>
    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center flex-wrap gap-2">
        <v-icon icon="mdi-format-list-bulleted-triangle" class="mr-2" color="primary" />
        工具总览
        <v-chip size="small" color="success" variant="tonal">可用 {{ stats.on }}</v-chip>
        <v-chip size="small" variant="tonal">共 {{ stats.total }}</v-chip>
        <v-spacer />
        <v-text-field
          v-model="keyword"
          prepend-inner-icon="mdi-magnify"
          placeholder="搜索工具 / 模块"
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
        <v-btn size="small" variant="text" prepend-icon="mdi-refresh" :loading="loading" @click="load">
          刷新
        </v-btn>
      </v-card-title>
      <v-card-text class="text-body-2" style="opacity: 0.8">
        状态由后端统一判定（模块是否启用、模块内开关、工具清单开关、依赖是否就绪），此处显示的就是实际行为。
      </v-card-text>
    </v-card>

    <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mb-3">{{ error }}</v-alert>

    <v-card variant="outlined">
      <v-table density="compact">
        <thead>
          <tr>
            <th>工具</th>
            <th style="width: 110px">来源</th>
            <th style="width: 150px">归属</th>
            <th style="width: 190px">状态</th>
            <th style="width: 110px" class="text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="tool in filtered" :key="`${tool.source}:${tool.name}`">
            <td>
              <span class="tool-name">{{ tool.name }}</span>
            </td>
            <td>
              <v-chip size="x-small" variant="tonal">{{ SOURCE_LABEL[tool.source] ?? tool.source }}</v-chip>
            </td>
            <td class="text-caption">{{ tool.owner }}</td>
            <td>
              <span v-if="tool.available" class="state-on">
                <v-icon icon="mdi-check-circle" size="14" class="mr-1" />可用
              </span>
              <span v-else class="state-off">
                <v-icon icon="mdi-minus-circle-outline" size="14" class="mr-1" />{{ tool.reason_text }}
                <v-chip
                  v-if="tool.reason === 'module_disabled'"
                  size="x-small"
                  variant="text"
                  color="primary"
                  class="ml-2"
                  to="/modules"
                >
                  去模块页启用
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
                {{ tool.available ? '关闭' : '启用' }}
              </v-btn>
            </td>
          </tr>
          <tr v-if="!filtered.length">
            <td colspan="5" class="text-center text-caption py-6" style="opacity: 0.6">
              没有匹配的工具
            </td>
          </tr>
        </tbody>
      </v-table>
    </v-card>
  </div>
</template>

<style scoped>
.tool-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
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
