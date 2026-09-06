<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToolsStore, type ToolTab } from '@/stores/tools'

const route = useRoute()
const router = useRouter()
const tools = useToolsStore()

const TABS: { value: ToolTab; to: string; label: string; icon: string }[] = [
  { value: 'system', to: '/tools/system', label: 'System Tools', icon: 'mdi-cog-outline' },
  { value: 'napcat', to: '/tools/napcat', label: 'Napcat Tools', icon: 'mdi-robot-industrial' },
  { value: 'mcp', to: '/tools/mcp', label: 'MCP Tools', icon: 'mdi-server-network' },
  { value: 'module', to: '/tools/module', label: '模块 Tools', icon: 'mdi-puzzle-outline' },
]

const activeTab = computed<ToolTab>(() => {
  if (route.path.startsWith('/tools/napcat')) return 'napcat'
  if (route.path.startsWith('/tools/mcp')) return 'mcp'
  if (route.path.startsWith('/tools/module')) return 'module'
  return 'system'
})

function onSelect(tab: ToolTab) {
  tools.setActiveTab(tab)
  const target = TABS.find((t) => t.value === tab)
  if (target) router.push(target.to)
}

onMounted(() => {
  if (route.path === '/tools' || route.path === '/tools/') {
    const target = TABS.find((t) => t.value === tools.activeTab) || TABS[0]
    router.replace(target.to)
  }
})

watch(
  () => route.path,
  () => {
    const tab = activeTab.value
    if (tools.activeTab !== tab) tools.setActiveTab(tab)
  },
)
</script>

<template>
  <div>
    <div class="app-page-header">
      <div>
        <h1 class="app-page-title">Tool 管理</h1>
        <div class="app-page-subtitle">统一管理四类 LLM 工具的开关与日志输出</div>
      </div>
    </div>

    <v-card variant="outlined" class="mb-4">
      <v-tabs
        :model-value="activeTab"
        color="primary"
        align-tabs="start"
        density="comfortable"
        @update:model-value="(v: any) => onSelect(v as ToolTab)"
      >
        <v-tab v-for="tab in TABS" :key="tab.value" :value="tab.value">
          <v-icon start size="small">{{ tab.icon }}</v-icon>
          {{ tab.label }}
        </v-tab>
      </v-tabs>
    </v-card>

    <router-view />
  </div>
</template>
