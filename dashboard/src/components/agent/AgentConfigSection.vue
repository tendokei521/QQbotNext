<script setup lang="ts">
/**
 * 渲染某个分区的 schema 配置表单。
 *
 * 分区含多个分组时，左侧出现分组二级导航（PageSectionNav，粘性 + scroll-spy）；
 * 点击导航除了滚动到锚点，还会通过 ConfigForm 的 activeGroup 强制展开对应面板。
 * 只有一个分组时不渲染导航，避免给单卡片页面多加一条侧栏。
 */
import { computed, nextTick, ref, watch } from 'vue'
import ConfigForm from '@/components/config/ConfigForm.vue'
import PageWithSections from '@/components/common/PageWithSections.vue'
import type { SectionItem } from '@/components/common/PageSectionNav.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { configGroupDomId, filterSchemaBySection, splitSchema } from '@/utils/schema'
import { agentGroupIcon, agentSectionSelector, type AgentSection } from '@/agent/sections'

const props = defineProps<{ section: AgentSection }>()

const agent = useAgentConfigStore()

const schema = computed(() => filterSchemaBySection(agent.schema, agentSectionSelector(props.section)))

const navItems = computed<SectionItem[]>(() => {
  const { groups } = splitSchema(schema.value)
  return Object.entries(groups).map(([id, def]: [string, any]) => ({
    id: configGroupDomId(id),
    label: def?.label || id,
    icon: agentGroupIcon(id),
  }))
})

const showNav = computed(() => navItems.value.length > 1)

const activeGroup = ref<string | null>(null)

function onSelectSection(domId: string) {
  const groupId = domId.replace(/^group-/, '')
  // activeGroup 的 watcher 只在值变化时触发；先置空再设回，
  // 保证「面板被手动收起后再点同一个导航项」仍然会重新展开。
  activeGroup.value = null
  nextTick(() => {
    activeGroup.value = groupId
  })
}

// 切换分区时清空，避免上一个分区的分组 id 误展开本页同名面板
watch(
  () => props.section.id,
  () => {
    activeGroup.value = null
  },
)
</script>

<template>
  <PageWithSections v-if="showNav" :sections="navItems" @select="onSelectSection">
    <slot name="before" />
    <v-card variant="outlined" class="mb-4">
      <v-card-text>
        <ConfigForm
          :module-name="'agent'"
          :schema="schema"
          :config="agent.draft"
          :bot-id="agent.botId"
          :active-group="activeGroup"
          @change="agent.onChange"
        />
      </v-card-text>
    </v-card>
    <slot />
  </PageWithSections>

  <div v-else>
    <slot name="before" />
    <v-card v-if="navItems.length" variant="outlined" class="mb-4">
      <v-card-text>
        <ConfigForm
          :module-name="'agent'"
          :schema="schema"
          :config="agent.draft"
          :bot-id="agent.botId"
          @change="agent.onChange"
        />
      </v-card-text>
    </v-card>
    <slot />
  </div>
</template>
