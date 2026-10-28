<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import { useBotsStore } from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { splitSchema } from '@/utils/schema'

interface BotOption {
  accountId: string
  title: string
}

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

const bots = useBotsStore()
const agent = useAgentConfigStore()
const notify = useNotifyStore()

const selectedBotId = ref<number | null>(null)
const targetConfig = ref<Record<string, any>>({})
const loading = ref(false)
const applying = ref(false)

const botOptions = computed<BotOption[]>(() =>
  bots.bots
    .filter((b) => b.id && b.id !== agent.botId)
    .map((b) => ({
      accountId: b.id,
      title: b.name || b.id,
    })),
)

const diffKeys = computed<string[]>(() => {
  const current = agent.draft || {}
  const target = targetConfig.value || {}
  return Object.keys({ ...current, ...target }).filter((k) => current[k] !== target[k])
})

/** 配置键是内部名字，界面上换成 schema 里的中文名 */
const fieldLabels = computed<Record<string, string>>(() => {
  const { items } = splitSchema(agent.schema)
  const labels: Record<string, string> = {}
  for (const [key, def] of Object.entries(items)) {
    labels[key] = def?.label || '配置项'
  }
  return labels
})

async function loadTarget(botId: number | null) {
  if (!botId) return
  loading.value = true
  try {
    const res = await http.get<{ ok: boolean; config: Record<string, any> }>('/api/agent/config', {
      params: { account_id: botId },
    })
    targetConfig.value = res.data.config || {}
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loading.value = false
  }
}

async function applyTarget() {
  if (!selectedBotId.value || applying.value) return
  applying.value = true
  try {
    agent.applyConfig(targetConfig.value)
    notify.push('已把来源账号的配置复制过来', 'success')
    emit('update:modelValue', false)
  } finally {
    applying.value = false
  }
}

watch(
  () => selectedBotId.value,
  (id) => {
    if (id) loadTarget(id)
    else targetConfig.value = {}
  },
)

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      selectedBotId.value = null
      targetConfig.value = {}
    }
  },
)
</script>

<template>
  <v-dialog :model-value="props.modelValue" max-width="540" @update:model-value="emit('update:modelValue', $event)">
    <v-card>
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-content-copy" class="mr-2" color="primary" /> 从其他账号复制配置
        <v-spacer />
        <v-btn icon="mdi-close" variant="text" size="small" @click="emit('update:modelValue', false)" />
      </v-card-title>
      <v-card-text>
        <v-select
          v-model="selectedBotId"
          :items="botOptions.map((b) => ({ title: b.title, value: b.accountId }))"
          label="选择来源账号"
          density="comfortable"
          variant="outlined"
          hide-details
        />
        <v-progress-linear v-if="loading" indeterminate color="primary" class="mt-3" />
        <div v-else-if="selectedBotId" class="mt-3">
          <div class="text-caption mb-1">来源账号与当前配置共有 <strong>{{ diffKeys.length }}</strong> 项不同。</div>
          <v-chip v-for="k in diffKeys.slice(0, 10)" :key="k" size="x-small" variant="tonal" class="mr-1 mb-1">{{ fieldLabels[k] || '配置项' }}</v-chip>
        </div>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="emit('update:modelValue', false)">取消</v-btn>
        <v-btn color="primary" variant="tonal" :disabled="!selectedBotId || !diffKeys.length" :loading="applying" @click="applyTarget">
          复制并应用
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
