<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'
import { useToolsStore } from '@/stores/tools'
import { useNotifyStore } from '@/stores/notify'
import ToolSourceList from '@/components/tools/ToolSourceList.vue'
import type { ToolLike } from '@/components/tools/ToolRow.vue'

interface McpTool {
  name: string
  description: string
  parameters: Record<string, any>
  category: string
  source: string
  enabled: boolean
  ready: boolean
  effective: boolean
  prerequisite: string
}

const agent = useAgentConfigStore()
const bots = useBotsStore()
const toolsStore = useToolsStore()
const notify = useNotifyStore()

const tools = ref<McpTool[]>([])
const loading = ref(false)
const loadError = ref('')

// 工具开关状态来自后端统一判定（/api/agent/tools），前端不再自算

const groups = computed(() => {
  const map = new Map<string, McpTool[]>()
  for (const tool of tools.value) {
    const category = tool.category || '外部工具'
    if (!map.has(category)) map.set(category, [])
    map.get(category)!.push(tool)
  }
  return Array.from(map.entries()).map(([category, items]) => ({ category, items }))
})

const emptyText = computed(() =>
  !agent.botId ? '还没有账号，先去账号管理添加一个' : '还没有外部工具',
)

/** 统一写入口：后端记进 `tools_disabled`（唯一总表），不再各写一套 map */
async function toggleTool(tool: ToolLike) {
  const next = !tool.enabled
  try {
    await http.post('/api/agent/tools/toggle', {
      name: tool.name,
      source: 'mcp',
      enabled: next,
    })
    tool.enabled = next
    tool.effective = next && (tool.ready ?? true)
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function loadTools() {
  loadError.value = ''
  if (!agent.botId) return
  loading.value = true
  try {
    const res = await http.get<{ ok: boolean; log_enabled: boolean; tools: McpTool[] }>('/api/agent/mcp/tools', {
      params: { account_id: agent.botId },
    })
    tools.value = res.data.tools || []
  } catch (err) {
    tools.value = []
    loadError.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

function restoreScroll() {
  const top = toolsStore.scrollTop.mcp || 0
  if (top) window.scrollTo(0, top)
}

function saveScroll() {
  toolsStore.setScroll('mcp', window.scrollY || 0)
}

onMounted(async () => {
  await agent.load()
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
    await agent.load(true)
    loadTools()
  },
)
</script>

<template>
  <div>
    <div class="app-toolbar">
      <span class="app-muted">共 {{ tools.length }} 个工具</span>
      <v-spacer />
      <v-btn
        icon="mdi-refresh"
        variant="text"
        size="small"
        title="重新加载"
        :loading="loading"
        @click="loadTools"
      />
    </div>

    <ToolSourceList
      :groups="groups"
      source="mcp"
      :loading="loading"
      :error="loadError"
      :empty-text="emptyText"
      @toggle="toggleTool"
      @reload="loadTools"
    />
  </div>
</template>
