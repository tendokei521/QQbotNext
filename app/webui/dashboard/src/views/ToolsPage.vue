<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToolsStore, type ToolTab } from '@/stores/tools'

const route = useRoute()
const router = useRouter()
const tools = useToolsStore()

const TABS: { value: ToolTab; to: string; label: string; icon: string }[] = [
  { value: 'overview', to: '/tools/overview', label: '总览', icon: 'mdi-format-list-bulleted-triangle' },
  { value: 'system', to: '/tools/system', label: '系统工具', icon: 'mdi-cog-outline' },
  { value: 'onebot', to: '/tools/onebot', label: 'OneBot 工具', icon: 'mdi-robot-industrial' },
  { value: 'mcp', to: '/tools/mcp', label: '外部工具', icon: 'mdi-server-network' },
  { value: 'module', to: '/tools/module', label: '插件工具', icon: 'mdi-puzzle-outline' },
]

const activeTab = computed<ToolTab>(() => {
  if (route.path.startsWith('/tools/overview')) return 'overview'
  if (route.path.startsWith('/tools/onebot')) return 'onebot'
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
        <h1 class="app-page-title">高级工具</h1>
        <div class="app-page-subtitle">四类工具的开关与可用状态</div>
      </div>
      <div class="d-flex flex-wrap gap-2">
        <v-btn variant="text" prepend-icon="mdi-cog-outline" @click="router.push('/settings')">
          返回设置
        </v-btn>
      </div>
    </div>

    <div class="app-toolbar">
      <v-btn
        v-for="tab in TABS"
        :key="tab.value"
        size="small"
        :variant="activeTab === tab.value ? 'tonal' : 'text'"
        :color="activeTab === tab.value ? 'primary' : undefined"
        :prepend-icon="tab.icon"
        @click="onSelect(tab.value)"
      >
        {{ tab.label }}
      </v-btn>
      <v-spacer />
      <span class="app-muted">调用日志在「高级功能」统一开关</span>
      <v-btn
        size="small"
        variant="text"
        prepend-icon="mdi-text-box-search-outline"
        @click="router.push({ path: '/ai/settings/advanced', query: { group: 'adv_tool_logs' } })"
      >
        去日志设置
      </v-btn>
    </div>

    <router-view />
  </div>
</template>
