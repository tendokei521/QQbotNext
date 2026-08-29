<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import ConfigForm from '@/components/config/ConfigForm.vue'
import AgentSubPage from '@/components/agent/AgentSubPage.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'
import { filterSchemaByPage } from '@/utils/schema'

const agent = useAgentConfigStore()
const bots = useBotsStore()

const schema = computed(() => filterSchemaByPage(agent.schema, 'model'))
const poolDialog = ref(false)
const pendingPoolModelId = ref('')

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

onMounted(() => agent.load())
watch(
  () => bots.currentBot?.bot_id,
  () => agent.load(true),
)
</script>

<template>
  <AgentSubPage title="模型" subtitle="Provider 模型池与调用参数" icon="mdi-api" color="info">
    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-api" class="mr-2" color="info" /> Provider 模型（按顺序请求）
        <v-spacer />
        <v-btn size="small" variant="tonal" prepend-icon="mdi-plus" @click="poolDialog = true">
          添加模型
        </v-btn>
      </v-card-title>
      <v-card-text>
        <v-list v-if="agent.poolModelIds.length" density="compact">
          <v-list-item v-for="(id, i) in agent.poolModelIds" :key="id">
            <template #prepend><span class="pool-order">{{ i + 1 }}</span></template>
            <v-list-item-title>{{ poolLabel(id) }}</v-list-item-title>
            <template #append>
              <v-btn size="x-small" variant="text" icon="mdi-arrow-up" :disabled="i === 0" @click="movePoolModel(i, -1)" />
              <v-btn size="x-small" variant="text" icon="mdi-arrow-down" :disabled="i === agent.poolModelIds.length - 1" @click="movePoolModel(i, 1)" />
              <v-btn size="x-small" variant="text" icon="mdi-close" color="error" @click="removePoolModel(i)" />
            </template>
          </v-list-item>
        </v-list>
        <div v-else class="text-caption text-center pa-4" style="color: rgba(var(--v-theme-on-surface), 0.45)">
          尚未配置模型，请添加一个 Provider 模型
        </div>
      </v-card-text>
    </v-card>

    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-cog-outline" class="mr-2" color="info" /> 模型参数
      </v-card-title>
      <v-card-text>
        <ConfigForm :module-name="'agent'" :schema="schema" :config="agent.draft" :bot-id="agent.botId" @change="agent.onChange" />
      </v-card-text>
    </v-card>

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
  </AgentSubPage>
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
