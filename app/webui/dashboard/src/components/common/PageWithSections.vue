<script setup lang="ts">
import { ref } from 'vue'
import PageSectionNav, { type SectionItem } from './PageSectionNav.vue'

const props = withDefaults(
  defineProps<{
    sections: SectionItem[]
    position?: 'left' | 'top'
    /** 页内导航小标题（透传给 PageSectionNav）；空串 = 不显示 */
    navTitle?: string
  }>(),
  {
    position: 'left',
    navTitle: '',
  },
)

const activeId = ref('')

const emit = defineEmits<{ (e: 'select', id: string): void }>()

function scrollToId(id: string) {
  const el = document.getElementById(id)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function onSelect(id: string) {
  activeId.value = id
  emit('select', id)
  scrollToId(id)
}
</script>

<template>
  <div class="page-with-sections" :class="`is-${props.position}`">
    <PageSectionNav
      :sections="props.sections"
      :position="props.position"
      :active="activeId"
      :title="props.navTitle"
      @select="onSelect"
    />
    <div class="page-with-sections__content">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.page-with-sections {
  display: flex;
  gap: 20px;
  align-items: flex-start;
}

.page-with-sections.is-top {
  flex-direction: column;
}

.page-with-sections__content {
  flex: 1 1 auto;
  min-width: 0;
}
</style>
