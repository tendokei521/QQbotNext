<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { splitSchema } from '@/utils/schema'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

const agent = useAgentConfigStore()
const router = useRouter()
const query = ref('')

const fields = computed(() => {
  const { items } = splitSchema(agent.schema)
  return Object.entries(items)
    .map(([key, def]) => ({
      key,
      label: def?.label || key,
      description: def?.description || '',
      page: def?.page || 'basic',
      group: def?.group || '',
    }))
})

const results = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return fields.value.slice(0, 20)
  return fields.value
    .filter((f) => f.label.toLowerCase().includes(q) || f.key.toLowerCase().includes(q) || f.description.toLowerCase().includes(q))
    .slice(0, 30)
})

function openField(field: { page: string; group: string }) {
  emit('update:modelValue', false)
  router.push(`/agent/${field.page}`)
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
        <v-icon icon="mdi-magnify" class="mr-2" color="primary" /> 配置字段搜索
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" size="small" @click="emit('update:modelValue', false)" />
      </v-card-title>
      <v-card-text>
        <v-text-field
          v-model="query"
          label="输入字段名或描述"
          prepend-inner-icon="mdi-magnify"
          density="comfortable"
          variant="outlined"
          hide-details
          autofocus
        />
        <v-list density="compact" class="mt-3" max-height="400" style="overflow-y: auto">
          <v-list-item v-for="f in results" :key="f.key" @click="openField(f)">
            <v-list-item-title>{{ f.label }}</v-list-item-title>
            <v-list-item-subtitle>{{ f.key }} · {{ f.description }}</v-list-item-subtitle>
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
