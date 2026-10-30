<script setup lang="ts">
/**
 * 渲染某个分区的 schema 配置表单。
 *
 * 页内分组只在 ≥3 组时出现：用一排紧凑的分段按钮切换（不再用左侧列表，
 * 避免和全局侧栏的「AI 设置」导航重复）。点击分组同时展开对应面板并滚到位置。
 */
import { computed, nextTick, ref, watch } from 'vue'
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

const activeGroup = ref<string | null>(null)

async function onSelectSection(domId: string) {
  activeSectionId.value = domId
  const groupId = domId.replace(/^group-/, '')
  // activeGroup 的 watcher 只在值变化时触发；先置空再设回，
  // 保证「面板被手动收起后再点同一个分组」仍然会重新展开。
  activeGroup.value = null
  await nextTick()
  activeGroup.value = groupId
  // 等面板展开后布局才最终确定，再滚动才准
  await nextTick()
  document.getElementById(domId)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// 切换分区时清空，避免上一个分区的分组 id 误展开本页同名面板
watch(
  () => props.section.id,
  () => {
    activeGroup.value = null
  },
)

// 配置搜索跳转带来 ?group=xxx 时展开并滚动到对应卡片
watch(
  () => route.query.group,
  async (groupId) => {
    if (typeof groupId !== 'string' || !groupId) return
    await onSelectSection(configGroupDomId(groupId))
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
          :active-group="activeGroup"
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
 * 抹掉 ConfigForm 内部的分组面板描边与每字段的描边框，用分隔线代替。
 */
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
  gap: 0;
  padding: 0;
}

.agent-config-card :deep(.config-item) {
  padding: 14px 0;
  border: 0;
  border-radius: 0;
  background: transparent;
}

.agent-config-card :deep(.config-item + .config-item) {
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.agent-config-card :deep(.flat-fields) {
  gap: 0;
}
</style>
