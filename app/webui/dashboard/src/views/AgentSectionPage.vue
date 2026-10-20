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
  <AgentConfigSection v-if="section" :section="section">
    <template v-if="section.customFirst && custom" #before>
      <component :is="custom" />
    </template>
    <component :is="custom" v-if="!section.customFirst && custom" />
  </AgentConfigSection>

  <EmptyState
    v-else
    icon="mdi-map-marker-question-outline"
    title="未知的 Agent 分区"
    description="这个地址没有对应的配置分区，可能是链接过期。"
  />
</template>
