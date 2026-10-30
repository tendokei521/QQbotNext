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
import HintText from '@/components/common/HintText.vue'

const props = defineProps<{
  modelValue: boolean
  /** null = 账号默认层 */
  channelId: string | null
  title?: string
}>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

const policy = usePolicyStore()
const notify = useNotifyStore()

/** 顶层说明：让用户明确这里的规则会压过面板里的默认值 */
const priorityNote = computed(() =>
  props.channelId
    ? '这里的设置只对这个群生效，优先于账号与全局默认值。'
    : '这里的设置是账号级全局规则，对没有单独设置过的群生效。',
)

const loading = ref(false)
const saving = ref(false)
const loadError = ref('')
const resolved = ref<Record<string, any>>({})
const sources = ref<Record<string, string>>({})

type TriggerMode = 'inherit' | 'at' | 'keyword' | 'both' | 'all' | 'none'
const triggerMode = ref<TriggerMode>('inherit')
const keywords = ref('')
const probability = ref('')
const cooldown = ref('')
const debounce = ref('')
const quietHours = ref('')
const userMode = ref<'inherit' | 'blacklist' | 'whitelist'>('inherit')
const userList = ref('')

/** 触发身份档位：账号级默认 → 本群可收窄；inherit = 本层不覆盖 */
type RoleMin = 'inherit' | 'everyone' | 'member' | 'group_admin' | 'group_owner' | 'manager' | 'owner'
const roleMin = ref<RoleMin>('inherit')

const ROLE_MIN_ITEMS: { title: string; value: RoleMin }[] = [
  { title: '跟随账号默认', value: 'inherit' },
  { title: '所有人', value: 'everyone' },
  { title: '普通成员', value: 'member' },
  { title: '群管理员', value: 'group_admin' },
  { title: '群主', value: 'group_owner' },
  { title: '管理者', value: 'manager' },
  { title: '拥有者', value: 'owner' },
]

const ROLE_LABELS: Record<string, string> = {
  everyone: '所有人',
  member: '普通成员',
  group_admin: '群管理员',
  group_owner: '群主',
  manager: '管理者',
  owner: '拥有者',
}

function roleLabel(role?: string | null): string {
  if (!role) return '所有人'
  return ROLE_LABELS[role] || role
}

/** 未在账号/场景/群任一层写过 role_min 时，实际档位来自「谁能回复」里的账号级配置 */
const ROLE_MIN_INHERIT_TEXT = '跟随「谁能回复」里的身份档位'

const inheritedRoleMin = computed(() => {
  const value = (resolved.value.permission || {}).role_min
  return value ? roleLabel(String(value)) : ROLE_MIN_INHERIT_TEXT
})

/** 本层已覆盖时，预览/提示直接用本层选择；未覆盖才显示上层生效值与来源 */
const effectiveRoleMin = computed(() =>
  roleMin.value === 'inherit' ? inheritedRoleMin.value : roleLabel(roleMin.value),
)
const effectiveRoleSource = computed(() =>
  roleMin.value === 'inherit' ? sourceLabel('role_min') : '本群规则',
)
const roleMinHint = computed(() => {
  if (roleMin.value !== 'inherit') return '已在本群单独设置，保存后按这里生效'
  const value = (resolved.value.permission || {}).role_min
  return value
    ? `当前生效：${inheritedRoleMin.value}（${sourceLabel('role_min')}）`
    : `未在本群设置，按${ROLE_MIN_INHERIT_TEXT}（对所有群生效）`
})

/** 来源中文化：与「生效预览」共用一份映射 */
function sourceLabel(key: string): string {
  const source = String(sources.value[key] || 'default')
  return { default: '默认', account: '账号规则', scene: '场景默认', channel: '本群规则' }[source] || source
}

const TRIGGER_ITEMS: { title: string; value: TriggerMode }[] = [
  { title: '沿用现有', value: 'inherit' },
  { title: '仅 @ 我', value: 'at' },
  { title: '仅关键词', value: 'keyword' },
  { title: '@ 或关键词', value: 'both' },
  { title: '所有消息（按概率参与）', value: 'all' },
  { title: '都不触发（沉默）', value: 'none' },
]

const USER_MODE_ITEMS = [
  { title: '沿用现有', value: 'inherit' },
  { title: '黑名单（以下用户不响应）', value: 'blacklist' },
  { title: '白名单（仅以下用户可触发）', value: 'whitelist' },
]

const TRIGGER_MODE_LABELS: Record<string, string> = {
  at: '仅 @',
  keyword: '仅关键词',
  both: '@ 或关键词',
  all: '所有消息',
  none: '不触发',
}

const inheritedTrigger = computed(() => {
  const mode = String(resolved.value.trigger_mode || '')
  const kws: string[] = resolved.value.trigger_keyword || []
  const label = TRIGGER_MODE_LABELS[mode] || (kws.length ? '关键词' : '不触发')
  if ((mode === 'keyword' || mode === 'both') && kws.length) {
    return `${label}（${kws.join('、')}）`
  }
  if (mode === 'all') {
    return `${label}（${Number(resolved.value.trigger_probability ?? 100)}% 概率）`
  }
  return label
})

const inheritedProbability = computed(
  () => `${Number(resolved.value.trigger_probability ?? 100)}%`,
)

/** 生效预览：值 + 来源（default/account/scene/channel），回答"为什么是这样" */
const previewRows = computed(() => {
  return [
    { label: '触发方式', value: inheritedTrigger.value, source: sourceLabel('trigger_mode') },
    { label: '触发身份', value: effectiveRoleMin.value, source: effectiveRoleSource.value },
    { label: '回复冷却', value: inheritedCooldown.value, source: sourceLabel('reply_cooldown') },
    { label: '合并等待', value: inheritedDebounce.value, source: sourceLabel('message_debounce_seconds') },
    { label: '免打扰', value: inheritedQuiet.value, source: sourceLabel('quiet_hours') },
  ]
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
  const hasMode = Object.prototype.hasOwnProperty.call(config, 'trigger_mode')
  const hasAt = Object.prototype.hasOwnProperty.call(config, 'trigger_at')
  const hasKeyword = Object.prototype.hasOwnProperty.call(config, 'trigger_keyword')
  if (hasMode) {
    triggerMode.value = String(config.trigger_mode) as TriggerMode
  } else if (!hasAt && !hasKeyword) {
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
  probability.value =
    config.trigger_probability !== undefined ? String(config.trigger_probability) : ''
  cooldown.value = config.reply_cooldown !== undefined ? String(config.reply_cooldown) : ''
  debounce.value =
    config.message_debounce_seconds !== undefined ? String(config.message_debounce_seconds) : ''
  quietHours.value = config.quiet_hours !== undefined ? String(config.quiet_hours) : ''

  const permission = (row?.permission || {}) as Record<string, any>
  userMode.value = permission.user_mode === 'blacklist' || permission.user_mode === 'whitelist'
    ? permission.user_mode
    : 'inherit'
  userList.value = (permission.user_list || []).join('\n')
  roleMin.value = Object.prototype.hasOwnProperty.call(permission, 'role_min') && permission.role_min
    ? (String(permission.role_min) as RoleMin)
    : 'inherit'
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
    clear.push('trigger_mode', 'trigger_at', 'trigger_keyword', 'trigger_probability')
  } else if (mode === 'keyword' || mode === 'both') {
    config.trigger_mode = mode
    config.trigger_keyword = parseKeywords()
  } else if (mode === 'all') {
    config.trigger_mode = 'all'
    const chance = probability.value.trim()
    if (!chance) clear.push('trigger_probability')
    else config.trigger_probability = Number(chance)
  } else {
    config.trigger_mode = mode
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
  if (roleMin.value === 'inherit') {
    clear.push('role_min')
  } else {
    permission.role_min = roleMin.value
  }
  return { config, permission, clear }
}

function validate(payload: ReturnType<typeof buildPayload>): string {
  if ((triggerMode.value === 'keyword' || triggerMode.value === 'both') && !parseKeywords().length) {
    return '「关键词」模式需要至少填写一个关键词'
  }
  if (triggerMode.value === 'all' && probability.value.trim()) {
    const chance = Number(probability.value.trim())
    if (!Number.isInteger(chance) || chance < 0 || chance > 100) {
      return '参与概率应为 0-100 的整数'
    }
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
      <v-card-title class="d-flex align-center flex-wrap gap-1 dialog-title">
        <v-icon icon="mdi-tune" class="mr-2" color="primary" />
        {{ title || (channelId ? '本群专属规则' : '账号专属规则') }}
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
          <div class="text-caption mb-1" style="opacity: 0.7">
            {{ priorityNote }}
          </div>
          <div class="text-caption mb-3" style="opacity: 0.7">
            不填的项沿用现有设置
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
          <v-row v-if="triggerMode === 'all'" dense class="mb-1">
            <v-col cols="12" md="6">
              <v-text-field
                v-model="probability"
                label="参与概率（%）"
                type="number"
                min="0"
                max="100"
                placeholder="留空 = 100%"
                :hint="`当前生效：${inheritedProbability}`"
                persistent-hint
                density="comfortable"
                variant="outlined"
              />
            </v-col>
          </v-row>

          <v-select
            v-model="roleMin"
            :items="ROLE_MIN_ITEMS"
            label="触发身份"
            density="comfortable"
            variant="outlined"
            :hint="roleMinHint"
            persistent-hint
            class="mb-3"
          />
          <HintText
            class="mb-3"
            text="身份低于这个档位的成员不能触发回复；跟随账号默认则不覆盖本群。"
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
                class="mb-3"
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
                class="mb-3"
              />
            </v-col>
          </v-row>

          <v-text-field
            v-model="quietHours"
            label="免打扰时段（被动回复）"
            placeholder="如 23:00-08:00；留空 = 沿用当前值"
            :hint="`当前生效：${inheritedQuiet}`"
            persistent-hint
            density="comfortable"
            variant="outlined"
            class="mb-1"
          />
          <HintText
            class="mb-3"
            text="此段时间不回复任何消息（含被 @）"
          />

          <v-card variant="tonal" class="mb-3">
            <v-card-subtitle class="text-caption">生效预览</v-card-subtitle>
            <v-card-text class="text-caption pt-0">
              <div v-for="row in previewRows" :key="row.label" class="d-flex align-center flex-wrap gap-2">
                <span style="min-width: 64px">{{ row.label }}</span>
                <span>{{ row.value }}</span>
                <v-chip size="x-small" variant="text">来自：{{ row.source }}</v-chip>
              </div>
            </v-card-text>
          </v-card>

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
          没填的项沿用上层设置
        </v-chip>
        <v-spacer />
        <v-btn variant="text" @click="emit('update:modelValue', false)">取消</v-btn>
        <v-btn color="primary" variant="tonal" :loading="saving" :disabled="saving" @click="onSave">
          保存规则
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
/* 群名可能很长：窄屏允许换行，不要被标题行截断 */
.dialog-title {
  white-space: normal;
  word-break: break-word;
  line-height: 1.4;
}
</style>
