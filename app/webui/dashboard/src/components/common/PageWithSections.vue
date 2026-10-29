<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
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

/**
 * 窄屏（≤720px）把左侧固定宽度导航换成顶部横向 chips。
 *
 * 之前左栏写死 200px：390px 宽的手机上内容列只剩 ~138px，字段标题被挤成逐字竖排。
 * 这里只换「呈现方式」，不改变「≥3 组才显示页内导航」的语义（由调用方决定是否渲染本组件）。
 */
const narrowQuery = '(max-width: 720px)'
const isNarrow = ref(false)
let mediaQuery: MediaQueryList | null = null

function syncNarrow(matches: boolean) {
  isNarrow.value = matches
}

function onNarrowChange(event: MediaQueryListEvent) {
  syncNarrow(event.matches)
}

const navPosition = computed<'left' | 'top'>(() => (isNarrow.value ? 'top' : props.position))

onMounted(() => {
  mediaQuery = window.matchMedia(narrowQuery)
  syncNarrow(mediaQuery.matches)
  mediaQuery.addEventListener('change', onNarrowChange)
})

onUnmounted(() => {
  mediaQuery?.removeEventListener('change', onNarrowChange)
  mediaQuery = null
})

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
  <div class="page-with-sections" :class="`is-${navPosition}`" :data-nav-position="navPosition">
    <PageSectionNav
      :sections="props.sections"
      :position="navPosition"
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
  gap: 12px;
}

/* 顶部导航模式：内容必须占满整行，不再被左栏固定宽度挤压 */
.page-with-sections.is-top .page-with-sections__content {
  width: 100%;
}

.page-with-sections__content {
  flex: 1 1 auto;
  min-width: 0;
}

@media (max-width: 720px) {
  .page-with-sections {
    gap: 10px;
  }
}

/* 页内锚点跳到分组时，别让分组标题藏到固定顶栏下面 */
.page-with-sections__content :deep([id^='group-']) {
  scroll-margin-top: 72px;
}
</style>
