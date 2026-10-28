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
import { AGENT_SECTIONS, agentSectionBadge } from '@/agent/sections'

const agent = useAgentConfigStore()
const router = useRouter()

const templateDialog = ref(false)
const searchDialog = ref(false)
const copyDialog = ref(false)

/*
 * 这里原来还有「只看基础」开关 + 每张卡的「高级」徽标，以及一个 /advanced 进阶区。
 * v4 把进阶内容拆成功能插件后两者同时失效：徽标按「分区内全部字段都是 basic 才算基础」
 * 判定，而 importance 已是字段级，几乎每页都至少有一个进阶字段 → 6 张卡 5 张标「高级」。
 * 现在只有单一区域，卡片就是这 5 页本身。
 */
const pages = computed(() =>
  AGENT_SECTIONS.map((section) => ({
    to: section.path,
    title: section.title,
    desc: section.subtitle,
    icon: section.icon,
    color: section.color,
    status: agentSectionBadge(section.id, {
      draft: agent.draft,
      poolModelIds: agent.poolModelIds,
      permission: agent.permission,
    }),
  })),
)

/**
 * 健康检查只读**当前 schema 里真实存在**的键。
 *
 * 历史教训：这里曾检查 `memory_enable` / `knowledge_enable` / `experimental_long_term_memory`，
 * 而它们 v4 已分别迁进 llm_memory / llm_knowledge 模块、或直接删除，
 * 于是警告永远不触发（读 undefined）。新增检查前请确认键仍在 config_partition.CORE_KEYS 里。
 */
const warnings = computed(() => {
  const list: string[] = []
  if (!agent.enabled) list.push('AI 回复已关闭，不会自动回消息（功能插件的回复不受影响）')
  if (!agent.poolModelIds.length) list.push('还没选模型，机器人无法回复')
  if (agent.draft.tavily_enable && !agent.draft.tavily_api_key) {
    list.push('联网搜索已开启，但未填写 Tavily API Key')
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
                </div>
                <div class="text-caption" style="opacity: 0.75">{{ p.desc }}</div>
                <v-chip size="x-small" variant="tonal" class="mt-1">{{ p.status }}</v-chip>
              </div>
            </v-card-text>
          </v-card>
        </div>

        <v-divider class="my-4" />
        <div class="d-flex align-center">
          <v-icon icon="mdi-cube-outline" size="small" class="mr-2" />
          <div class="text-body-2">
            工具能力、主动消息、流式输出、记忆与知识库是独立的
            <a class="text-primary text-decoration-none" href="#" @click.prevent="router.push('/modules')">
              功能插件
            </a>
            ，在各模块的配置页里调整。
          </div>
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
