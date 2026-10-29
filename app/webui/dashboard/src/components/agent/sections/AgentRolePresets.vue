<script setup lang="ts">
/**
 * 「人格」定制卡片（AI 对话面板用）。
 *
 * 一屏一张卡，按用户决策顺序排：
 *   1. 当前人格（只读 chip）+ 去「人格设定」编辑文案；
 *   2. 从人格预设里挑一个并应用（唯一的「应用」入口，人格设定页只负责编辑内容）；
 *   3. 低频的「提示词风格」收进折叠面板，默认收起。
 *
 * 人格内容的唯一来源是「人格设定」页的角色预设库；这里只读预设列表、写
 * persona_preset_id / system_prompt，不再复制第二份预览与第二个编辑入口。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import http, { errorMessage } from '@/api/http'
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

/**
 * 提示词风格（三选一）。
 *
 * 唯一真相是后端 `app/agent/assembly/presets.py::PRESETS`（那里有全文），这里只放
 * 展示用的标签与图标——`key` 必须与后端逐字一致，否则选了等于没选（后端按未知值忽略）。
 */
const PROMPT_PRESETS = [
  {
    key: 'chat',
    label: '聊天',
    icon: 'mdi-chat-outline',
    color: 'info',
    desc: '混在群里的普通群友：短句、选择性接话、不端着',
  },
  {
    key: 'roleplay',
    label: '角色扮演',
    icon: 'mdi-drama-masks',
    color: 'purple',
    desc: '以人格设定里的身份持续扮演：有人物感、不跳出',
  },
  {
    key: 'assistant',
    label: '助手',
    icon: 'mdi-robot-outline',
    color: 'success',
    desc: '服务型助手：有求必应、先给结论、结构化',
  },
]

const agent = useAgentConfigStore()
const notify = useNotifyStore()
const router = useRouter()

const presets = ref<RolePreset[]>([])
const loading = ref(false)
const search = ref('')
const selectedId = ref('')

const currentPromptPreset = computed(() => String(agent.draft.prompt_preset || ''))

function selectPromptPreset(key: string) {
  // 再点一次同一张卡片 = 取消（回到"沿用内置文案"），省一个额外的取消按钮
  agent.onChange('prompt_preset', currentPromptPreset.value === key ? '' : key)
}

const filteredPresets = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return presets.value
  return presets.value.filter((p) =>
    [p.name, p.description, p.system_prompt].some((v) => (v || '').toLowerCase().includes(q)),
  )
})

const selectedPreset = computed(() => presets.value.find((p) => p.id === selectedId.value) || null)

/** 当前生效的人格：优先按活引用 persona_preset_id（人设唯一来源），旧数据回退按提示词内容匹配 */
const currentPreset = computed(() => {
  const presetId = String(agent.draft.persona_preset_id || '')
  if (presetId) return presets.value.find((p) => p.id === presetId) || null
  return presets.value.find((p) => p.system_prompt === agent.draft.system_prompt) || null
})

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
    notify.push('请先在「账号管理」添加账号，再应用人格', 'warning')
    return
  }
  agent.onChange('system_prompt', selectedPreset.value.system_prompt)
  // 人设的唯一来源是人格预设：账号已有 persona_preset_id 时后端会优先用预设内容，
  // 只写 system_prompt 会出现「提示已应用但人设没变」。同步写活引用，保证两处一致。
  agent.onChange('persona_preset_id', selectedPreset.value.id)
  // 保存失败时 save() 已经弹了失败原因，这里只在真正落盘后说「已应用」
  if (await agent.save()) {
    notify.push(`已应用人格「${selectedPreset.value.name}」`, 'success')
  }
}

function goToPersonas() {
  router.push('/personas')
}

onMounted(() => {
  loadPresets()
})
</script>

<template>
  <v-card variant="outlined" class="mb-4">
    <v-card-title class="d-flex align-center flex-wrap gap-2">
      <v-icon icon="mdi-account-heart" class="mr-2" color="pink" /> 人格
      <v-chip v-if="currentPreset" size="small" color="pink" variant="tonal">当前人格：{{ currentPreset.name }}</v-chip>
      <v-chip v-else-if="agent.draft.system_prompt" size="small" variant="tonal">当前人格：自定义</v-chip>
      <v-chip v-else size="small" variant="tonal">当前人格：未选择</v-chip>
      <v-spacer />
      <v-btn size="small" variant="text" prepend-icon="mdi-pencil-outline" @click="goToPersonas">去编辑文案</v-btn>
    </v-card-title>
    <v-card-text class="text-caption pt-0 pb-0" style="opacity: 0.7">
      选中一个人格预设后点「应用」，会立即写入当前账号。
    </v-card-text>

    <div class="px-4 pb-2">
      <v-row dense>
        <v-col cols="12" md="7">
          <div class="text-subtitle-2 mb-2">选择人格预设</div>
          <v-text-field
            v-model="search"
            prepend-inner-icon="mdi-magnify"
            label="搜索人格预设"
            density="compact"
            variant="outlined"
            hide-details
            class="mb-2"
          />
          <v-progress-linear v-if="loading" indeterminate color="primary" />
          <template v-else-if="!presets.length">
            <div class="text-caption text-center pa-4">还没有人格预设，先去「人格设定」新建一个。</div>
          </template>
          <v-list v-else density="compact" class="preset-list">
            <v-list-item
              v-for="p in filteredPresets"
              :key="p.id"
              :active="selectedId === p.id"
              @click="selectedId = p.id"
            >
              <template #prepend>
                <v-icon icon="mdi-account-heart-outline" color="pink" />
              </template>
              <v-list-item-title class="text-body-2">{{ p.name }}</v-list-item-title>
              <v-list-item-subtitle class="text-caption">
                {{ p.description || '无描述' }}
              </v-list-item-subtitle>
            </v-list-item>
            <v-list-item v-if="!filteredPresets.length">
              <v-list-item-title class="text-caption text-center py-3" style="opacity: 0.5">无匹配人格</v-list-item-title>
            </v-list-item>
          </v-list>
        </v-col>

        <v-col cols="12" md="5">
          <div class="selected-box">
            <template v-if="selectedPreset">
              <div class="d-flex align-center flex-wrap gap-2 mb-2">
                <v-chip size="small" color="pink" variant="tonal">{{ selectedPreset.name }}</v-chip>
                <v-chip v-if="selectedPreset.id === currentPreset?.id" size="small" color="success" variant="tonal">
                  当前正在使用
                </v-chip>
              </div>
              <div v-if="selectedPreset.description" class="text-caption mb-3" style="opacity: 0.7">
                {{ selectedPreset.description }}
              </div>
              <v-btn
                color="primary"
                variant="tonal"
                prepend-icon="mdi-check-circle-outline"
                :disabled="agent.botId == null"
                @click="applyToAgent"
              >
                应用所选人格
              </v-btn>
              <div class="text-caption mt-2" style="opacity: 0.6">应用后立即对当前账号生效。</div>
            </template>
            <div v-else class="text-caption py-6 text-center" style="opacity: 0.55">请从左侧选择一个人格预设</div>
          </div>
        </v-col>
      </v-row>
    </div>

    <div class="px-4 pb-4 pt-2">
      <v-expansion-panels variant="accordion">
        <v-expansion-panel>
          <v-expansion-panel-title class="text-body-2">
            <v-icon icon="mdi-tune-variant" size="small" class="mr-2" />
            高级：提示词风格
            <v-spacer />
            <v-chip v-if="currentPromptPreset" size="x-small" variant="tonal">
              {{ PROMPT_PRESETS.find((p) => p.key === currentPromptPreset)?.label || currentPromptPreset }}
            </v-chip>
            <v-chip v-else size="x-small" variant="tonal">沿用内置文案</v-chip>
          </v-expansion-panel-title>
          <v-expansion-panel-text>
            <div class="text-caption mb-3" style="opacity: 0.7">
              只换说话风格，不改人格内容；再点一次已选卡片可取消。
            </div>
            <v-row dense>
              <v-col v-for="p in PROMPT_PRESETS" :key="p.key" cols="12" md="4">
                <v-card
                  :variant="currentPromptPreset === p.key ? 'tonal' : 'outlined'"
                  :color="currentPromptPreset === p.key ? p.color : undefined"
                  class="h-100 preset-card"
                  @click="selectPromptPreset(p.key)"
                >
                  <v-card-item>
                    <template #prepend>
                      <v-icon :icon="p.icon" :color="currentPromptPreset === p.key ? p.color : undefined" />
                    </template>
                    <v-card-title class="text-body-1">{{ p.label }}</v-card-title>
                    <v-card-subtitle class="text-caption">{{ p.desc }}</v-card-subtitle>
                    <template #append>
                      <v-icon v-if="currentPromptPreset === p.key" icon="mdi-check-circle" :color="p.color" />
                    </template>
                  </v-card-item>
                </v-card>
              </v-col>
            </v-row>
          </v-expansion-panel-text>
        </v-expansion-panel>
      </v-expansion-panels>
    </div>
  </v-card>
</template>

<style scoped>
.preset-list {
  max-height: 300px;
  overflow-y: auto;
}

.selected-box {
  height: 100%;
  min-height: 120px;
  padding: 12px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  border-radius: 10px;
  background: rgba(var(--v-theme-on-surface), 0.02);
}

.preset-card {
  cursor: pointer;
  transition: transform 0.12s ease;
}

.preset-card:hover {
  transform: translateY(-1px);
}
</style>
