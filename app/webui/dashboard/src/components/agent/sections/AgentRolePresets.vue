<script setup lang="ts">
/**
 * 「人格」定制卡片（AI 对话面板用）。
 *
 * 一屏一张卡，按用户决策顺序排：
 *   1. 当前人格（只读 chip）+ 去「人格设定」编辑文案；
 *   2. 从人格预设里挑一个并应用（唯一的「应用」入口，人格设定页只负责编辑内容）；
 *   3.「提示词风格」是必选三选一，常驻展示（不再收进折叠面板）。
 *
 * 人格内容的唯一来源是「人格设定」页的角色预设库；这里只读预设列表、写
 * persona_preset_id / system_prompt，不再复制第二份预览与第二个编辑入口。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import http, { errorMessage } from '@/api/http'
import { resolvePersona, type PersonaPresetLike } from '@/agent/persona'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { useNotifyStore } from '@/stores/notify'

interface RolePreset extends PersonaPresetLike {
  description: string
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

/** 三套风格是**必选**的：空/未知值统一按默认 chat 显示（与后端兜底一致） */
const PROMPT_KEYS = PROMPT_PRESETS.map((p) => p.key)

const currentPromptPreset = computed(() => {
  const raw = String(agent.draft.prompt_preset || '').trim().toLowerCase()
  return PROMPT_KEYS.includes(raw) ? raw : 'chat'
})

/** 点哪张就是哪张，不存在"取消选择"（再点已选项不会清空） */
function selectPromptPreset(key: string) {
  if (key === currentPromptPreset.value) return
  agent.onChange('prompt_preset', key)
}

/** 三套方案正文的全局编辑入口在「设置 → 编辑系统提示词」 */
function editPromptText() {
  router.push({ path: '/settings', query: { 'prompt-presets': '1' } })
}

const filteredPresets = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return presets.value
  return presets.value.filter((p) =>
    [p.name, p.description, p.system_prompt].some((v) => (v || '').toLowerCase().includes(q)),
  )
})

const selectedPreset = computed(() => presets.value.find((p) => p.id === selectedId.value) || null)

/** 当前人格：与状态条共用 resolvePersona，三处口径一致（含"内容相同但未绑定"） */
const persona = computed(() => resolvePersona(agent.draft, presets.value))
/** 有活引用的预设 id；只有它才算"当前正在使用" */
const boundPresetId = computed(() => (persona.value.kind === 'bound' ? persona.value.presetId : ''))

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
    <v-card-title class="v-card-title persona-head">
      <v-icon icon="mdi-account-heart" class="mr-2" color="pink" /> 人格
      <v-chip
        v-if="persona.kind === 'bound'"
        class="persona-chip"
        color="pink"
        variant="tonal"
      >
        当前人格：{{ persona.label }}
      </v-chip>
      <v-chip v-else-if="persona.kind === 'same-content'" class="persona-chip" color="warning" variant="tonal">
        当前人格：{{ persona.label }}
      </v-chip>
      <v-chip v-else class="persona-chip" variant="tonal">当前人格：未选择</v-chip>
      <v-spacer />
    </v-card-title>
    <v-divider class="mx-4 mt-3" />

    <div class="px-4 pb-3 pt-3">
      <div class="d-flex align-center flex-wrap gap-2 mb-1">
        <v-icon icon="mdi-tune-variant" size="small" color="primary" />
        <span class="text-subtitle-2">提示词风格</span>
        <v-chip size="x-small" variant="tonal" color="primary">必选，三选一</v-chip>
        <v-spacer />
        <a class="text-caption text-primary text-decoration-none" href="#" @click.prevent="editPromptText">
          编辑这三套文案
        </a>
      </div>
      <div class="text-caption mb-3" style="opacity: 0.7">
        决定框架生成的系统提示词口径（聊天 / 角色扮演 / 助手）；只换说法，不改人格内容。
      </div>
      <v-row dense role="radiogroup" aria-label="提示词风格（三选一）">
        <v-col v-for="p in PROMPT_PRESETS" :key="p.key" cols="12" md="4">
          <v-card
            :variant="currentPromptPreset === p.key ? 'tonal' : 'outlined'"
            :color="currentPromptPreset === p.key ? p.color : undefined"
            class="h-100 prompt-option"
            role="radio"
            :aria-checked="currentPromptPreset === p.key"
            :tabindex="0"
            @click="selectPromptPreset(p.key)"
            @keydown.enter.prevent="selectPromptPreset(p.key)"
            @keydown.space.prevent="selectPromptPreset(p.key)"
          >
            <v-card-item>
              <template #prepend>
                <v-icon :icon="p.icon" :color="currentPromptPreset === p.key ? p.color : undefined" />
              </template>
              <v-card-title class="text-body-1">{{ p.label }}</v-card-title>
              <v-card-subtitle class="text-caption prompt-desc">{{ p.desc }}</v-card-subtitle>
              <template #append>
                <v-icon
                  :icon="currentPromptPreset === p.key ? 'mdi-radiobox-marked' : 'mdi-radiobox-blank'"
                  :color="currentPromptPreset === p.key ? p.color : undefined"
                  :title="currentPromptPreset === p.key ? '当前选择' : '点击选择'"
                />
              </template>
            </v-card-item>
          </v-card>
        </v-col>
      </v-row>
    </div>

    <v-divider class="mx-4 my-2" />

    <div class="px-4 pt-2 pb-1">
      <div class="d-flex align-center flex-wrap gap-2">
        <v-icon icon="mdi-account-heart-outline" size="small" color="pink" />
        <span class="text-subtitle-2">人格预设</span>
        <span class="text-caption ml-2" style="opacity: 0.65">选中后点「应用所选人格」，会写入当前账号</span>
        <v-spacer />
        <a class="text-caption text-primary text-decoration-none" href="#" @click.prevent="goToPersonas">
          去「人格设定」编辑
        </a>
      </div>
    </div>

    <div class="px-4 pb-2">
      <v-row dense>
        <v-col cols="12" md="7">
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
            <div class="text-caption text-center pa-4">还没有人格预设，先去「人格设定」新建。</div>
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
                <v-chip v-if="selectedPreset.id === boundPresetId" size="small" color="success" variant="tonal">
                  当前正在使用
                </v-chip>
                <v-chip
                  v-else-if="persona.kind === 'same-content' && selectedPreset.id === persona.presetId"
                  size="small"
                  color="warning"
                  variant="tonal"
                >
                  与当前内容相同（未绑定）
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
              <div class="text-caption mt-1" style="opacity: 0.6">
                内容在
                <a class="text-primary text-decoration-none" href="#" @click.prevent="goToPersonas">「人格设定」</a>
                里编辑
              </div>
            </template>
            <div v-else class="text-caption py-6 text-center" style="opacity: 0.55">请从左侧选择一个人格预设</div>
          </div>
        </v-col>
      </v-row>
    </div>

  </v-card>
</template>

<style scoped>
.persona-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.persona-chip {
  /* 默认尺寸的 chip 高度 32px、左右各 12px 内边距；这里再放宽到 18px，避免长人格名贴边 */
  padding-inline: 18px;
  height: 32px;
  font-size: 13px;
  letter-spacing: 0;
}

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

.prompt-option {
  cursor: pointer;
  transition: transform 0.12s ease, box-shadow 0.12s ease;
}

.prompt-option:hover {
  transform: translateY(-1px);
}

.prompt-option:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}

.prompt-desc {
  white-space: normal;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  margin-top: 2px;
}
</style>
