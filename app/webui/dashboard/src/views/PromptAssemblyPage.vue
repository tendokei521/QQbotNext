<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import http from '@/api/http'
import { errorMessage } from '@/api/http'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useNotifyStore } from '@/stores/notify'

const agent = useAgentConfigStore()
const notify = useNotifyStore()

/** 提示词组装（系统方案）：行为规则 / 参与度 / 结构化消息 */
const form = reactive({
  behavior_rules_override: '',
  participation: 'normal',
  structured_assembly: true,
})
const loading = ref(true)
const saving = ref(false)

const participationItems = [
  { title: '低（少插话）', value: 'low' },
  { title: '正常', value: 'normal' },
  { title: '高（多参与）', value: 'high' },
]

const accountLabel = computed(() => agent.botId || '未选择账号')

async function load() {
  loading.value = true
  try {
    if (agent.botId) {
      const res = await http.get('/api/agent/config', { params: { account_id: agent.botId } })
      const cfg = (res.data && res.data.config) || {}
      form.behavior_rules_override = cfg.behavior_rules_override ?? ''
      form.participation = cfg.participation ?? 'normal'
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
      { config: { ...form } },
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
        <h1 class="app-page-title">提示词组装</h1>
        <div class="app-page-subtitle">系统级方案：行为规则怎么注入、本轮消息怎么组装。改动即保存。</div>
      </div>
      <v-chip size="small" variant="tonal">{{ accountLabel }}</v-chip>
    </div>

    <v-skeleton-loader v-if="loading" type="card" />

    <v-card v-else variant="outlined">
      <v-card-text>
        <div class="app-muted mb-3">提示词注入</div>
        <v-textarea
          v-model="form.behavior_rules_override"
          label="覆盖行为规则（留空用内置）"
          hint="填写后整段替换；安全规则与工作方式仍会追加"
          rows="6"
          density="comfortable"
          variant="outlined"
          class="mb-4"
          @blur="save"
        />
        <v-select
          v-model="form.participation"
          :items="participationItems"
          label="参与度"
          hint="影响行为段语气与主动程度，不改触发规则"
          density="comfortable"
          variant="outlined"
          class="mb-4"
          @update:model-value="save"
        />
        <v-divider class="my-4" />
        <div class="app-muted mb-3">消息组装方式</div>
        <v-switch
          v-model="form.structured_assembly"
          label="结构化消息"
          hint="[time][sender] 正文 [at] [reply_to]；关闭则只发原始正文"
          density="comfortable"
          color="primary"
          hide-details="auto"
          @update:model-value="save"
        />
      </v-card-text>
    </v-card>
  </div>
</template>
