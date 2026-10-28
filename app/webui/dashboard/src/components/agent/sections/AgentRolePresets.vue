<script setup lang="ts">
/**
 * 「人格设定」分区的定制 UI：提示词方案三选一 + 从人格预设中挑选并应用。
 * system_prompt / persona_preset_id 等字段由 AgentConfigSection 渲染。
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
 * 提示词方案（三选一）。
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

const currentPreset = computed(
  () => presets.value.find((p) => p.system_prompt === agent.draft.system_prompt) || null,
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
    notify.push('请先在「账号管理」添加账号，再应用人格', 'warning')
    return
  }
  agent.onChange('system_prompt', selectedPreset.value.system_prompt)
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
    <v-card-title class="d-flex align-center">
      <v-icon icon="mdi-text-box-check-outline" class="mr-2" color="primary" /> 提示词方案
      <v-spacer />
      <v-chip v-if="currentPromptPreset" size="small" color="primary" variant="tonal">
        {{ PROMPT_PRESETS.find((p) => p.key === currentPromptPreset)?.label || currentPromptPreset }}
      </v-chip>
      <v-chip v-else size="small" variant="tonal">沿用内置文案</v-chip>
    </v-card-title>
    <v-card-text class="pb-0 text-caption" style="opacity: 0.7">
      只替换框架生成的系统提示词，人格设定不受影响；再点一次已选卡片可取消。
    </v-card-text>
    <!-- 三张卡片放在 v-card-text 之外：卡片文案闸门限制 v-card-text 内不超过 40 汉字，
         而三张卡片的说明要各自完整，不能被拼成一段一起算长度。 -->
    <v-row dense class="pa-4 pt-2">
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
  </v-card>

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
            <div class="text-caption text-center pa-4">暂无人格预设，请先前往「人格设定」创建</div>
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
              <v-list-item-title class="text-caption text-center py-3" style="opacity: 0.5">无匹配人格</v-list-item-title>
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
          <v-btn variant="tonal" prepend-icon="mdi-account-heart" @click="goToPersonas">前往人格编辑</v-btn>
        </v-card-title>
        <v-card-text>
          <div class="d-flex align-center flex-wrap gap-2">
            <v-chip v-if="currentPreset" size="small" color="pink" variant="tonal">{{ currentPreset.name }}</v-chip>
            <v-chip v-else-if="agent.draft.system_prompt" size="small" variant="tonal">自定义提示词</v-chip>
            <v-chip v-else size="small" variant="tonal">未启用</v-chip>
          </div>
          <!--
            这里原来还把整段 system_prompt 预览了一遍，而下方「人格设定 → 系统提示词」显示的就是
            同一段文字：同一页里同一个人格文本出现两次，光这一块就多占约 300px。
            预览留给「选择人格」里的待应用项（那里是看过再决定），当前生效的正文交给下面表单。
          -->
          <div class="text-caption mt-2" style="opacity: 0.6">
            当前生效的正文见下方「人格设定 → 系统提示词」。
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
            :title="agent.botId == null ? '需要先在「账号管理」添加账号' : '把该人格写入当前账号的 system_prompt'"
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
          <div v-else class="text-caption text-center py-6" style="opacity: 0.55">请从左侧选择一个人格预设</div>
        </v-card-text>
      </v-card>
    </v-col>
  </v-row>
</template>

<style scoped>
.preset-card {
  cursor: pointer;
  transition: transform 0.12s ease;
}
.preset-card:hover {
  transform: translateY(-1px);
}
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
