<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useToolsStore, type ToolTab } from '@/stores/tools'

const route = useRoute()
const router = useRouter()
const tools = useToolsStore()

const TABS: { value: ToolTab; to: string; label: string; icon: string; desc: string }[] = [
  {
    value: 'overview',
    to: '/tools/overview',
    label: '总览',
    icon: 'mdi-format-list-bulleted-triangle',
    desc: '按来源汇总所有工具的实际可用状态，并说明不可用的原因',
  },
  { value: 'system', to: '/tools/system', label: '系统工具', icon: 'mdi-cog-outline', desc: '框架内置工具，控制模型能调用哪些能力' },
  { value: 'onebot', to: '/tools/onebot', label: 'OneBot 工具', icon: 'mdi-robot-industrial', desc: '通过 OneBot 协议调用 QQ 接口，例如发消息、查群' },
  { value: 'mcp', to: '/tools/mcp', label: '外部工具', icon: 'mdi-server-network', desc: 'MCP 服务提供的工具' },
  { value: 'module', to: '/tools/module', label: '插件工具', icon: 'mdi-puzzle-outline', desc: '功能插件提供给模型的工具，插件开关在「功能插件」页管理' },
]

const activeTab = computed<ToolTab>(() => {
  if (route.path.startsWith('/tools/overview')) return 'overview'
  if (route.path.startsWith('/tools/onebot')) return 'onebot'
  if (route.path.startsWith('/tools/mcp')) return 'mcp'
  if (route.path.startsWith('/tools/module')) return 'module'
  return 'system'
})

const activeDesc = computed(() => TABS.find((t) => t.value === activeTab.value)?.desc || '')

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
    <div class="app-page-header d-flex align-center flex-wrap gap-2">
      <div class="me-4">
        <h1 class="app-page-title">高级工具</h1>
        <div class="app-page-subtitle">统一查看四类工具的开关与可用状态</div>
      </div>
      <v-spacer />
      <v-btn variant="text" prepend-icon="mdi-cog-outline" @click="router.push('/settings')">
        返回设置
      </v-btn>
    </div>

    <v-card variant="outlined" class="mb-4">
      <v-card-text class="pb-0">
        <div class="text-caption" style="opacity: 0.7">{{ activeDesc }}</div>
      </v-card-text>
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

    <!-- 日志开关只有一处：AI 设置 → 高级功能 → 工具日志（配置分区由生成器维护） -->
    <v-card variant="outlined" class="mb-4">
      <v-card-text class="d-flex align-center flex-wrap gap-3">
        <v-icon icon="mdi-text-box-search-outline" color="primary" />
        <div class="text-body-2">
          工具调用日志（系统 / 插件 / 外部 / OneBot）在「AI 设置 → 高级功能 → 工具日志」统一开关。
        </div>
        <v-spacer />
        <v-btn
          size="small"
          variant="tonal"
          prepend-icon="mdi-cog-outline"
          @click="router.push({ path: '/ai/settings/advanced', query: { group: 'adv_tool_logs' } })"
        >
          去工具日志
        </v-btn>
      </v-card-text>
    </v-card>

    <router-view />
  </div>
</template>
