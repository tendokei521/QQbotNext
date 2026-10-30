<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'
import { useModulesStore } from '@/stores/modules'
import { useToolsStore } from '@/stores/tools'
import { useNotifyStore } from '@/stores/notify'
import ToolSourceList from '@/components/tools/ToolSourceList.vue'
import type { ToolLike } from '@/components/tools/ToolRow.vue'

interface ModuleTool {
  name: string
  description: string
  parameters: Record<string, any>
  category: string
  source: string
  module: string
  module_label: string
  enabled: boolean
  ready: boolean
  effective: boolean
  prerequisite: string
}

const agent = useAgentConfigStore()
const bots = useBotsStore()
const modulesStore = useModulesStore()
const toolsStore = useToolsStore()
const notify = useNotifyStore()

const tools = ref<ModuleTool[]>([])
const loading = ref(false)
const loadError = ref('')

// 工具开关状态来自后端统一判定（/api/agent/tools），前端不再自算

const groups = computed(() => {
  const map = new Map<string, ModuleTool[]>()
  for (const tool of tools.value) {
    const category = tool.module_label || tool.category || '插件工具'
    if (!map.has(category)) map.set(category, [])
    map.get(category)!.push(tool)
  }
  return Array.from(map.entries()).map(([category, items]) => ({ category, items }))
})

/** 插件内部名 → 用户可见名；取不到显示「插件」，原名只用于 tooltip */
const moduleNames = computed<Record<string, string>>(() => {
  const out: Record<string, string> = {}
  for (const tool of tools.value) {
    if (!tool.module) continue
    out[tool.module] = modulesStore.displayNameOf(tool.module) || '插件'
  }
  return out
})

const emptyText = computed(() =>
  !agent.botId ? '还没有账号，先去账号管理添加一个' : '还没有插件提供工具',
)

/** 统一写入口：后端记进 `tools_disabled`（唯一总表），不再各写一套 map */
async function toggleTool(tool: ToolLike) {
  const next = !tool.enabled
  try {
    await http.post('/api/agent/tools/toggle', {
      name: tool.name,
      source: 'module',
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
    const res = await http.get<{ ok: boolean; log_enabled: boolean; tools: ModuleTool[] }>('/api/agent/module/tools', {
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
  const top = toolsStore.scrollTop.module || 0
  if (top) window.scrollTo(0, top)
}

function saveScroll() {
  toolsStore.setScroll('module', window.scrollY || 0)
}

async function loadPluginNames() {
  try {
    if (!modulesStore.list.length) await modulesStore.load()
  } catch {
    /* 插件名映射失败不影响工具开关；卡片会显示「插件」 */
  }
}

onMounted(async () => {
  await agent.load()
  loadTools()
  void loadPluginNames()
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
    void loadPluginNames()
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
      source="module"
      :loading="loading"
      :error="loadError"
      :empty-text="emptyText"
      :module-names="moduleNames"
      @toggle="toggleTool"
      @reload="loadTools"
    />
  </div>
</template>
