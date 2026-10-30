<script setup lang="ts">
/**
 * 通用分区的页面实现。
 *
 * 一个组件承载所有「区域为 agent」的分区：分区元数据来自 @/agent/sections，
 * 字段来自后端 schema（按分区选择器过滤），定制 UI 来自 @/agent/customSections。
 * 因此新增分区只需要在 sections.json 里加一条，**不需要新增页面文件**。
 */
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import EmptyState from '@/components/EmptyState.vue'
import AgentConfigSection from '@/components/agent/AgentConfigSection.vue'
import { agentCustomSection } from '@/agent/customSections'
import { agentSectionByPath } from '@/agent/sections'

const route = useRoute()

const section = computed(() => agentSectionByPath(route.path))
const custom = computed(() => (section.value ? agentCustomSection(section.value.id) : null))
</script>

<template>
  <div class="agent-section-page">
    <AgentConfigSection v-if="section" :section="section">
      <template v-if="section.customFirst && custom" #before>
        <component :is="custom" />
      </template>
      <component :is="custom" v-if="!section.customFirst && custom" />
    </AgentConfigSection>

    <EmptyState
      v-else
      icon="mdi-map-marker-question-outline"
      title="未知的 AI 设置页"
      description="这个地址没有对应的设置页，可能是链接过期。"
    />
  </div>
</template>

<style scoped>
/*
 * 页内锚点滚动留白：锚点会被固定顶栏（56px）挡住，scrollIntoView 时补上这段偏移。
 * 只在有页内分组（id 前缀 group-）的 AI 设置页生效，不影响其它页面。
 */
.agent-section-page :deep([id^='group-']) {
  scroll-margin-top: 88px;
}
</style>
