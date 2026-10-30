<script setup lang="ts">
/**
 * Agent 配置区共享外壳。
 *
 * 此前 10 个子页面各自重复：AgentSubPage 包装 + title/subtitle/icon/color props +
 * ``onMounted(agent.load())`` + ``watch(botId → agent.load(true))`` + 空态与 loading。
 * 这里统一收口，子页面只负责自己的内容。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AGENT_INDEX_PATH, agentSectionByPath } from '@/agent/sections'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'
import SaveStatusBar from '@/components/common/SaveStatusBar.vue'

const route = useRoute()
const router = useRouter()
const bots = useBotsStore()
const agent = useAgentConfigStore()

// 当前路由对应的分区（AI 设置只有 3 个深度设置页）
const section = computed(() => agentSectionByPath(route.path))
const panelTitle = 'AI 设置'
const botId = computed(() => bots.currentAccountId)
/** 是否已选定账号：有账号就够，**不要求连上**（配置按账号存，与连接无关） */
const hasAccount = computed(() => botId.value !== null)
const isConnected = computed(() => bots.currentBot?.status === 'connected')
const booting = ref(false)

async function ensureLoaded() {
  if (botId.value === null) {
    booting.value = true
    await bots.fetchBots()
    if (bots.currentId === null && bots.bots.length) bots.restoreSelection()
    booting.value = false
  }
  await agent.load()
}

onMounted(ensureLoaded)
watch(
  () => bots.currentAccountId,
  () => agent.load(true),
)
</script>

<template>
  <div>
    <div class="app-page-header">
      <div class="d-flex align-center gap-2 flex-wrap">
        <v-btn
          v-if="section"
          icon="mdi-arrow-left"
          variant="text"
          size="small"
          title="返回 AI 对话面板"
          @click="router.push(AGENT_INDEX_PATH)"
        />
        <div>
          <h1 class="app-page-title">
            {{ section ? section.title : panelTitle }}
          </h1>
          <div class="app-page-subtitle">
            {{ section ? section.subtitle : '回复时序、上下文与高级能力' }}
          </div>
        </div>
      </div>
      <!-- 头部只放保存状态（清态不显示），不再重复 AI 开关/账号 chip -->
      <SaveStatusBar
        v-if="hasAccount && agent.loadedFor !== null"
        :status="agent.saveStatus"
        autosave
      />
    </div>

    <v-progress-linear v-if="agent.loading || booting" indeterminate color="primary" class="mb-3" />

    <!-- 离线提示：账号存在（有 id）就能编辑配置，只是收发不了消息 -->
    <v-alert
      v-if="hasAccount && !isConnected"
      type="info"
      variant="tonal"
      density="compact"
      class="mb-3"
    >
      <div class="text-caption">当前账号未连接：配置可编辑，连接后才收发消息。</div>
    </v-alert>

    <!-- 加载失败（不是"没账号"）：给原因 + 重试，而不是红条 toast -->
    <v-alert
      v-if="hasAccount && agent.loadError"
      type="warning"
      variant="tonal"
      density="compact"
      class="mb-3"
    >
      <div class="d-flex align-center flex-wrap gap-2">
        <span class="text-caption">配置加载失败：{{ agent.loadError }}</span>
        <v-btn size="small" variant="text" prepend-icon="mdi-refresh" @click="ensureLoaded">重试</v-btn>
      </div>
    </v-alert>

    <!-- 一个账号都没有：人格预设是全局资产，仍可编辑；账号级配置无法保存 -->
    <v-alert v-if="!hasAccount" type="warning" variant="tonal" density="compact" class="mb-3">
      <div class="d-flex align-center flex-wrap gap-2">
        <span class="text-caption">未添加账号：配置无法保存；人格预设不受影响。</span>
        <v-btn size="small" variant="text" prepend-icon="mdi-plus" @click="router.push('/bots')">去添加账号</v-btn>
        <v-btn size="small" variant="text" prepend-icon="mdi-account-heart-outline" @click="router.push('/personas')">
          编辑人格预设
        </v-btn>
      </div>
    </v-alert>

    <router-view />
  </div>
</template>
