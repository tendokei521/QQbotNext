<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'

export interface SectionItem {
  id: string
  label: string
  icon?: string
  disabled?: boolean
}

const props = withDefaults(
  defineProps<{
    sections: SectionItem[]
    active?: string
    position?: 'left' | 'top'
  }>(),
  {
    active: '',
    position: 'left',
  },
)

const emit = defineEmits<{ (e: 'select', id: string): void }>()

const activeId = ref(props.active || props.sections[0]?.id || '')

function scrollSpy() {
  const anchor = 120
  let current = ''
  for (const s of props.sections) {
    const el = document.getElementById(s.id)
    if (!el) continue
    const rect = el.getBoundingClientRect()
    if (rect.top <= anchor && rect.bottom >= anchor) {
      current = s.id
      break
    }
  }
  if (!current) {
    for (const s of props.sections) {
      const el = document.getElementById(s.id)
      if (!el) continue
      const rect = el.getBoundingClientRect()
      if (rect.top <= anchor) current = s.id
    }
  }
  if (!current && props.sections.length) current = props.sections[0].id
  activeId.value = current
}

function onSelect(item: SectionItem) {
  if (item.disabled) return
  activeId.value = item.id
  emit('select', item.id)
}

function onScroll() {
  requestAnimationFrame(scrollSpy)
}

watch(
  () => props.active,
  (v) => {
    if (v) activeId.value = v
  },
)

watch(
  () => props.sections,
  () => scrollSpy(),
  { deep: true },
)

onMounted(() => {
  scrollSpy()
  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('resize', onScroll, { passive: true })
})

onUnmounted(() => {
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('resize', onScroll)
})
</script>

<template>
  <nav class="page-section-nav" :class="`is-${position}`">
    <div
      v-for="item in sections"
      :key="item.id"
      class="section-item"
      :class="{ 'is-active': activeId === item.id, 'is-disabled': item.disabled }"
      @click="onSelect(item)"
    >
      <v-icon v-if="item.icon" size="small" class="mr-1">{{ item.icon }}</v-icon>
      <span>{{ item.label }}</span>
    </div>
  </nav>
</template>

<style scoped>
.page-section-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  position: sticky;
  top: 72px;
  align-self: flex-start;
  width: 200px;
  flex-shrink: 0;
}

.is-top {
  flex-direction: row;
  flex-wrap: wrap;
  width: auto;
  position: static;
}

.section-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  border-radius: 8px;
  cursor: pointer;
  color: rgba(var(--v-theme-on-surface), 0.7);
  font-size: 13px;
  transition:
    background-color 0.15s ease,
    color 0.15s ease;
}

.section-item:hover {
  background: rgba(var(--v-theme-primary), 0.08);
}

.section-item.is-active {
  background: linear-gradient(
    90deg,
    rgba(var(--v-theme-primary), 0.16),
    rgba(var(--v-theme-primary), 0.05)
  );
  color: rgb(var(--v-theme-primary));
  font-weight: 600;
}

.section-item.is-disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>
