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

const route = useRoute()
const router = useRouter()
const bots = useBotsStore()
const agent = useAgentConfigStore()

// 当前路由对应的分区（index 路由没有分区）
const section = computed(() => agentSectionByPath(route.path))
const panelTitle = 'AI 对话设置'
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
          返回 {{ panelTitle }}
        </v-btn>
        <h1 class="app-page-title">
          <v-icon v-if="section" :icon="section.icon" size="28" class="mr-1" :color="section.color" />
          {{ section ? section.title : panelTitle }}
        </h1>
        <v-chip v-if="!section" size="small" variant="tonal" color="primary">LLM · 框架级</v-chip>
        <v-chip v-if="agent.saveStatus === 'saving'" size="small" color="primary" variant="flat">
          <v-progress-circular size="12" indeterminate class="mr-1" /> 保存中…
        </v-chip>
        <v-chip v-else-if="agent.saveStatus === 'error'" size="small" color="error" variant="flat">保存失败</v-chip>
        <v-chip v-else-if="agent.saveStatus === 'dirty'" size="small" color="warning" variant="flat">未保存</v-chip>
        <v-chip v-else-if="agent.saveStatus === 'blocked'" size="small" color="grey" variant="flat">无账号·不保存</v-chip>
      </div>
      <div class="d-flex align-center gap-2">
        <!--
          Agent 启用开关移到「基础配置」页：它是整个 Agent 的总闸，不属于任何一个具体
          配置页的头部；放在这里会让每个配置页都出现一个"看起来只影响本页"的开关。
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
        <v-btn
          size="small"
          color="primary"
          variant="tonal"
          prepend-icon="mdi-content-save"
          :loading="agent.saveStatus === 'saving'"
          @click="agent.save()"
        >
          保存配置
        </v-btn>
      </div>
    </div>
    <div class="app-page-subtitle">
      {{ section ? section.subtitle : '模型、人格、权限与对话行为' }}
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
          其余按账号保存的 Agent 配置改完<strong>不会落库</strong>——先在「账号管理」添加账号即可保存。
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
