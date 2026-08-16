<script setup lang="ts">
import { ref, watch } from 'vue'

const props = defineProps<{
  modelValue: string[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string[]): void
}>()

const items = ref<string[]>([...(props.modelValue ?? [])])
// 用户一旦编辑就不再被外部快照覆盖，避免输入过程中被重置
const touched = ref(false)

function add() {
  items.value.push('')
  emitChange()
}

function remove(i: number) {
  items.value.splice(i, 1)
  emitChange()
}

function onInput(i: number, v: string) {
  items.value[i] = v
  emitChange()
}

function emitChange() {
  touched.value = true
  emit('update:modelValue', items.value.map((s) => s.trim()).filter(Boolean))
}

// 外部配置（如切账号 / WS 同步）到达时，若用户尚未编辑则同步渲染
watch(
  () => props.modelValue,
  (val) => {
    if (!touched.value) items.value = [...(val ?? [])]
  },
  { deep: true },
)
</script>

<template>
  <div class="string-list">
    <div v-for="(item, i) in items" :key="i" class="string-row">
      <v-text-field
        :model-value="item"
        density="comfortable"
        variant="outlined"
        hide-details
        placeholder="输入一项"
        @update:model-value="(v: string) => onInput(i, v)"
      />
      <v-btn variant="text" icon="mdi-close" color="error" size="small" @click="remove(i)" />
    </div>
    <v-btn size="small" variant="tonal" prepend-icon="mdi-plus" class="mt-1" @click="add">添加</v-btn>
  </div>
</template>

<style scoped>
.string-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.string-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
</style>
