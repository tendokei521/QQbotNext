<script setup lang="ts">
/**
 * 「模型」分区的定制 UI：模型池（顺序请求 / 测试 / 增删改序）。
 *
 * 这里的「加入」只是把**模型服务页已经建好的模型**放进本账号的模型池；
 * 新建服务商/模型在「模型服务」页，两者语义不同，按钮文案必须区分开。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import http, { errorMessage } from '@/api/http'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useNotifyStore } from '@/stores/notify'

const agent = useAgentConfigStore()
const notify = useNotifyStore()
const router = useRouter()

const poolDialog = ref(false)
const pendingPoolModelId = ref('')
const testingId = ref('')

/** 模型服务页还没有任何模型：此时「从模型服务加入」没有候选，直接引导去创建 */
const hasAnyModel = computed(() => agent.providerModels.length > 0)

const availablePoolModels = computed(() =>
  agent.providerModels.filter((m) => !agent.poolModelIds.includes(m.id)),
)

const canJoin = computed(() => availablePoolModels.value.length > 0)

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

function poolLabel(id: string): string {
  const m = agent.providerModels.find((x) => x.id === id)
  return m ? `${m.preset_name || m.preset_id} / ${m.model}` : id
}

function openPoolDialog() {
  pendingPoolModelId.value = ''
  poolDialog.value = true
}

function addPoolModel() {
  if (!pendingPoolModelId.value || agent.poolModelIds.includes(pendingPoolModelId.value)) return
  agent.poolModelIds.push(pendingPoolModelId.value)
  agent.syncPool()
  poolDialog.value = false
  pendingPoolModelId.value = ''
  notify.push('已加入，按列表顺序依次尝试', 'success')
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

function goToProviderPresets() {
  poolDialog.value = false
  router.push('/provider-presets')
}
</script>

<template>
  <v-card variant="outlined" class="mb-4">
    <v-card-title class="d-flex align-center flex-wrap gap-2">
      <v-icon icon="mdi-api" class="mr-2" color="info" /> 模型
      <v-spacer />
      <v-btn v-if="hasAnyModel" size="small" variant="tonal" prepend-icon="mdi-plus" @click="openPoolDialog">
        从模型服务加入
      </v-btn>
      <v-btn v-else size="small" color="primary" variant="tonal" prepend-icon="mdi-open-in-new" @click="goToProviderPresets">
        去「模型服务」添加模型
      </v-btn>
    </v-card-title>
    <v-card-text class="text-body-2 pt-0 pb-0" style="opacity: 0.8">
      从上到下依次尝试，前面的失败再换下一个。
    </v-card-text>
    <v-card-text v-if="agent.poolModelIds.length">
      <v-list density="compact" class="model-list">
        <v-list-item v-for="(id, i) in agent.poolModelIds" :key="id">
          <template #prepend><span class="pool-order">{{ i + 1 }}</span></template>
          <v-list-item-title>{{ poolLabel(id) }}</v-list-item-title>
          <template #append>
            <div class="model-actions">
              <v-btn
                icon="mdi-play-circle-outline"
                size="small"
                variant="text"
                title="测试连接"
                :loading="testingId === id"
                @click="testModel(id)"
              />
              <v-btn
                icon="mdi-arrow-up"
                size="small"
                variant="text"
                title="上移"
                :disabled="i === 0"
                @click="movePoolModel(i, -1)"
              />
              <v-btn
                icon="mdi-arrow-down"
                size="small"
                variant="text"
                title="下移"
                :disabled="i === agent.poolModelIds.length - 1"
                @click="movePoolModel(i, 1)"
              />
              <v-btn
                icon="mdi-close"
                size="small"
                variant="text"
                color="error"
                title="移出模型池"
                @click="removePoolModel(i)"
              />
            </div>
          </template>
        </v-list-item>
      </v-list>
    </v-card-text>

    <div v-else-if="hasAnyModel" class="pool-empty px-4 pb-4 pt-2">
      <div class="text-caption">还没有加入模型</div>
    </div>
    <div v-else class="pool-empty px-4 pb-4 pt-2">
      <div class="text-caption">模型服务里还没有模型</div>
    </div>

    <v-dialog v-model="poolDialog" max-width="460">
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon icon="mdi-plus" class="mr-2" color="info" /> 从模型服务加入
        </v-card-title>
        <v-card-text>
          <template v-if="canJoin">
            <v-select
              v-model="pendingPoolModelId"
              :items="availablePoolModels.map((m) => ({ title: `${m.preset_name || m.preset_id} / ${m.model}`, value: m.id }))"
              label="选择模型"
              density="comfortable"
              hide-details
            />
          </template>
          <template v-else>
            <div class="text-body-2">没有可加入的模型</div>
            <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-open-in-new" class="mt-3" @click="goToProviderPresets">
              去「模型服务」
            </v-btn>
          </template>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="poolDialog = false">取消</v-btn>
          <v-btn v-if="canJoin" color="primary" variant="tonal" :disabled="!pendingPoolModelId" @click="addPoolModel">
            加入
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-card>
</template>

<style scoped>
.model-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.model-list :deep(.v-list-item) {
  padding-inline: 0;
}

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

.pool-empty {
  padding: 18px 12px;
  text-align: center;
  color: rgba(var(--v-theme-on-surface), 0.55);
}
</style>
