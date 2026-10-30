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
import { displayName, useBotsStore } from '@/stores/bots'
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
// 账号作用域提示：这一整片配置是按「当前账号」保存的，页面内要能看见
const accountLabel = computed(() => {
  const bot = bots.bots.find((item) => item.id === bots.currentAccountId)
  return bot ? displayName(bot) : ''
})
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
    <div class="app-page-header" style="align-items: center">
      <div class="d-flex align-center gap-2 flex-wrap">
        <v-btn
          v-if="section"
          size="small"
          variant="text"
          prepend-icon="mdi-arrow-left"
          class="mr-1"
          @click="router.push(AGENT_INDEX_PATH)"
        >
          返回 AI 对话面板
        </v-btn>
        <h1 class="app-page-title">
          <v-icon v-if="section" :icon="section.icon" size="28" class="mr-1" :color="section.color" />
          {{ section ? section.title : panelTitle }}
        </h1>
        <v-chip v-if="accountLabel" size="small" variant="tonal" prepend-icon="mdi-account">
          当前账号：{{ accountLabel }}
        </v-chip>
      </div>
      <div class="d-flex align-center flex-wrap gap-2">
        <!--
          AI 回复总开关在「AI 对话面板」里维护（高频操作台），不在任何一个深度设置页；
          头部只保留只读状态，让人一眼知道当前是开还是关。
        -->
        <v-chip
          size="small"
          variant="flat"
          :color="agent.enabled ? 'success' : 'grey'"
          :prepend-icon="agent.enabled ? 'mdi-check-circle-outline' : 'mdi-pause-circle-outline'"
        >
          {{ agent.enabled ? 'AI 回复已开启' : 'AI 回复已关闭' }}
        </v-chip>
        <!-- 保存状态与动作统一走共享状态条（自动保存，成功后回到「已保存」） -->
        <SaveStatusBar
          v-if="hasAccount && agent.loadedFor !== null"
          :status="agent.saveStatus"
          autosave
        />
      </div>
    </div>
    <div class="app-page-subtitle">
      {{ section ? section.subtitle : '回复时序、上下文与高级能力' }}
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
      <div class="text-caption">
        当前账号未连接：<strong>配置照常可编辑并保存</strong>，连接后才会收发消息、
        运行定时任务与主动消息。
      </div>
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

    <!--
      一个账号都没有：**不再整页挡住**。
      人格是全局资产（预设库），没有账号也该能编辑；只有"按账号保存"的字段没法落库，
      所以这里给警告 + 保留编辑能力，而不是把整个人格设定区块锁死。
    -->
    <v-alert v-if="!hasAccount" type="warning" variant="tonal" density="compact" class="mb-3">
      <div class="d-flex align-center flex-wrap gap-2">
        <span class="text-caption">
          未添加账号：<strong>人格预设可以照常编辑</strong>（存在全局预设库里，与账号无关）；
          其余按账号保存的配置改完<strong>无法保存</strong>——先在「账号管理」添加账号。
        </span>
        <v-btn size="small" variant="text" prepend-icon="mdi-plus" @click="router.push('/bots')">去添加账号</v-btn>
        <v-btn size="small" variant="text" prepend-icon="mdi-account-heart-outline" @click="router.push('/personas')">
          编辑人格预设
        </v-btn>
      </div>
    </v-alert>

    <router-view />
  </div>
</template>
