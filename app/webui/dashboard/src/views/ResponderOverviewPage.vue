<script setup lang="ts">
/**
 * 插件响应策略 · 总览（框架功能组）。
 *
 * 列所有插件：声明支持多账号响应权的可以点进去配「同群由哪个账号响应」；
 * 未声明的只说明"无需配置"。数据是全局的（channel_policies 按 能力 × 群 存，
 * 对所有账号生效），所以页面没有账号选择器。
 */
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useResponderStore, type ResponderOverviewItem } from '@/stores/responder'

const router = useRouter()
const responder = useResponderStore()

const overview = computed(() => responder.overviewData)
const declared = computed(() => overview.value?.declared || [])
const undeclared = computed(() => overview.value?.undeclared || [])
const loading = computed(() => responder.overviewLoading)

const POLICY_LABELS: Record<string, string> = {
  all: '各自响应',
  dedupe: '只回一次',
  delegate: '固定账号',
  leader: '主备',
}

function policySummary(item: ResponderOverviewItem): string {
  const parts = Object.entries(item.policies || {})
    .filter(([policy]) => policy !== 'all')
    .map(([policy, count]) => `${POLICY_LABELS[policy] || policy} ${count} 群`)
  return parts.join(' · ')
}

function openCard(item: ResponderOverviewItem) {
  void router.push(`/responder/${encodeURIComponent(item.capability_id)}`)
}

function configuredText(item: ResponderOverviewItem): string {
  if (!item.groups_total) return '暂无可配置的群'
  return `${item.configured}/${item.groups_total} 个群已配置`
}

onMounted(() => {
  void responder.loadOverview().catch(() => undefined)
})
</script>

<template>
  <div>
    <h2 class="text-h5">插件响应策略</h2>
    <div class="app-page-subtitle">同一个群里装了多个账号时，由哪个账号响应。</div>
    <div class="app-page-subtitle">只对声明支持多账号响应权的插件生效；对所有账号生效。</div>

    <div v-if="loading" class="text-caption mt-4">加载中…</div>

    <template v-else>
      <div class="section-title mt-4">可配置（{{ declared.length }}）</div>
      <div v-if="!declared.length" class="text-caption mt-1">
        当前没有声明支持多账号响应权的插件——这类插件由作者在代码里声明，不需要在这里配置。
      </div>
      <v-row v-else class="mt-1">
        <v-col v-for="item in declared" :key="item.capability_id" cols="12" md="6" lg="4">
          <v-card
            variant="outlined"
            class="responder-card"
            tabindex="0"
            @click="openCard(item)"
            @keydown.enter="openCard(item)"
          >
            <v-card-title class="d-flex align-center flex-wrap gap-2">
              <v-icon icon="mdi-puzzle-outline" class="mr-1" color="primary" />
              <span class="card-name">{{ item.name }}</span>
              <v-chip v-if="item.sign" size="x-small" variant="tonal">{{ item.sign }}</v-chip>
            </v-card-title>
            <v-card-text>
              <div v-if="item.description" class="text-caption card-desc">{{ item.description }}</div>
              <div class="d-flex align-center flex-wrap gap-2 mt-2">
                <v-chip size="x-small" variant="tonal">{{ configuredText(item) }}</v-chip>
                <v-chip v-if="policySummary(item)" size="x-small" variant="tonal">
                  {{ policySummary(item) }}
                </v-chip>
                <v-chip v-if="item.degraded" size="x-small" color="warning" variant="tonal">
                  {{ item.degraded }} 个群降级
                </v-chip>
              </div>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>

      <template v-if="undeclared.length">
        <div class="section-title mt-6">无需配置（{{ undeclared.length }}）</div>
        <div class="text-caption mt-1 mb-2">这些插件没有声明支持多账号响应权，同一个群里始终各自响应。</div>
        <v-row>
          <v-col v-for="item in undeclared" :key="item.capability_id" cols="12" md="6" lg="4">
            <v-card variant="outlined" class="responder-card responder-card--off">
              <v-card-title class="d-flex align-center flex-wrap gap-2">
                <v-icon icon="mdi-puzzle-outline" class="mr-1" />
                <span class="card-name">{{ item.name }}</span>
                <v-chip v-if="item.sign" size="x-small" variant="text">{{ item.sign }}</v-chip>
              </v-card-title>
            </v-card>
          </v-col>
        </v-row>
      </template>
    </template>
  </div>
</template>

<style scoped>
.section-title {
  font-size: 0.875rem;
  font-weight: 600;
  opacity: 0.75;
}

.responder-card {
  height: 100%;
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.responder-card:hover,
.responder-card:focus-visible {
  border-color: rgb(var(--v-theme-primary));
}

.responder-card--off {
  cursor: default;
  opacity: 0.55;
}

.card-name {
  font-size: 1rem;
  font-weight: 600;
}

.card-desc {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  opacity: 0.75;
}
</style>
