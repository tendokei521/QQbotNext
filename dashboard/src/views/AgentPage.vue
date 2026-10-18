<script setup lang="ts">
/**
 * Agent 概览（index）。
 *
 * 头部 / bot 守卫 / loading / 保存按钮由 ``AgentLayout`` 统一提供，
 * 本页只负责健康警告、分区入口卡片与三个配置对话框。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAgentConfigStore } from '@/stores/agentConfig'
import AgentTemplateDialog from '@/components/agent/AgentTemplateDialog.vue'
import AgentConfigSearchDialog from '@/components/agent/AgentConfigSearchDialog.vue'
import AgentCopyConfigDialog from '@/components/agent/AgentCopyConfigDialog.vue'
import { AGENT_SECTIONS, agentSectionBadge, agentSectionImportance } from '@/agent/sections'

const agent = useAgentConfigStore()
const router = useRouter()

const templateDialog = ref(false)
const searchDialog = ref(false)
const copyDialog = ref(false)
// 新手只需要 basic 档分区；进阶配置默认收起
const onlyBasic = ref(false)

const pages = computed(() => {
  const list = AGENT_SECTIONS.map((section) => ({
    to: section.path,
    title: section.title,
    desc: section.subtitle,
    icon: section.icon,
    color: section.color,
    advanced: agentSectionImportance(section, agent.schema) === 'advanced',
    status: agentSectionBadge(section.id, {
      draft: agent.draft,
      poolModelIds: agent.poolModelIds,
      permission: agent.permission,
    }),
  }))
  return onlyBasic.value ? list.filter((p) => !p.advanced) : list
})

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
</script>

<template>
  <div>
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
        <v-switch
          v-model="onlyBasic"
          color="primary"
          label="只看基础"
          density="compact"
          hide-details
          class="mr-3"
        />
        <v-btn size="small" variant="tonal" prepend-icon="mdi-book-multiple" class="mr-3" @click="templateDialog = true">
          配置模板
        </v-btn>
        <v-btn size="small" variant="tonal" prepend-icon="mdi-magnify" class="mr-3" @click="searchDialog = true">
          搜索配置
        </v-btn>
        <v-btn size="small" variant="tonal" prepend-icon="mdi-content-copy" class="mr-3" @click="copyDialog = true">
          复制配置
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
                <div class="font-weight-medium d-flex align-center gap-2">
                  {{ p.title }}
                  <v-chip v-if="p.advanced" size="x-small" variant="text" color="secondary">高级</v-chip>
                </div>
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
</style>
