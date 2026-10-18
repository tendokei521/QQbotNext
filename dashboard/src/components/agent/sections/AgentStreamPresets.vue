<script setup lang="ts">
/**
 * 「流式回复」分区的定制 UI：发送节奏预设卡片。
 * 参数表单由 AgentConfigSection 统一渲染。
 */
import { computed } from 'vue'
import { useAgentConfigStore } from '@/stores/agentConfig'

const agent = useAgentConfigStore()

// 预设的唯一来源是后端 /api/agent/config 的 stream_presets（config_schema.STREAM_PRESETS）。
// 此前这里还硬编码了一份 fallback，与后端逐值重复，已删除。
const presets = computed<Record<string, any>>(() => agent.streamPresets)
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
</script>

<template>
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
      <div v-if="presetKeys.length" class="preset-grid">
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
              <v-icon v-if="activePreset === key" color="success">mdi-check-circle</v-icon>
            </div>
            <div class="text-caption mt-1" style="opacity: 0.75">{{ p.description }}</div>
          </v-card-text>
        </v-card>
      </div>
      <div class="text-caption mt-3" style="opacity: 0.6">
        预设会覆盖基础间隔、最短/最长间隔和短语延迟；保存后生效。
      </div>
      <div v-if="!presetKeys.length" class="text-caption text-center pa-4" style="opacity: 0.55">
        未获取到发送节奏预设（配置加载失败时可点右上角重试）
      </div>
    </v-card-text>
  </v-card>
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
