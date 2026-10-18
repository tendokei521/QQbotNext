<script setup lang="ts">
/**
 * 「模型」分区的定制 UI：Provider 模型池（顺序请求 / 测试 / 增删改序）。
 * 分区的 schema 字段由 AgentConfigSection 统一渲染，这里只管池子。
 */
import { computed, ref } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useNotifyStore } from '@/stores/notify'

const agent = useAgentConfigStore()
const notify = useNotifyStore()

const poolDialog = ref(false)
const pendingPoolModelId = ref('')
const testingId = ref('')

async function testModel(id: string) {
  if (testingId.value) return
  testingId.value = id
  try {
    const res = await http.post(`/api/provider-models/${id}/test`, null)
    notify.push(res.data?.message || '模型连接正常', 'success')
  } catch (err: any) {
    notify.push(errorMessage(err), 'error')
  } finally {
    testingId.value = ''
  }
}

const availablePoolModels = computed(() =>
  agent.providerModels.filter((m) => !agent.poolModelIds.includes(m.id)),
)

function poolLabel(id: string): string {
  const m = agent.providerModels.find((x) => x.id === id)
  return m ? `${m.preset_name || m.preset_id} / ${m.model}` : id
}

function addPoolModel() {
  if (!pendingPoolModelId.value || agent.poolModelIds.includes(pendingPoolModelId.value)) return
  agent.poolModelIds.push(pendingPoolModelId.value)
  agent.syncPool()
  poolDialog.value = false
  pendingPoolModelId.value = ''
}

function removePoolModel(index: number) {
  agent.poolModelIds.splice(index, 1)
  agent.syncPool()
}

function movePoolModel(index: number, delta: number) {
  const target = index + delta
  if (target < 0 || target >= agent.poolModelIds.length) return
  const [moved] = agent.poolModelIds.splice(index, 1)
  agent.poolModelIds.splice(target, 0, moved)
  agent.syncPool()
}
</script>

<template>
  <v-card variant="outlined" class="mb-4">
    <v-card-title class="d-flex align-center">
      <v-icon icon="mdi-api" class="mr-2" color="info" /> Provider 模型（按顺序请求）
      <v-spacer />
      <v-btn size="small" variant="tonal" prepend-icon="mdi-plus" @click="poolDialog = true">添加模型</v-btn>
    </v-card-title>
    <v-card-text>
      <v-list v-if="agent.poolModelIds.length" density="compact">
        <v-list-item v-for="(id, i) in agent.poolModelIds" :key="id">
          <template #prepend><span class="pool-order">{{ i + 1 }}</span></template>
          <v-list-item-title>{{ poolLabel(id) }}</v-list-item-title>
          <template #append>
            <v-btn size="x-small" variant="text" prepend-icon="mdi-play" :loading="testingId === id" @click="testModel(id)">
              测试
            </v-btn>
            <v-btn size="x-small" variant="text" icon="mdi-arrow-up" :disabled="i === 0" @click="movePoolModel(i, -1)" />
            <v-btn
              size="x-small"
              variant="text"
              icon="mdi-arrow-down"
              :disabled="i === agent.poolModelIds.length - 1"
              @click="movePoolModel(i, 1)"
            />
            <v-btn size="x-small" variant="text" icon="mdi-close" color="error" @click="removePoolModel(i)" />
          </template>
        </v-list-item>
      </v-list>
      <div v-else class="text-caption text-center pa-4" style="color: rgba(var(--v-theme-on-surface), 0.45)">
        尚未配置模型，请添加一个 Provider 模型
      </div>
    </v-card-text>

    <v-dialog v-model="poolDialog" max-width="420">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-plus" class="mr-2" color="info" /> 添加 Provider 模型
        </v-card-title>
        <v-card-text>
          <v-select
            v-model="pendingPoolModelId"
            :items="availablePoolModels.map((m) => ({ title: `${m.preset_name || m.preset_id} / ${m.model}`, value: m.id }))"
            label="选择模型"
            density="comfortable"
            hide-details
          />
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="poolDialog = false">取消</v-btn>
          <v-btn color="primary" variant="tonal" :disabled="!pendingPoolModelId" @click="addPoolModel">添加</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-card>
</template>

<style scoped>
.pool-order {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: rgba(var(--v-theme-info), 0.16);
  color: rgb(var(--v-theme-info));
  font-size: 12px;
  font-weight: 600;
}
</style>
