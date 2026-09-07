<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import http, { errorMessage } from '@/api/http'
import AgentSubPage from '@/components/agent/AgentSubPage.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useNotifyStore } from '@/stores/notify'

interface RolePreset {
  id: string
  name: string
  description: string
  system_prompt: string
  created_at: number
  updated_at: number
}

const agent = useAgentConfigStore()
const notify = useNotifyStore()
const router = useRouter()

const presets = ref<RolePreset[]>([])
const loading = ref(false)
const search = ref('')
const selectedId = ref('')

const filteredPresets = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return presets.value
  return presets.value.filter((p) =>
    [p.name, p.description, p.system_prompt].some((v) => (v || '').toLowerCase().includes(q)),
  )
})

const selectedPreset = computed(() => presets.value.find((p) => p.id === selectedId.value) || null)

const currentPreset = computed(() =>
  presets.value.find((p) => p.system_prompt === agent.draft.system_prompt) || null,
)

async function loadPresets() {
  loading.value = true
  try {
    const res = await http.get<{ ok: boolean; presets: RolePreset[] }>('/api/role-presets')
    presets.value = res.data?.presets || []
    if (selectedId.value && !presets.value.some((p) => p.id === selectedId.value)) {
      selectedId.value = ''
    }
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    loading.value = false
  }
}

async function applyToAgent() {
  if (!selectedPreset.value) return
  if (agent.botId == null) {
    notify.push('请先选择并连接一个 Bot', 'warning')
    return
  }
  agent.onChange('system_prompt', selectedPreset.value.system_prompt)
  await agent.save()
  notify.push(`已应用人格「${selectedPreset.value.name}」`, 'success')
}

function goToPersonas() {
  router.push('/personas')
}

onMounted(() => {
  agent.load()
  loadPresets()
})
</script>

<template>
  <AgentSubPage
    title="角色设定"
    subtitle="从已有的人格预设中选择并应用到当前 Agent"
    icon="mdi-account-heart"
    color="pink"
  >
    <v-row class="mt-2">
      <v-col cols="12" md="4">
        <v-card variant="outlined" class="h-full">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-account-group-outline" class="mr-2" color="pink" /> 人格预设
            <v-spacer />
            <v-btn size="small" color="primary" variant="tonal" prepend-icon="mdi-open-in-new" @click="goToPersonas">
              管理
            </v-btn>
          </v-card-title>
          <v-card-text class="pa-2">
            <v-text-field
              v-model="search"
              prepend-inner-icon="mdi-magnify"
              label="搜索人格"
              density="compact"
              variant="outlined"
              hide-details
              class="mb-2"
            />
            <v-progress-linear v-if="loading" indeterminate color="primary" />
            <template v-else-if="!presets.length">
              <div class="text-caption text-center pa-4">
                暂无人格预设，请先前往「人格设定」创建
              </div>
            </template>
            <v-list v-else density="compact">
              <v-list-item
                v-for="p in filteredPresets"
                :key="p.id"
                :active="selectedId === p.id"
                @click="selectedId = p.id"
              >
                <template #prepend>
                  <v-icon icon="mdi-account-heart" color="pink" />
                </template>
                <v-list-item-title class="text-body-2">{{ p.name }}</v-list-item-title>
                <v-list-item-subtitle class="text-caption">
                  {{ p.description || p.system_prompt.slice(0, 30) || '无描述' }}
                </v-list-item-subtitle>
              </v-list-item>
              <v-list-item v-if="!filteredPresets.length">
                <v-list-item-title class="text-caption text-center py-3" style="opacity: 0.5">
                  无匹配人格
                </v-list-item-title>
              </v-list-item>
            </v-list>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="8">
        <v-card variant="outlined" class="mb-4">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-robot-happy-outline" class="mr-2" color="primary" /> 当前启用人格
            <v-spacer />
            <v-btn variant="tonal" prepend-icon="mdi-account-heart" @click="goToPersonas">
              前往人格编辑
            </v-btn>
          </v-card-title>
          <v-card-text>
            <div class="d-flex align-center flex-wrap gap-2 mb-2">
              <v-chip v-if="currentPreset" size="small" color="pink" variant="tonal">
                {{ currentPreset.name }}
              </v-chip>
              <v-chip v-else-if="agent.draft.system_prompt" size="small" variant="tonal">
                自定义提示词
              </v-chip>
              <v-chip v-else size="small" variant="tonal">未启用</v-chip>
            </div>
            <div class="prompt-preview">
              {{ agent.draft.system_prompt || '当前 Agent 未启用任何人格提示词' }}
            </div>
          </v-card-text>
        </v-card>

        <v-card variant="outlined">
          <v-card-title class="d-flex align-center">
            <v-icon icon="mdi-format-list-checks" class="mr-2" color="primary" /> 选择人格
            <v-spacer />
            <v-btn
              color="primary"
              variant="tonal"
              prepend-icon="mdi-arrow-right-bold-circle-outline"
              :disabled="!selectedPreset || agent.botId == null"
              @click="applyToAgent"
            >
              应用所选人格
            </v-btn>
          </v-card-title>
          <v-card-text>
            <template v-if="selectedPreset">
              <div class="d-flex align-center flex-wrap gap-2 mb-2">
                <v-chip size="small" color="pink" variant="tonal">{{ selectedPreset.name }}</v-chip>
                <span v-if="selectedPreset.description" class="text-caption" style="opacity: 0.65">
                  {{ selectedPreset.description }}
                </span>
                <v-chip v-if="selectedPreset.id === currentPreset?.id" size="small" color="success" variant="tonal">
                  当前正在使用
                </v-chip>
              </div>
              <div class="prompt-preview">{{ selectedPreset.system_prompt }}</div>
            </template>
            <div v-else class="text-caption text-center py-6" style="opacity: 0.55">
              请从左侧选择一个人格预设
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </AgentSubPage>
</template>

<style scoped>
.prompt-preview {
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.6;
  opacity: 0.85;
  max-height: 260px;
  overflow-y: auto;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 8px;
  padding: 12px;
  background: rgba(var(--v-theme-on-surface), 0.02);
}
</style>