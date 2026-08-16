<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  modelValue: string[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string[]): void
}>()

const items = ref<string[]>([...(props.modelValue ?? [])])

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
  emit('update:modelValue', items.value.map((s) => s.trim()).filter(Boolean))
}
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
