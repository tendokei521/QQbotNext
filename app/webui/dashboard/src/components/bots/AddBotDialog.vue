<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import {
  useBotsStore,
  type BotAddPayload,
  type BotConnMode,
  type ProtocolField,
} from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import { isTransportProtocol, useProtocolCatalog } from '@/composables/useProtocolCatalog'
import ProtocolHelp from '@/components/bots/ProtocolHelp.vue'

/**
 * 新增账号：先选账号类型（官方 QQ 机器人 / OneBot 正向 / OneBot 反向），再创建。
 *
 * 账号类型与连接方式都来自 `GET /api/bots/protocols`（后端目录），前端不硬编码
 * 「哪种协议有什么能力」；目录拿不到时用本地兜底目录，保证还能建 OneBot 账号。
 * 字段同样按目录渲染：官方账号填 AppID / AppSecret，OneBot 填协议端地址。
 *
 * 文案只描述**本程序**做什么，不提具体对端产品名（除非用户自己要在那里面配）：
 * 那是用户自己的另一端，本程序不该假设它是什么。
 */
const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'created', accountId: string): void
}>()

const bots = useBotsStore()
const notify = useNotifyStore()
const catalog = useProtocolCatalog()
/** 字段名→界面文案（目录给裸标识符时换成中文） */
const { fieldLabelOf } = catalog

/** 一张可选卡片：一个协议的一种连接方式（官方两种接入方式收在同一张卡里） */
interface Choice {
  key: string
  protocol: string
  /** 连接方向 forward/reverse，或官方账号的接入方式 ws/webhook */
  mode: string
  title: string
  desc: string
  icon: string
  recommended?: boolean
  /** 官方账号：卡片内再选 WebSocket / Webhook */
  transports?: { value: string; title: string; description?: string }[]
}

/** 目录 → 卡片。接入方式（ws/webhook）归一为卡片内的二级选项，连接方向各占一张卡 */
const choices = computed<Choice[]>(() => {
  const out: Choice[] = []
  for (const entry of catalog.protocols.value) {
    if (!entry.modes.length) {
      out.push({
        key: `${entry.protocol}:`,
        protocol: entry.protocol,
        mode: '',
        title: entry.label,
        desc: entry.description || '',
        icon: 'mdi-robot-outline',
      })
      continue
    }
    if (isTransportProtocol(entry)) {
      const ws = entry.modes.find((mode) => mode.value === 'ws')
      out.push({
        key: `${entry.protocol}:transport`,
        protocol: entry.protocol,
        mode: (ws || entry.modes[0]).value,
        title: entry.label,
        desc: entry.description || '',
        icon: 'mdi-robot-happy-outline',
        transports: entry.modes.map((mode) => ({
          value: mode.value,
          title: mode.label,
          description: mode.description,
        })),
      })
      continue
    }
    for (const mode of entry.modes) {
      // 标题为目录里的方式名；目录没带协议名时在说明前补上，避免只看到「本地监听」不知道是哪种号
      const protoName = entry.label.split(' ')[0]
      const baseDesc = mode.description || entry.description || ''
      const desc = mode.label.includes(protoName) ? baseDesc : [entry.label, baseDesc].filter(Boolean).join(' · ')
      out.push({
        key: `${entry.protocol}:${mode.value}`,
        protocol: entry.protocol,
        mode: mode.value,
        title: mode.label,
        desc,
        icon: mode.value === 'reverse' ? 'mdi-call-received' : 'mdi-call-made',
        recommended: mode.value === 'reverse',
      })
    }
  }
  return out
})

const selectedKey = ref('')
/** 官方账号的接入方式（ws/webhook）；OneBot 账号下不用 */
const selectedMode = ref('')
const creating = ref(false)
/** 展示名（随便改） */
const name = ref('')
/** 账号 id（唯一凭据，创建后不可改）：只允许字母/数字/下划线/连字符 */
const accountId = ref('')
/** 各协议的字段值：键就是目录里字段的 name（AppID / 地址 / 令牌…） */
const values = reactive<Record<string, string>>({})
/** 点过「创建账号」之后才把必填项标红，避免一打开就一片红 */
const attempted = ref(false)

const selected = computed(() => choices.value.find((item) => item.key === selectedKey.value) || null)
const protocol = computed(() => selected.value?.protocol || 'onebot')
const isTransportAccount = computed(() => !!selected.value?.transports?.length)
/** 官方账号：当前接入方式；OneBot：连接方向 */
const currentMode = computed(() => selectedMode.value || selected.value?.mode || 'forward')
const fields = computed<ProtocolField[]>(() => catalog.fieldsOf(protocol.value))

const currentTransport = computed(() =>
  selected.value?.transports?.find((item) => item.value === currentMode.value) || null,
)

/** 选中的卡片换协议时，把该协议的字段键补齐（不重置已填的其他协议内容） */
function ensureFieldKeys() {
  for (const field of fields.value) {
    if (values[field.name] === undefined) values[field.name] = ''
  }
}

function resetDefaults() {
  const preferred = choices.value.find((item) => item.recommended) || choices.value[0] || null
  selectedKey.value = preferred?.key || ''
  selectedMode.value = preferred?.mode || ''
  name.value = ''
  attempted.value = false
  Object.keys(values).forEach((key) => delete values[key])
  ensureFieldKeys()
  // 给一个不冲突的默认 id（形如 bot1），用户可以改；id 创建后不可变
  const taken = new Set(bots.bots.map((b) => b.id))
  let n = 1
  while (taken.has(`bot${n}`)) n += 1
  accountId.value = `bot${n}`
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    // 每次打开都回到默认推荐项：上次的选择没有记忆价值
    void catalog.load()
    resetDefaults()
  },
)

// 目录是异步到的：打开时可能还是本地兜底目录，到了之后把默认选中项对齐一次
watch(
  () => catalog.protocols.value,
  () => {
    if (!props.modelValue) return
    if (!choices.value.some((item) => item.key === selectedKey.value)) resetDefaults()
    else ensureFieldKeys()
  },
)

watch(fields, ensureFieldKeys)

const ACCOUNT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/
const idError = computed(() => {
  const v = accountId.value.trim()
  if (!v) return '请填写账号标识'
  if (!ACCOUNT_ID_RE.test(v)) return '只能用字母、数字、下划线、连字符，最多 32 个字符'
  if (['none', 'null', 'global', '_global'].includes(v.toLowerCase())) return '这个名字被系统保留，请换一个'
  return ''
})

function fieldValue(field: ProtocolField): string {
  return values[field.name] || ''
}

function fieldHint(field: ProtocolField): string {
  if (field.name === 'ws_url') {
    return currentMode.value === 'reverse'
      ? '留空用默认监听地址，创建后卡片上能看到'
      : '填协议端给出的 ws 地址'
  }
  if (field.name === 'app_secret') return field.hint || '只在本机保存，不会发到别处'
  return field.hint || ''
}

function fieldError(field: ProtocolField): string {
  if (!attempted.value || !field.required) return ''
  return fieldValue(field).trim() ? '' : '这一项要填'
}

const missingLabels = computed(() =>
  fields.value.filter((field) => field.required && !fieldValue(field).trim()).map((field) => field.label),
)

const canCreate = computed(() => !creating.value && !idError.value)

function close() {
  if (creating.value) return
  emit('update:modelValue', false)
}

async function create() {
  if (creating.value) return
  attempted.value = true
  if (idError.value) return
  if (missingLabels.value.length) {
    notify.push(`还要填：${missingLabels.value.join('、')}`, 'warning')
    return
  }
  creating.value = true
  try {
    const extra: Record<string, string> = {}
    for (const field of fields.value) {
      const value = fieldValue(field).trim()
      if (value) extra[field.name] = value
    }
    const payload = {
      id: accountId.value.trim(),
      name: name.value.trim(),
      protocol: protocol.value,
      conn_mode: (isTransportAccount.value ? 'forward' : currentMode.value) as BotConnMode,
      ...(isTransportAccount.value ? { transport: currentMode.value as 'ws' | 'webhook' } : {}),
      ...extra,
    } as BotAddPayload
    const result = await bots.addBotConfig(payload)
    notify.push('账号已添加', 'success')
    emit('update:modelValue', false)
    if (result?.id) emit('created', result.id)
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    creating.value = false
  }
}
</script>

<template>
  <v-dialog
    :model-value="props.modelValue"
    max-width="560"
    scrollable
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <v-card>
      <v-card-title class="text-h5 pa-4 pb-0 pl-6">添加账号</v-card-title>
      <v-card-text class="pa-4 pl-6">
        <div class="app-muted mb-3">先选账号类型，不确定就用 OneBot 反向（本地监听）</div>

        <div class="mode-list">
          <v-card
            v-for="opt in choices"
            :key="opt.key"
            variant="outlined"
            hover
            class="mode-card"
            :class="{ 'mode-card--active': selectedKey === opt.key }"
            @click="selectedKey = opt.key; selectedMode = opt.mode"
          >
            <v-icon :icon="opt.icon" size="22" class="mode-card__icon" />
            <div class="mode-card__text">
              <div class="mode-card__title">
                {{ opt.title }}
                <span v-if="opt.recommended" class="mode-card__rec">(推荐)</span>
              </div>
              <div class="mode-card__desc">{{ opt.desc }}</div>
            </div>
            <v-icon
              v-if="selectedKey === opt.key"
              icon="mdi-check-circle"
              color="primary"
              size="18"
              class="mode-card__check"
            />
          </v-card>
        </div>

        <div v-if="selected?.transports?.length" class="mt-3">
          <div class="app-muted mb-2">连接方式</div>
          <v-btn-toggle
            v-if="selected.transports.length > 1"
            :model-value="selectedMode"
            mandatory
            density="comfortable"
            variant="outlined"
            divided
            @update:model-value="(v: any) => selectedMode = String(v || '')"
          >
            <v-btn v-for="item in selected.transports" :key="item.value" :value="item.value" size="small">
              {{ item.title }}
            </v-btn>
          </v-btn-toggle>
          <div v-else class="connection-only">{{ selected.transports[0].title }}</div>
          <div v-if="currentTransport?.description" class="app-muted mt-2">
            {{ currentTransport.description }}
          </div>
        </div>

        <div class="app-form-grid mt-3">
          <v-text-field
            v-model="name"
            label="名称"
            placeholder="如：主号"
            hint="可留空，只影响显示"
            persistent-hint
            density="comfortable"
            hide-details="auto"
            maxlength="32"
          />
          <v-text-field
            v-model="accountId"
            label="账号标识"
            placeholder="bot1"
            :error-messages="accountId ? idError : ''"
            :hint="accountId ? '用来区分多个账号，创建后不能改' : ''"
            persistent-hint
            density="comfortable"
            hide-details="auto"
            autocomplete="off"
            spellcheck="false"
          />
        </div>

        <div v-if="fields.length" class="mt-3">
          <v-text-field
            v-for="field in fields"
            :key="field.name"
            :model-value="values[field.name]"
            :label="fieldLabelOf(field)"
            :type="field.secret || field.type === 'password' ? 'password' : 'text'"
            :hint="fieldHint(field)"
            :error-messages="fieldError(field)"
            persistent-hint
            density="comfortable"
            hide-details="auto"
            class="mb-2"
            autocomplete="off"
            spellcheck="false"
            @update:model-value="(v: string) => { values[field.name] = v }"
          />
        </div>

        <div class="mt-2">
          <ProtocolHelp :official="isTransportAccount" :mode="currentMode === 'forward' ? 'forward' : 'reverse'" />
        </div>
      </v-card-text>
      <v-card-actions class="px-4 pb-4">
        <v-spacer />
        <v-btn variant="text" :disabled="creating" @click="close">取消</v-btn>
        <v-btn
          color="primary"
          variant="flat"
          :loading="creating"
          :disabled="!canCreate"
          prepend-icon="mdi-check"
          @click="create"
        >
          创建账号
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
/* 选择器：账号类型一行一张卡，选中不填充（填充块会和「推荐」抢注意力），
   只加主色描边、浅底与右上角对勾。说明是整句，单列让每条都读得顺。 */
.mode-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.mode-card {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 14px;
  border-radius: 12px;
  cursor: pointer;
  transition: border-color .15s, box-shadow .15s, background-color .15s;
}

.mode-card:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, .06);
}

.mode-card--active {
  border-color: rgb(var(--v-theme-primary)) !important;
  background: rgba(var(--v-theme-primary), .06);
}

.mode-card__icon {
  flex: 0 0 auto;
  margin-top: 1px;
  opacity: .55;
}

.mode-card--active .mode-card__icon {
  opacity: 1;
  color: rgb(var(--v-theme-primary));
}

.mode-card__text {
  flex: 1;
  min-width: 0;
}

.mode-card__title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
  padding-right: 20px;
}

.mode-card__rec {
  font-size: 12px;
  font-weight: 400;
  color: rgb(var(--v-theme-primary));
}

.mode-card__desc {
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.45;
  color: rgba(var(--v-theme-on-surface), .6);
}

.mode-card__check {
  position: absolute;
  top: 8px;
  right: 8px;
}

/* 只有一种连接方式时不做成「单选只有一个选项」的开关，直接给一行结果 */
.connection-only {
  display: inline-flex;
  align-items: center;
  padding: 4px 12px;
  border-radius: 8px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  font-size: 13px;
}
</style>
