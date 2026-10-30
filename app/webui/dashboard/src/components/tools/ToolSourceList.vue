<script setup lang="ts">
/**
 * 某来源的工具列表：分组标题 + 工具行 + 空态/错误态。
 *
 * 三个来源页（系统 / 外部 / 插件）共用；OneBot 页因每行还有权限策略，
 * 直接用 ToolRow 自行组合。列表只展示「名称 + 一行描述 + 开关」，
 * 长说明与参数在行内展开，不再显示工具内部 id。
 */
import { computed } from 'vue'
import { useToolsStore, type ToolTab } from '@/stores/tools'
import EmptyState from '@/components/EmptyState.vue'
import ToolRow, { type ToolLike } from './ToolRow.vue'

const props = withDefaults(
  defineProps<{
    groups: { category: string; items: ToolLike[] }[]
    source: ToolTab
    loading?: boolean
    error?: string
    emptyText?: string
    /** 插件工具需要显示「来自哪个插件」 */
    moduleNames?: Record<string, string>
  }>(),
  { loading: false, error: '', emptyText: '还没有工具', moduleNames: () => ({}) },
)

const emit = defineEmits<{
  (e: 'toggle', tool: ToolLike): void
  (e: 'reload'): void
}>()

const toolsStore = useToolsStore()

function isCollapsed(category: string): boolean {
  return !!toolsStore.collapsedOf(props.source)[category]
}

function toggleCategory(category: string): void {
  const next = { ...toolsStore.collapsedOf(props.source), [category]: !isCollapsed(category) }
  toolsStore.setCollapsed(props.source, next)
}

const total = computed(() => props.groups.reduce((sum, group) => sum + group.items.length, 0))
</script>

<template>
  <div>
    <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mb-3">
      <div class="d-flex align-center flex-wrap gap-2">
        <span class="text-caption">{{ error }}</span>
        <v-btn size="small" variant="text" prepend-icon="mdi-refresh" @click="emit('reload')">重试</v-btn>
      </div>
    </v-alert>

    <v-progress-linear v-if="loading" indeterminate color="primary" class="mb-3" />

    <div v-for="group in groups" :key="group.category" class="tool-group">
      <div
        class="tool-category"
        role="button"
        tabindex="0"
        :aria-expanded="!isCollapsed(group.category)"
        @click="toggleCategory(group.category)"
        @keydown.enter.prevent="toggleCategory(group.category)"
        @keydown.space.prevent="toggleCategory(group.category)"
      >
        <v-icon :icon="isCollapsed(group.category) ? 'mdi-chevron-right' : 'mdi-chevron-down'" size="small" />
        <span class="app-section-title">{{ group.category }}</span>
        <span class="tool-category-count">{{ group.items.length }}</span>
      </div>
      <div v-show="!isCollapsed(group.category)" class="tool-list">
        <ToolRow
          v-for="tool in group.items"
          :key="tool.name"
          :tool="tool"
          @toggle="emit('toggle', tool)"
        >
          <template v-if="tool.module" #meta>
            <v-chip size="x-small" variant="tonal" :title="tool.module">
              {{ moduleNames[tool.module] || '插件' }}
            </v-chip>
          </template>
        </ToolRow>
      </div>
    </div>

    <EmptyState
      v-if="!total && !loading && !error"
      icon="mdi-tools"
      :title="emptyText"
      description="需要先在对应来源里开启或连接"
    />
  </div>
</template>

<style scoped>
.tool-group {
  margin-bottom: 6px;
}

.tool-category {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 2px;
  cursor: pointer;
  user-select: none;
}

.tool-category:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
  border-radius: 6px;
}

.tool-category-count {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  background: rgba(var(--v-theme-on-surface), 0.06);
  border-radius: 999px;
  padding: 1px 8px;
}

.tool-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 2px 0 8px;
}
</style>
