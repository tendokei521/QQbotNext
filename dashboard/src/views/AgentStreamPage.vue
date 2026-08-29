<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import ConfigForm from '@/components/config/ConfigForm.vue'
import AgentSubPage from '@/components/agent/AgentSubPage.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useBotsStore } from '@/stores/bots'
import { filterSchemaByPage } from '@/utils/schema'

const agent = useAgentConfigStore()
const bots = useBotsStore()

const schema = computed(() => filterSchemaByPage(agent.schema, 'stream'))

const fallbackPresets: Record<string, any> = {
  fast: {
    label: '快速',
    description: '平均约 500–1000ms，消息紧跟生成节奏',
    config: {
      stream_send_interval_mode: 'fixed',
      stream_send_interval_base_ms: 750,
      stream_send_interval_min_ms: 500,
      stream_send_interval_max_ms: 1000,
      stream_send_curve: 'sqrt',
      stream_send_curve_k: 200,
      stream_short_message_length: 10,
      stream_short_message_delay_ms: 500,
      stream_long_message_delay_ms: 1000,
    },
  },
  normal: {
    label: '正常',
    description: '平均约 1000–2000ms，自然但有节奏',
    config: {
      stream_send_interval_mode: 'fixed',
      stream_send_interval_base_ms: 1500,
      stream_send_interval_min_ms: 1000,
      stream_send_interval_max_ms: 2000,
      stream_send_curve: 'sqrt',
      stream_send_curve_k: 200,
      stream_short_message_length: 10,
      stream_short_message_delay_ms: 1200,
      stream_long_message_delay_ms: 2000,
    },
  },
  slow: {
    label: '偏慢',
    description: '平均约 3000–4000ms，更有“思考感”',
    config: {
      stream_send_interval_mode: 'fixed',
      stream_send_interval_base_ms: 3500,
      stream_send_interval_min_ms: 3000,
      stream_send_interval_max_ms: 4000,
      stream_send_curve: 'sqrt',
      stream_send_curve_k: 200,
      stream_short_message_length: 10,
      stream_short_message_delay_ms: 3000,
      stream_long_message_delay_ms: 4000,
    },
  },
}

const presets = computed<Record<string, any>>(() =>
  Object.keys(agent.streamPresets).length ? agent.streamPresets : fallbackPresets,
)
const presetKeys = computed(() => Object.keys(presets.value))

function matchesPreset(key: string): boolean {
  const cfg = presets.value[key]?.config || {}
  return Object.entries(cfg).every(([k, v]) => agent.draft[k] === v)
}

const activePreset = computed(() => presetKeys.value.find((k) => matchesPreset(k)) || '')

function applyPreset(key: string) {
  const cfg = presets.value[key]?.config || {}
  Object.entries(cfg).forEach(([k, v]) => agent.onChange(k, v))
}

onMounted(() => agent.load())
watch(
  () => bots.currentBot?.bot_id,
  () => agent.load(true),
)
</script>

<template>
  <AgentSubPage title="流式回复" subtitle="流式开关、发送频率与预设" icon="mdi-wave" color="purple">
    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-wave" class="mr-2" color="purple" /> 发送节奏预设
        <v-spacer />
        <v-chip v-if="activePreset" size="small" variant="tonal" color="purple">
          当前：{{ presets[activePreset]?.label }}
        </v-chip>
        <v-chip v-else size="small" variant="tonal">自定义</v-chip>
      </v-card-title>
      <v-card-text>
        <div class="preset-grid">
          <v-card
            v-for="(p, key) in presets"
            :key="key"
            variant="outlined"
            class="preset-card"
            :class="{ 'is-active': activePreset === key }"
            @click="applyPreset(String(key))"
          >
            <v-card-text>
              <div class="d-flex align-center justify-space-between">
                <span class="font-weight-medium">{{ p.label }}</span>
                <v-icon color="success" v-if="activePreset === key">mdi-check-circle</v-icon>
              </div>
              <div class="text-caption mt-1" style="opacity: 0.75">{{ p.description }}</div>
            </v-card-text>
          </v-card>
        </div>
        <div class="text-caption mt-3" style="opacity: 0.6">
          预设会覆盖基础间隔、最短/最长间隔和短语延迟；保存后生效。
        </div>
      </v-card-text>
    </v-card>

    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-tune" class="mr-2" color="purple" /> 高级流式参数
      </v-card-title>
      <v-card-text>
        <ConfigForm :module-name="'agent'" :schema="schema" :config="agent.draft" :bot-id="agent.botId" @change="agent.onChange" />
      </v-card-text>
    </v-card>
  </AgentSubPage>
</template>

<style scoped>
.preset-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
}

.preset-card {
  cursor: pointer;
  transition:
    border-color 0.15s ease,
    background-color 0.15s ease;
}

.preset-card.is-active {
  border-color: rgb(var(--v-theme-purple));
  background: rgba(var(--v-theme-purple), 0.08);
}
</style>
