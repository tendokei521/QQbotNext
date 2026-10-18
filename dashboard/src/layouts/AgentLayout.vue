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
import EmptyState from '@/components/EmptyState.vue'
import { AGENT_INDEX_PATH, agentSectionByPath } from '@/agent/sections'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'

const route = useRoute()
const router = useRouter()
const bots = useBotsStore()
const agent = useAgentConfigStore()

// 当前路由对应的分区（index 路由没有分区）
const section = computed(() => agentSectionByPath(route.path))
const botId = computed(() => bots.currentBot?.bot_id ?? null)
const booting = ref(false)

async function ensureLoaded() {
  if (botId.value === null) {
    booting.value = true
    await bots.fetchBots()
    if (bots.currentIndex === null && bots.bots.length) bots.restoreSelection()
    booting.value = false
  }
  await agent.load()
}

onMounted(ensureLoaded)
watch(
  () => bots.currentBot?.bot_id,
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
          返回 Agent 面板
        </v-btn>
        <h1 class="app-page-title">
          <v-icon v-if="section" :icon="section.icon" size="28" class="mr-1" :color="section.color" />
          {{ section ? section.title : 'Agent 面板' }}
        </h1>
        <v-chip v-if="!section" size="small" variant="tonal" color="primary">LLM · 框架级</v-chip>
        <v-chip v-if="agent.saveStatus === 'saving'" size="small" color="primary" variant="flat">
          <v-progress-circular size="12" indeterminate class="mr-1" /> 保存中…
        </v-chip>
        <v-chip v-else-if="agent.saveStatus === 'error'" size="small" color="error" variant="flat">保存失败</v-chip>
        <v-chip v-else-if="agent.saveStatus === 'dirty'" size="small" color="warning" variant="flat">未保存</v-chip>
      </div>
      <div class="d-flex align-center gap-2">
        <v-switch
          v-if="!section"
          v-model="agent.enabled"
          color="primary"
          label="启用 Agent"
          density="compact"
          hide-details
          @update:model-value="(v: any) => agent.onEnabledChange(v)"
        />
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
      {{ section ? section.subtitle : '框架级 LLM Agent：配置、权限、定时任务与主动消息' }}
    </div>

    <v-progress-linear v-if="agent.loading || booting" indeterminate color="primary" class="mb-3" />

    <EmptyState
      v-if="botId === null"
      icon="mdi-robot-off-outline"
      title="请先选择并连接一个 Bot"
      description="Agent 配置按 Bot 独立保存，需要先选定目标 Bot 才能编辑。"
    >
      <v-btn variant="tonal" prepend-icon="mdi-refresh" @click="ensureLoaded">重试</v-btn>
    </EmptyState>

    <router-view v-else />
  </div>
</template>
