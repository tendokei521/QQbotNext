<script setup lang="ts">
/**
 * 总览页「运行状态」卡：把"能不能回话 / 差什么 / 去哪修"一次说清。
 *
 * 数据与首启向导、启动横幅同源（`/api/readiness`），所以三处口径永远一致；
 * 每条 blocker 都带 action.route，点一下就去修对应的配置。
 */
import { onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useReadinessStore } from '@/stores/readiness'

const readiness = useReadinessStore()
const router = useRouter()

function go(check: { action?: { route?: string } | null }) {
  const route = check.action?.route
  if (route) router.push(route)
}

onMounted(() => readiness.startPolling())
onUnmounted(() => readiness.stopPolling())
</script>

<template>
  <v-card variant="outlined" class="mb-4 readiness-card">
    <v-card-text>
      <div class="d-flex align-center flex-wrap gap-2 mb-2">
        <v-icon
          :icon="readiness.ready ? 'mdi-check-circle-outline' : 'mdi-alert-circle-outline'"
          :color="readiness.ready ? 'success' : 'warning'"
        />
        <span class="font-weight-medium">
          {{ readiness.ready ? '一切就绪' : `还不能回复消息 · ${readiness.blockers.length} 个问题待解决` }}
        </span>
        <v-spacer />
        <v-btn size="x-small" variant="text" prepend-icon="mdi-refresh" :loading="readiness.loading" @click="readiness.load()">
          刷新
        </v-btn>
        <v-btn
          v-if="!readiness.setupCompleted"
          size="small"
          color="primary"
          variant="tonal"
          prepend-icon="mdi-rocket-launch-outline"
          @click="router.push('/setup')"
        >
          开始首次配置
        </v-btn>
      </div>

      <div v-if="readiness.error" class="text-caption text-error">{{ readiness.error }}</div>

      <template v-else-if="readiness.ready">
        <div class="text-caption" style="opacity: 0.7">
          在群里 @ 我试试；如果没反应，去「日志」页看「为什么不回」。
        </div>
      </template>

      <template v-else>
        <div
          v-for="check in [...readiness.blockers, ...readiness.warnings]"
          :key="check.id"
          class="d-flex align-center flex-wrap gap-2 readiness-row"
        >
          <v-icon
            size="x-small"
            :icon="check.level === 'blocker' ? 'mdi-close-circle-outline' : 'mdi-alert-outline'"
            :color="check.level === 'blocker' ? 'error' : 'warning'"
          />
          <span class="text-body-2">{{ check.title }}</span>
          <span v-if="check.detail" class="text-caption" style="opacity: 0.65">{{ check.detail }}</span>
          <v-btn
            v-if="check.action?.route"
            size="x-small"
            variant="text"
            color="primary"
            @click="go(check)"
          >
            {{ check.action.label || '去配置' }}
          </v-btn>
        </div>
      </template>
    </v-card-text>
  </v-card>
</template>

<style scoped>
.readiness-card {
  border-radius: 14px;
}

.readiness-row + .readiness-row {
  margin-top: 4px;
}
</style>
