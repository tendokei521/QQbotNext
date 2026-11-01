<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import http from '@/api/http'
import { errorMessage } from '@/api/http'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useNotifyStore } from '@/stores/notify'

const agent = useAgentConfigStore()
const notify = useNotifyStore()

/** 高级选项（系统方案）：行为规则 / 参与度 / 结构化消息 */
const form = reactive({
  structured_assembly: true,
})
const loading = ref(true)
const saving = ref(false)

const accountLabel = computed(() => agent.botId || '未选择账号')

async function load() {
  loading.value = true
  try {
    if (agent.botId) {
      const res = await http.get('/api/agent/config', { params: { account_id: agent.botId } })
      const cfg = (res.data && res.data.config) || {}
      form.structured_assembly = cfg.structured_assembly !== false
    }
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!agent.botId || saving.value) return
  saving.value = true
  try {
    await http.post(
      '/api/agent/config',
      { config: { structured_assembly: form.structured_assembly } },
      { params: { account_id: agent.botId } },
    )
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="app-page-header">
      <div>
        <h1 class="app-page-title">高级选项</h1>
        <div class="app-page-subtitle">实验性开关：消息组装方式。改动即保存。</div>
      </div>
      <v-chip size="small" variant="tonal">{{ accountLabel }}</v-chip>
    </div>

    <v-skeleton-loader v-if="loading" type="card" />

    <v-card v-else variant="outlined">
      <v-card-text>
        <v-divider class="my-4" />
        <div class="app-muted mb-3">消息组装方式</div>
        <v-switch
          v-model="form.structured_assembly"
          label="结构化消息"
          hint="开启后消息带时间、发送者与引用标记；关闭则只发原始正文"
          density="comfortable"
          color="primary"
          hide-details="auto"
          @update:model-value="save"
        />
      </v-card-text>
    </v-card>
  </div>
</template>
