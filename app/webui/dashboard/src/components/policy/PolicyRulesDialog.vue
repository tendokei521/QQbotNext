<script setup lang="ts">
/**
 * 高级规则对话框（账号默认层 / 某个群）。
 *
 * 交互契约（docs/permission-dialogue-policy-design.md §6.3）：
 * - **留空 = 继承**：每项都能"不覆盖"，保存时这些键从本层删掉；
 * - 继承值放在 placeholder / hint 里，用户随时知道"不填会是什么"；
 * - 只写白名单键；保存后后端统一校验，非法值（如免打扰时段格式错）会 400。
 */
import { computed, ref, watch } from 'vue'
import { usePolicyStore } from '@/stores/policy'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'

const props = defineProps<{
  modelValue: boolean
  /** null = 账号默认层 */
  channelId: string | null
  title?: string
}>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const policy = usePolicyStore()
const notify = useNotifyStore()

const loading = ref(false)
const saving = ref(false)
const loadError = ref('')
const resolved = ref<Record<string, any>>({})
const sources = ref<Record<string, string>>({})

type TriggerMode = 'inherit' | 'at' | 'keyword' | 'both' | 'none'
const triggerMode = ref<TriggerMode>('inherit')
const keywords = ref('')
const cooldown = ref('')
const debounce = ref('')
const quietHours = ref('')
const userMode = ref<'inherit' | 'blacklist' | 'whitelist'>('inherit')
const userList = ref('')

const TRIGGER_ITEMS: { title: string; value: TriggerMode }[] = [
  { title: '沿用现有', value: 'inherit' },
  { title: '仅 @ 我', value: 'at' },
  { title: '仅关键词', value: 'keyword' },
  { title: '@ 或关键词', value: 'both' },
  { title: '都不触发（沉默）', value: 'none' },
]

const USER_MODE_ITEMS = [
  { title: '沿用现有', value: 'inherit' },
  { title: '黑名单（以下用户不响应）', value: 'blacklist' },
  { title: '白名单（仅以下用户可触发）', value: 'whitelist' },
]

const inheritedTrigger = computed(() => {
  const at = Boolean(resolved.value.trigger_at)
  const kws: string[] = resolved.value.trigger_keyword || []
  if (at && kws.length) return `@ 或关键词（${kws.join('、')}）`
  if (at) return '仅 @'
  if (kws.length) return `仅关键词（${kws.join('、')}）`
  return '未开启触发'
})

const inheritedQuiet = computed(() => String(resolved.value.quiet_hours || '') || '未设置')
const inheritedCooldown = computed(() => String(resolved.value.reply_cooldown ?? '') || '未设置（无冷却）')
const inheritedDebounce = computed(
  () => String(resolved.value.message_debounce_seconds ?? '') || '未设置（不合并）',
)

function parseKeywords(): string[] {
  return keywords.value
    .split(/[,\n，、]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function parseLines(): string[] {
  return userList.value
    .split(/\n|,|，/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function applyOverride(row: Record<string, any> | null) {
  const config = (row?.config || {}) as Record<string, any>
  const hasAt = Object.prototype.hasOwnProperty.call(config, 'trigger_at')
  const hasKeyword = Object.prototype.hasOwnProperty.call(config, 'trigger_keyword')
  if (!hasAt && !hasKeyword) {
    triggerMode.value = 'inherit'
  } else if (config.trigger_at && (config.trigger_keyword || []).length) {
    triggerMode.value = 'both'
  } else if (config.trigger_at) {
    triggerMode.value = 'at'
  } else if ((config.trigger_keyword || []).length) {
    triggerMode.value = 'keyword'
  } else {
    triggerMode.value = 'none'
  }
  keywords.value = (config.trigger_keyword || []).join('、')
  cooldown.value = config.reply_cooldown !== undefined ? String(config.reply_cooldown) : ''
  debounce.value =
    config.message_debounce_seconds !== undefined ? String(config.message_debounce_seconds) : ''
  quietHours.value = config.quiet_hours !== undefined ? String(config.quiet_hours) : ''

  const permission = (row?.permission || {}) as Record<string, any>
  userMode.value = permission.user_mode === 'blacklist' || permission.user_mode === 'whitelist'
    ? permission.user_mode
    : 'inherit'
  userList.value = (permission.user_list || []).join('\n')
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const detail = await policy.getPolicy(props.channelId)
    if (!detail) {
      loadError.value = '没有选定账号'
      return
    }
    resolved.value = detail.resolved.config || {}
    sources.value = detail.resolved.sources || {}
    applyOverride(props.channelId ? detail.overrides.channel : detail.overrides.account)
  } catch (err) {
    loadError.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

function buildPayload(): { config: Record<string, any>; permission: Record<string, any>; clear: string[] } {
  const config: Record<string, any> = {}
  const clear: string[] = []
  const mode = triggerMode.value
  if (mode === 'inherit') {
    clear.push('trigger_at', 'trigger_keyword')
  } else if (mode === 'at') {
    config.trigger_at = true
    config.trigger_keyword = []
  } else if (mode === 'keyword') {
    config.trigger_at = false
    config.trigger_keyword = parseKeywords()
  } else if (mode === 'both') {
    config.trigger_at = true
    config.trigger_keyword = parseKeywords()
  } else {
    config.trigger_at = false
    config.trigger_keyword = []
  }

  const putNumber = (key: string, raw: string) => {
    const text = raw.trim()
    if (!text) {
      clear.push(key)
      return
    }
    config[key] = Number(text)
  }
  putNumber('reply_cooldown', cooldown.value)
  putNumber('message_debounce_seconds', debounce.value)

  const quiet = quietHours.value.trim()
  if (!quiet) clear.push('quiet_hours')
  else config.quiet_hours = quiet

  const permission: Record<string, any> = {}
  if (userMode.value === 'inherit') {
    clear.push('user_mode', 'user_list')
  } else {
    permission.user_mode = userMode.value
    permission.user_list = parseLines()
  }
  return { config, permission, clear }
}

function validate(payload: ReturnType<typeof buildPayload>): string {
  if (triggerMode.value === 'keyword' && !parseKeywords().length) {
    return '「仅关键词」需要至少填写一个关键词'
  }
  for (const key of ['reply_cooldown', 'message_debounce_seconds']) {
    if (payload.config[key] !== undefined && (!Number.isFinite(payload.config[key]) || payload.config[key] < 0)) {
      return '冷却和合并等待请填写 ≥0 的数字'
    }
  }
  const quiet = payload.config.quiet_hours
  if (quiet !== undefined && !/^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$/.test(quiet)) {
    return '免打扰时段格式应为 HH:MM-HH:MM（如 23:00-08:00）'
  }
  if (userMode.value !== 'inherit' && !parseLines().length) {
    return '请选择「沿用现有」，或至少填一个 QQ 号'
  }
  return ''
}

async function onSave() {
  const payload = buildPayload()
  const problem = validate(payload)
  if (problem) {
    notify.push(problem, 'warning')
    return
  }
  saving.value = true
  try {
    await policy.savePolicy(props.channelId, {
      config: payload.config,
      permission: payload.permission,
    }, payload.clear)
    notify.push('规则已保存', 'success')
    emit('update:modelValue', false)
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    saving.value = false
  }
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) void load()
  },
)
</script>

<template>
  <v-dialog
    :model-value="modelValue"
    max-width="720"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <v-card>
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-tune" class="mr-2" color="primary" />
        {{ title || (channelId ? '本群高级规则' : '账号默认规则') }}
        <v-spacer />
        <v-chip size="small" variant="tonal">
          {{ channelId ? `群 ${channelId}` : '对所有群生效' }}
        </v-chip>
      </v-card-title>

      <v-card-text>
        <v-progress-linear v-if="loading" indeterminate color="primary" class="mb-3" />
        <v-alert v-if="loadError" type="error" variant="tonal" density="compact" class="mb-3">
          {{ loadError }}
        </v-alert>

        <template v-else-if="!loading">
          <div class="text-caption mb-3" style="opacity: 0.7">
            不填的项沿用现有设置；保存后只记录你这次改动的内容。
          </div>

          <v-select
            v-model="triggerMode"
            :items="TRIGGER_ITEMS"
            label="触发方式"
            density="comfortable"
            variant="outlined"
            :hint="`当前生效：${inheritedTrigger}`"
            persistent-hint
            class="mb-3"
          />
          <v-text-field
            v-if="triggerMode === 'keyword' || triggerMode === 'both'"
            v-model="keywords"
            label="触发关键词"
            placeholder="多个关键词用「、」或换行分隔"
            density="comfortable"
            variant="outlined"
            class="mb-3"
          />

          <v-row dense>
            <v-col cols="12" md="6">
              <v-text-field
                v-model="cooldown"
                label="回复冷却（秒）"
                type="number"
                min="0"
                placeholder="留空 = 沿用当前值"
                :hint="`当前生效：${inheritedCooldown}`"
                persistent-hint
                density="comfortable"
                variant="outlined"
              />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field
                v-model="debounce"
                label="消息合并等待（秒）"
                type="number"
                min="0"
                placeholder="留空 = 沿用当前值"
                :hint="`当前生效：${inheritedDebounce}`"
                persistent-hint
                density="comfortable"
                variant="outlined"
              />
            </v-col>
          </v-row>

          <v-text-field
            v-model="quietHours"
            label="免打扰时段"
            placeholder="如 23:00-08:00；留空 = 沿用当前值"
            :hint="`当前生效：${inheritedQuiet}`"
            persistent-hint
            density="comfortable"
            variant="outlined"
            class="mb-3"
          />

          <v-select
            v-model="userMode"
            :items="USER_MODE_ITEMS"
            label="用户例外"
            density="comfortable"
            variant="outlined"
            class="mb-2"
          />
          <v-textarea
            v-if="userMode !== 'inherit'"
            v-model="userList"
            label="QQ 号（每行一个）"
            rows="3"
            auto-grow
            density="comfortable"
            variant="outlined"
          />
        </template>
      </v-card-text>

      <v-card-actions>
        <v-chip v-if="Object.keys(sources).length" size="x-small" variant="text" class="ml-2">
          {{ channelId ? '没填的项沿用本群或账号设置' : '没填的项沿用账号设置或默认值' }}
        </v-chip>
        <v-spacer />
        <v-btn variant="text" @click="emit('update:modelValue', false)">取消</v-btn>
        <v-btn color="primary" variant="tonal" :loading="saving" :disabled="loading" @click="onSave">
          保存规则
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
