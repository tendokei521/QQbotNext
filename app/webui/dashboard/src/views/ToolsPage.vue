<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'
import { useToolsStore, type ToolTab } from '@/stores/tools'
import SaveStatusBar from '@/components/common/SaveStatusBar.vue'

const route = useRoute()
const router = useRouter()
const tools = useToolsStore()
const agent = useAgentConfigStore()
const bots = useBotsStore()

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
  { value: 'mcp', to: '/tools/mcp', label: '外部工具', icon: 'mdi-server-network', desc: 'MCP 服务提供的工具，连接与参数在「功能插件 → 工具扩展」维护' },
  { value: 'module', to: '/tools/module', label: '插件工具', icon: 'mdi-puzzle-outline', desc: '功能插件提供给模型的工具，插件开关在「功能插件」页管理' },
]

/** 四类工具共用一组日志开关：这里集中放，避免每个标签页各摆一套 */
const LOG_SWITCHES: { key: string; label: string }[] = [
  { key: 'system_tools_log_enabled', label: '系统工具调用日志' },
  { key: 'module_tools_log_enabled', label: '插件工具调用日志' },
  { key: 'mcp_tools_log_enabled', label: '外部工具调用日志' },
  { key: 'onebot_tools_log_enabled', label: 'OneBot 工具调用日志' },
  { key: 'onebot_tools_debug', label: 'OneBot 详细调试日志' },
]

const activeTab = computed<ToolTab>(() => {
  if (route.path.startsWith('/tools/overview')) return 'overview'
  if (route.path.startsWith('/tools/onebot')) return 'onebot'
  if (route.path.startsWith('/tools/mcp')) return 'mcp'
  if (route.path.startsWith('/tools/module')) return 'module'
  return 'system'
})

const activeDesc = computed(() => TABS.find((t) => t.value === activeTab.value)?.desc || '')
const ready = ref(false)
const noAccount = computed(() => agent.botId === null)

function logValue(key: string): boolean {
  return !!agent.draft[key]
}

function setLog(key: string, value: boolean | null) {
  agent.onChange(key, !!value)
}

function onSelect(tab: ToolTab) {
  tools.setActiveTab(tab)
  const target = TABS.find((t) => t.value === tab)
  if (target) router.push(target.to)
}

async function initAgent() {
  await agent.load()
  ready.value = true
}

onMounted(async () => {
  await initAgent()
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

watch(
  () => bots.currentAccountId,
  () => {
    void agent.load(true)
  },
)
</script>

<template>
  <div>
    <div class="app-page-header d-flex align-center flex-wrap gap-2">
      <div class="me-4">
        <h1 class="app-page-title">高级工具</h1>
        <div class="app-page-subtitle">统一管理四类工具的开关、日志与可用状态</div>
      </div>
      <v-spacer />
      <v-btn variant="text" prepend-icon="mdi-cube-outline" @click="router.push('/modules')">
        返回功能插件
      </v-btn>
    </div>

    <v-card variant="outlined" class="mb-4">
      <v-card-text class="pb-0">
        <div class="text-caption" style="opacity: 0.7">{{ activeDesc }}</div>
        <div v-if="ready && noAccount" class="text-caption text-warning mt-1">
          还没有选中账号：工具开关与日志按账号保存，先在「账号管理」里添加或选中账号。
        </div>
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

    <v-card variant="outlined" class="mb-4">
      <v-expansion-panels variant="accordion" flat>
        <v-expansion-panel title="日志设置">
          <v-expansion-panel-text>
            <div class="d-flex flex-wrap gap-4 mb-2">
              <v-switch
                v-for="item in LOG_SWITCHES"
                :key="item.key"
                :model-value="logValue(item.key)"
                :label="item.label"
                color="primary"
                density="compact"
                hide-details
                :disabled="!ready || noAccount"
                @update:model-value="(v: boolean | null) => setLog(item.key, v)"
              />
            </div>
            <SaveStatusBar
              :status="agent.saveStatus"
              autosave
              hint="改动会自动保存；详细调试日志只排查问题时打开"
            />
          </v-expansion-panel-text>
        </v-expansion-panel>
      </v-expansion-panels>
    </v-card>

    <router-view />
  </div>
</template>
