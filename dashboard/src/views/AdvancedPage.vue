<script setup lang="ts">
/**
 * 高级设置概览（index）。
 *
 * 「常用配置」和「进阶调参」拆成两个区域，是因为它们的心智完全不同：
 * Agent 面板里的六页是「配好就能用」，这里的几页是「出问题了 / 想精细控制才来」。
 * 混在一起时，新手会被 100+ 个调参项劝退。
 *
 * 外壳（bot 守卫 / 加载 / 保存状态）由 ``AgentLayout`` 统一提供。
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { AGENT_INDEX_PATH, agentSectionBadge, sectionsOfArea } from '@/agent/sections'

const agent = useAgentConfigStore()
const router = useRouter()

const pages = computed(() =>
  sectionsOfArea('advanced').map((section) => ({
    to: section.path,
    title: section.title,
    desc: section.subtitle,
    icon: section.icon,
    color: section.color,
    status: agentSectionBadge(section.id, {
      draft: agent.draft,
      poolModelIds: agent.poolModelIds,
      permission: agent.permission,
    }),
  })),
)
</script>

<template>
  <div>
    <v-alert type="info" variant="tonal" density="compact" class="mb-4" closable>
      <div class="font-weight-medium">这里的都是进阶项，配错会让 Agent 行为变得奇怪</div>
      <div class="text-caption mt-1">
        消息组装、用户信息感知、指代消解等已改为固定流程，不再需要手动开关。
        日常使用请回「Agent 面板」。
      </div>
    </v-alert>

    <div class="advanced-grid">
      <v-card
        v-for="p in pages"
        :key="p.to"
        variant="tonal"
        class="advanced-card"
        :color="p.color"
        @click="router.push(p.to)"
      >
        <v-card-text class="d-flex align-center">
          <v-icon :icon="p.icon" size="30" class="mr-3" />
          <div>
            <div class="font-weight-medium">{{ p.title }}</div>
            <div class="text-caption" style="opacity: 0.75">{{ p.desc }}</div>
            <v-chip size="x-small" variant="tonal" class="mt-1">{{ p.status }}</v-chip>
          </div>
        </v-card-text>
      </v-card>
    </div>

    <v-btn variant="text" prepend-icon="mdi-arrow-left" class="mt-4" @click="router.push(AGENT_INDEX_PATH)">
      返回 Agent 面板
    </v-btn>
  </div>
</template>

<style scoped>
.advanced-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 14px;
}

.advanced-card {
  cursor: pointer;
  transition: transform 0.15s ease;
}

.advanced-card:hover {
  transform: translateY(-2px);
}
</style>
