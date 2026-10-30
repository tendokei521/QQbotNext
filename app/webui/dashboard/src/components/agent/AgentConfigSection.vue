<script setup lang="ts">
/**
 * 渲染某个分区的 schema 配置表单。
 *
 * 页内分组只在 ≥3 组时出现：顶部一排紧凑分段按钮，点击即切换当前分组
 * （单分组渲染，不再把所有分组纵向堆叠；也不再和全局侧栏的「AI 设置」导航重复）。
 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ConfigForm from '@/components/config/ConfigForm.vue'
import type { SectionItem } from '@/components/common/PageSectionNav.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { configGroupDomId, splitSchema } from '@/utils/schema'
import { agentSectionSchema, type AgentSection } from '@/agent/sections'

const props = defineProps<{ section: AgentSection }>()

const agent = useAgentConfigStore()
const route = useRoute()
const router = useRouter()

// 页内分组结构来自 registry（不是后端 group）：标题、顺序、字段归属都由
// sections.json 决定，后端 group 在这里已被重写成本页的子分组 id。
const schema = computed(() => agentSectionSchema(props.section, agent.schema))

const navItems = computed<SectionItem[]>(() => {
  const { groups } = splitSchema(schema.value)
  return Object.entries(groups).map(([id, def]: [string, any]) => ({
    id: configGroupDomId(id),
    label: def?.label || id,
    icon: def?.icon || 'mdi-circle-small',
  }))
})

/** ≤2 组时不显示页内切换（和侧栏并列会像两层菜单）；≥3 组才值得跳转 */
const hasNav = computed(() => navItems.value.length >= 3)

const activeSectionId = ref('')

watch(
  navItems,
  (items) => {
    if (!items.some((item) => item.id === activeSectionId.value)) {
      activeSectionId.value = items[0]?.id || ''
    }
  },
  { immediate: true },
)

/** 当前 tab 对应的 schema 分组 id（去 DOM 前缀）；单分组渲染时传给 ConfigForm */
const activeGroupId = computed(() => activeSectionId.value.replace(/^group-/, ''))

function onSelectSection(domId: string) {
  // tab 即页面：切换分组时只渲染该组字段，不再把所有分组纵向堆叠
  activeSectionId.value = domId
  // 换组后回到页头，避免停在上一组中间位置
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

// 配置搜索跳转带来 ?group=xxx 时切到对应分组
watch(
  () => route.query.group,
  (groupId) => {
    if (typeof groupId !== 'string' || !groupId) return
    onSelectSection(configGroupDomId(groupId))
  },
  { immediate: true },
)
</script>

<template>
  <div class="agent-config">
    <!-- 对话行为的合并等待/冷却/打断可被账号或单群规则覆盖：给一句提示即可 -->
    <v-alert v-if="props.section.id === 'behavior'" type="info" variant="tonal" density="compact" class="mb-3">
      <div class="d-flex align-center flex-wrap gap-2">
        <span class="text-caption">账号或单群规则可覆盖这些值</span>
        <v-btn size="small" variant="text" prepend-icon="mdi-arrow-right" @click="router.push('/reply?tab=rules')">
          去调整
        </v-btn>
      </div>
    </v-alert>

    <!-- 页内分组：紧凑分段按钮（与左侧 AI 设置导航区分） -->
    <div v-if="hasNav" class="app-toolbar section-tabs" role="tablist" aria-label="本页分组">
      <v-btn
        v-for="item in navItems"
        :key="item.id"
        size="small"
        class="section-tab"
        :variant="activeSectionId === item.id ? 'tonal' : 'text'"
        :color="activeSectionId === item.id ? 'primary' : undefined"
        :aria-selected="activeSectionId === item.id"
        @click="onSelectSection(item.id)"
      >
        <v-icon :icon="item.icon" size="small" start />
        {{ item.label }}
      </v-btn>
    </div>

    <slot name="before" />
    <v-card v-if="navItems.length" variant="outlined" class="agent-config-card">
      <v-card-text>
        <ConfigForm
          :module-name="'agent'"
          :schema="schema"
          :config="agent.draft"
          :bot-id="agent.botId"
          :only-group="hasNav ? activeGroupId : null"
          @change="agent.onChange"
        />
      </v-card-text>
    </v-card>
    <slot />
  </div>
</template>

<style scoped>
.section-tabs {
  gap: 4px;
  margin-bottom: 12px;
}

.section-tab {
  text-transform: none;
  letter-spacing: 0;
}

/*
 * AI 设置页只保留「卡片 > 字段」一层：
 * 抹掉 ConfigForm 内部的分组面板描边与每字段的描边框。
 * 卡片内留出和其他页一致的呼吸位，避免字段文字贴到左侧/顶部边线（用户反馈「顶格」）。
 */
.agent-config-card :deep(.v-card-text) {
  padding: 18px 24px 22px;
}

.agent-config-card :deep(.v-expansion-panels) {
  box-shadow: none !important;
  background: transparent !important;
  border-radius: 0;
}

.agent-config-card :deep(.v-expansion-panel) {
  box-shadow: none !important;
  background: transparent !important;
  border-radius: 0;
}

.agent-config-card :deep(.v-expansion-panel-title) {
  min-height: 40px;
  padding: 8px 0;
  font-size: 14px;
  font-weight: 600;
}

.agent-config-card :deep(.v-expansion-panel-title__overlay) {
  display: none;
}

.agent-config-card :deep(.v-expansion-panel-text__wrapper) {
  padding: 0 0 10px;
}

.agent-config-card :deep(.v-expansion-panels + .v-expansion-panels) {
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.agent-config-card :deep(.group-body) {
  padding: 0;
}

.agent-config-card :deep(.config-item) {
  padding: 12px 0;
  border: 0;
  border-radius: 0;
  background: transparent;
}

.agent-config-card :deep(.flat-fields) {
  gap: 0;
}
</style>
