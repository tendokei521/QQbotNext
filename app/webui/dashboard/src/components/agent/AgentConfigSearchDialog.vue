<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { splitSchema } from '@/utils/schema'
import { agentSectionForField } from '@/agent/sections'
import HintText from '@/components/common/HintText.vue'
import panelManifest from '@/agent/ai-panel.json'

/** 页面外工作台的字段归属：面板 / 回复与权限（搜索跳转用，见 ai-panel.json）。 */
interface SurfaceTarget {
  title: string
  path: string
  tab?: string
}

const SURFACE_BY_FIELD: Record<string, SurfaceTarget> = {}
for (const card of (panelManifest as any).panel?.cards || []) {
  for (const field of card.fields || []) {
    SURFACE_BY_FIELD[field] = { title: 'AI 对话面板', path: '/ai' }
  }
}
for (const group of (panelManifest as any).reply?.groups || []) {
  for (const field of group.fields || []) {
    SURFACE_BY_FIELD[field] = { title: '回复与权限', path: '/reply', tab: 'who' }
  }
}

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

const agent = useAgentConfigStore()
const router = useRouter()
const query = ref('')

// 目标分区由 registry 解析，不再依赖后端每个字段的 page 元数据
// （分组重划后 page 会和分区不一致，例如 group_session 的字段已迁到「会话管理」）
const fields = computed(() => {
  const { items } = splitSchema(agent.schema)
  return Object.entries(items).map(([key, def]) => {
    const hit = agentSectionForField(key, def?.group || '')
    const surface = SURFACE_BY_FIELD[key]
    return {
      key,
      label: def?.label || '设置项',
      description: def?.description || '',
      sectionTitle: hit?.section.title || surface?.title || '',
      path: hit?.section.path || surface?.path || '',
      groupId: hit?.groupId || '',
      tab: surface?.tab || '',
    }
  })
})

const results = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return fields.value.slice(0, 20)
  return fields.value
    .filter((f) => f.label.toLowerCase().includes(q) || f.key.toLowerCase().includes(q) || f.description.toLowerCase().includes(q))
    .slice(0, 30)
})

/** 结果按所在页面分组：一次搜索会横跨多个页面，先看分组再点，避免同名项点错页 */
const groupedResults = computed(() => {
  const groups: { title: string; items: typeof results.value }[] = []
  for (const field of results.value) {
    const title = field.sectionTitle || '其他设置'
    const group = groups.find((item) => item.title === title)
    if (group) group.items.push(field)
    else groups.push({ title, items: [field] })
  }
  return groups
})

function openField(field: { path: string; groupId: string; tab?: string }) {
  if (!field.path) return
  emit('update:modelValue', false)
  // 用 query 带页内分组/页签：hash 路由下再拼 "#group-xxx" 会被当成路由 hash 的一部分。
  // AgentConfigSection 读到 group 参数后会展开并滚动到对应卡片；回复与权限页读 tab。
  const query: Record<string, string> = {}
  if (field.groupId) query.group = field.groupId
  if (field.tab) query.tab = field.tab
  router.push({ path: field.path, query })
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) query.value = ''
  },
)
</script>

<template>
  <v-dialog :model-value="props.modelValue" max-width="560" @update:model-value="emit('update:modelValue', $event)">
    <v-card>
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-magnify" class="mr-2" color="primary" /> 搜索配置项
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" size="small" @click="emit('update:modelValue', false)" />
      </v-card-title>
      <v-card-text>
        <v-text-field
          v-model="query"
          label="输入关键词，如「上下文轮数」"
          prepend-inner-icon="mdi-magnify"
          density="comfortable"
          variant="outlined"
          hide-details
          autofocus
        />
        <HintText class="mt-2" text="结果按所在页面分组，点一条直接跳过去。" />
        <v-list density="compact" class="mt-2" max-height="400" style="overflow-y: auto">
          <template v-for="group in groupedResults" :key="group.title">
            <v-list-subheader>{{ group.title }}</v-list-subheader>
            <v-list-item v-for="f in group.items" :key="f.key" @click="openField(f)">
              <v-list-item-title>{{ f.label }}</v-list-item-title>
              <v-list-item-subtitle v-if="f.description" class="text-caption">
                {{ f.description }}
              </v-list-item-subtitle>
            </v-list-item>
          </template>
          <v-list-item v-if="!results.length">
            <v-list-item-title class="text-caption text-center py-3" style="opacity: 0.5">
              未找到匹配配置项
            </v-list-item-title>
          </v-list-item>
        </v-list>
      </v-card-text>
    </v-card>
  </v-dialog>
</template>
