<script setup lang="ts">
/**
 * 渲染某个分区的 schema 配置表单。
 *
 * 分区含多个分组时，左侧出现分组二级导航（PageSectionNav，粘性 + scroll-spy）；
 * 只有 ≤2 组时不渲染导航——它和全局侧栏并列会像"两层菜单"，用户分不清。
 * 点击导航除了滚动到锚点，还会通过 ConfigForm 的 activeGroup 强制展开对应面板。
 */
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ConfigForm from '@/components/config/ConfigForm.vue'
import PageWithSections from '@/components/common/PageWithSections.vue'
import type { SectionItem } from '@/components/common/PageSectionNav.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { configGroupDomId, splitSchema } from '@/utils/schema'
import { agentSectionSchema, type AgentSection } from '@/agent/sections'

const props = defineProps<{ section: AgentSection }>()

const agent = useAgentConfigStore()
const route = useRoute()
const router = useRouter()

// 页内分组结构来自 registry（不是后端 group）：标题、顺序、字段归属都由
// sections.json 决定，后端 group 在这里已被重写成本页的子分组 id。
const schema = computed(() => agentSectionSchema(props.section, agent.schema))

const navItems = computed<SectionItem[]>(() => {
  const { groups } = splitSchema(schema.value)
  return Object.entries(groups).map(([id, def]: [string, any]) => ({
    id: configGroupDomId(id),
    label: def?.label || id,
    icon: def?.icon || 'mdi-circle-small',
  }))
})

// 页内分组导航：≤2 组时收起（和全局侧栏并列会像两层菜单，用户分不清），
// ≥3 组才有"跳转"价值，并给导航卡加「本页分组」小标题说明这是页内锚点。
const showNav = computed(() => navItems.value.length >= 3)

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

// 配置搜索跳转带来 ?group=xxx 时展开并滚动到对应卡片
watch(
  () => route.query.group,
  async (groupId) => {
    if (typeof groupId !== 'string' || !groupId) return
    onSelectSection(configGroupDomId(groupId))
    // 等面板展开（activeGroup 生效）后布局才最终确定，再滚动才准
    await nextTick()
    await nextTick()
    document
      .getElementById(configGroupDomId(groupId))
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  },
  { immediate: true },
)
</script>

<template>
  <div>
    <!-- 对话行为的合并等待/冷却/打断可被账号或单群规则覆盖：页内先给一句提示，不用用户自己猜 -->
    <v-alert v-if="props.section.id === 'behavior'" type="info" variant="tonal" density="compact" class="mb-3">
      <div class="d-flex align-center flex-wrap gap-2">
        <span class="text-caption">账号专属规则或单个群的规则可以覆盖这些值</span>
        <v-btn size="small" variant="text" prepend-icon="mdi-arrow-right" @click="router.push('/reply?tab=rules')">
          去调整
        </v-btn>
      </div>
    </v-alert>

    <PageWithSections v-if="showNav" :sections="navItems" nav-title="本页分组" @select="onSelectSection">
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
  </div>
</template>
