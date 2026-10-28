<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { splitSchema } from '@/utils/schema'
import { agentSectionForField } from '@/agent/sections'

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
    return {
      key,
      label: def?.label || key,
      description: def?.description || '',
      sectionTitle: hit?.section.title || '',
      path: hit?.section.path || '',
      groupId: hit?.groupId || '',
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

function openField(field: { path: string; groupId: string }) {
  if (!field.path) return
  emit('update:modelValue', false)
  // 用 query 带页内分组：hash 路由下再拼 "#group-xxx" 会被当成路由 hash 的一部分。
  // AgentConfigSection 读到 group 参数后会展开并滚动到对应卡片。
  router.push({ path: field.path, query: field.groupId ? { group: field.groupId } : {} })
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
        <v-list density="compact" class="mt-3" max-height="400" style="overflow-y: auto">
          <v-list-item v-for="f in results" :key="f.key" @click="openField(f)">
            <v-list-item-title>{{ f.label }}</v-list-item-title>
            <v-list-item-subtitle>{{ f.sectionTitle ? `位于「${f.sectionTitle}」` : '配置项' }}</v-list-item-subtitle>
          </v-list-item>
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
