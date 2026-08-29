<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useBotsStore } from '@/stores/bots'
import { useAgentConfigStore } from '@/stores/agentConfig'
import AgentTemplateDialog from '@/components/agent/AgentTemplateDialog.vue'
import AgentConfigSearchDialog from '@/components/agent/AgentConfigSearchDialog.vue'
import AgentCopyConfigDialog from '@/components/agent/AgentCopyConfigDialog.vue'

const bots = useBotsStore()
const agent = useAgentConfigStore()
const router = useRouter()
const loading = ref(false)

const botId = computed(() => bots.currentBot?.bot_id ?? null)
const templateDialog = ref(false)
const searchDialog = ref(false)
const copyDialog = ref(false)

const pages = computed(() => [
  {
    to: '/agent/basic',
    title: '基础配置',
    icon: 'mdi-tune-variant',
    desc: '提示词、群/私聊开关、历史与触发',
    status: '已配置',
    color: 'primary',
  },
  {
    to: '/agent/model',
    title: '模型',
    icon: 'mdi-api',
    desc: 'Provider 模型池与调用参数',
    status: agent.poolModelIds.length ? `${agent.poolModelIds.length} 个模型` : '未配置模型',
    color: 'info',
  },
  {
    to: '/agent/behavior',
    title: '对话行为',
    icon: 'mdi-account-voice',
    desc: '用户信息感知、回复打断、冷却',
    status: '可调',
    color: 'secondary',
  },
  {
    to: '/agent/stream',
    title: '流式回复',
    icon: 'mdi-wave',
    desc: '流式开关、发送频率与预设',
    status: agent.draft.stream_output ? '开启' : '关闭',
    color: 'purple',
  },
  {
    to: '/agent/permission',
    title: '权限',
    icon: 'mdi-shield-account-outline',
    desc: '黑白名单与角色权限',
    status: agent.permission.group_mode === 'blacklist' ? '黑名单' : '白名单',
    color: 'success',
  },
  {
    to: '/agent/memory',
    title: '长期记忆',
    icon: 'mdi-brain',
    desc: '记忆开关、召回与可信度',
    status: agent.draft.memory_enable ? '开启' : '关闭',
    color: 'warning',
  },
  {
    to: '/agent/knowledge',
    title: '知识库',
    icon: 'mdi-book-open-variant',
    desc: '知识库检索与 Embedding 模型',
    status: agent.draft.knowledge_enable ? '开启' : '关闭',
    color: 'deep-purple',
  },
  {
    to: '/agent/mcp',
    title: 'MCP 工具',
    icon: 'mdi-server-network',
    desc: 'MCP stdio server 配置',
    status: agent.draft.mcp_servers ? '已配置' : '未配置',
    color: 'blue-grey',
  },
  {
    to: '/agent/napcat',
    title: 'Napcat Tools',
    icon: 'mdi-robot-industrial',
    desc: '把 NapCat/OneBot API 暴露给 LLM',
    status: agent.draft.napcat_tools_enable ? '开启' : '关闭',
    color: 'teal',
  },
  {
    to: '/agent/panels',
    title: '定时任务 / 主动消息',
    icon: 'mdi-clock-outline',
    desc: '定时触发与主动发言',
    status: '管理',
    color: 'brown',
  },
])

const warnings = computed(() => {
  const list: string[] = []
  if (!agent.poolModelIds.length) list.push('尚未配置 Provider 模型，Agent 无法回复')
  if (agent.draft.memory_enable && !agent.draft.experimental_long_term_memory) {
    list.push('长期记忆已开启，但实验性长期记忆开关未开启')
  }
  if (agent.draft.knowledge_enable && !agent.draft.knowledge_embedding_model_id) {
    list.push('知识库已开启，但未指定 Embedding 模型')
  }
  return list
})

async function ensureLoaded() {
  if (botId.value === null) {
    loading.value = true
    await bots.fetchBots()
    if (bots.currentIndex === null && bots.bots.length) bots.restoreSelection()
    loading.value = false
  }
  await agent.load()
}

onMounted(ensureLoaded)
</script>

<template>
  <div>
    <div class="app-page-header" style="align-items: center">
      <div class="d-flex align-center gap-2 flex-wrap">
        <h1 class="app-page-title">Agent 面板</h1>
        <v-chip size="small" variant="tonal" color="primary">LLM · 框架级</v-chip>
        <v-chip v-if="agent.saveStatus === 'saving'" size="small" color="primary" variant="flat">
          <v-progress-circular size="12" indeterminate class="mr-1" /> 保存中…
        </v-chip>
        <v-chip v-else-if="agent.saveStatus === 'error'" size="small" color="error" variant="flat">保存失败</v-chip>
        <v-chip v-else-if="agent.saveStatus === 'dirty'" size="small" color="warning" variant="flat">未保存</v-chip>
      </div>
      <v-switch v-model="agent.enabled" color="primary" label="启用 Agent" density="compact" hide-details @update:model-value="(v: any) => agent.onEnabledChange(v)" />
    </div>
    <div class="app-page-subtitle">框架级 LLM Agent：配置、权限、定时任务与主动消息</div>

    <div v-if="botId === null" class="empty-tip">
      <v-icon icon="mdi-robot-off-outline" size="56" color="rgba(var(--v-theme-on-surface), 0.3)" />
      <div>请先在顶栏选择并连接一个 Bot，再配置 Agent</div>
      <v-btn variant="tonal" prepend-icon="mdi-refresh" class="mt-2" @click="ensureLoaded">重试</v-btn>
    </div>

    <template v-else>
      <v-progress-linear v-if="loading" indeterminate color="primary" />

      <v-card v-if="warnings.length" variant="outlined" color="warning" class="mb-4">
        <v-card-text class="d-flex flex-column gap-1">
          <div v-for="w in warnings" :key="w" class="d-flex align-center gap-2">
            <v-icon size="small" icon="mdi-alert-circle-outline" />
            <span>{{ w }}</span>
          </div>
        </v-card-text>
      </v-card>

      <v-card variant="outlined" class="mb-4">
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-view-dashboard-outline" class="mr-2" color="primary" /> 配置入口
          <v-spacer />
          <v-btn size="small" variant="tonal" prepend-icon="mdi-book-multiple" class="mr-2" @click="templateDialog = true">
            配置模板
          </v-btn>
          <v-btn size="small" variant="tonal" prepend-icon="mdi-magnify" class="mr-2" @click="searchDialog = true">
            搜索配置
          </v-btn>
          <v-btn size="small" variant="tonal" prepend-icon="mdi-content-copy" class="mr-2" @click="copyDialog = true">
            复制配置
          </v-btn>
          <v-btn size="small" variant="tonal" prepend-icon="mdi-content-save" :loading="agent.saveStatus === 'saving'" @click="agent.save()">
            保存配置
          </v-btn>
        </v-card-title>
        <v-card-text>
          <div class="agent-grid">
            <v-card
              v-for="p in pages"
              :key="p.to"
              variant="tonal"
              class="agent-card"
              :color="p.color"
              @click="router.push(p.to)"
            >
              <v-card-text class="d-flex align-center">
                <v-icon :icon="p.icon" size="30" class="mr-3" />
                <div>
                  <div class="font-weight-medium">{{ p.title }}</div>
                  <div class="text-caption" style="opacity: 0.75">{{ p.desc }}</div>
                  <v-chip size="x-small" variant="tonal" class="mt-1">{{ p.status }}</v-chip>
                </div>
              </v-card-text>
            </v-card>
          </div>
        </v-card-text>
      </v-card>

      <AgentTemplateDialog v-model="templateDialog" />
      <AgentConfigSearchDialog v-model="searchDialog" />
      <AgentCopyConfigDialog v-model="copyDialog" />
    </template>
  </div>
</template>

<style scoped>
.agent-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 14px;
}

.agent-card {
  cursor: pointer;
  transition: transform 0.15s ease;
}

.agent-card:hover {
  transform: translateY(-2px);
}

.empty-tip {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 80px 0;
  color: rgba(var(--v-theme-on-surface), 0.45);
}
</style>
