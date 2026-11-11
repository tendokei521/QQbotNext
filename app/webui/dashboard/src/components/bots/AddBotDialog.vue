<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import {
  useBotsStore,
  type BotAddPayload,
  type BotConnMode,
  type ProtocolField,
  type ProtocolMode,
} from '@/stores/bots'
import { useNotifyStore } from '@/stores/notify'
import { errorMessage } from '@/api/http'
import { useProtocolCatalog } from '@/composables/useProtocolCatalog'
import ProtocolHelp from '@/components/bots/ProtocolHelp.vue'

/**
 * 新增账号：先选**机器人类型**（官方机器人 / 第三方机器人），再选连接方式。
 *
 * - 官方机器人：QQ 开放平台建号，填 AppID/AppSecret，连接方式 WebSocket / Webhook；
 * - 第三方机器人：走 OneBot v11 协议端（NapCat / Lagrange 等），再选正向 / 反向。
 *
 * 类型与连接方式都来自 `GET /api/bots/protocols`（后端目录），前端不硬编码
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

/** 账号类型：官方机器人 / 第三方机器人（OneBot） */
type BotKind = '' | 'official' | 'thirdparty'
const kind = ref<BotKind>('')
/** 官方机器人的接入方式 ws/webhook */
const officialTransport = ref('ws')
/** 第三方机器人的连接方向 forward/reverse */
const onebotMode = ref<BotConnMode>('reverse')

const creating = ref(false)
/** 展示名（随便改） */
const name = ref('')
/** 账号 id（唯一凭据，创建后不可改）：只允许字母/数字/下划线/连字符 */
const accountId = ref('')
/** 各协议的字段值：键就是目录里字段的 name（AppID / 地址 / 令牌…） */
const values = reactive<Record<string, string>>({})
/** 点过「创建账号」之后才把必填项标红，避免一打开就一片红 */
const attempted = ref(false)

const officialEntry = computed(() => catalog.protocols.value.find((item) => item.protocol === 'qqbot') || null)
const onebotEntry = computed(() => catalog.protocols.value.find((item) => item.protocol === 'onebot') || null)

/** 官方接入方式：目录优先，缺省给 WebSocket / Webhook 两项 */
const officialTransports = computed<ProtocolMode[]>(() => {
  const modes = officialEntry.value?.modes || []
  if (modes.length) return modes
  return [
    { value: 'ws', label: 'WebSocket', description: '本机连官方网关，不用公网地址（推荐）' },
    { value: 'webhook', label: 'Webhook', description: '需要公网能访问的回调地址' },
  ]
})

/** OneBot 连接方向：目录优先，缺省给「反向（推荐）/ 正向」两项 */
const onebotModes = computed<ProtocolMode[]>(() => {
  const modes = onebotEntry.value?.modes || []
  if (modes.length) return modes
  return [
    { value: 'reverse', label: 'OneBot 反向（本地监听）', description: '本机等协议端连进来（推荐）' },
    { value: 'forward', label: 'OneBot 正向（向外连接）', description: '本程序主动连协议端' },
  ]
})

const protocol = computed(() => (kind.value === 'official' ? 'qqbot' : kind.value === 'thirdparty' ? 'onebot' : ''))
const fields = computed<ProtocolField[]>(() => (protocol.value ? catalog.fieldsOf(protocol.value) : []))
/** 当前生效的连接方式（官方是 ws/webhook；第三方是 forward/reverse） */
const currentTransport = computed(() =>
  officialTransports.value.find((item) => item.value === officialTransport.value) || null,
)

/** 选中的类型换协议时，把该协议的字段键补齐（不重置已填的其他协议内容） */
function ensureFieldKeys() {
  for (const field of fields.value) {
    if (values[field.name] === undefined) values[field.name] = ''
  }
}

function chooseKind(next: 'official' | 'thirdparty') {
  kind.value = next
  attempted.value = false
  ensureFieldKeys()
}

function resetDefaults() {
  kind.value = ''
  officialTransport.value = 'ws'
  onebotMode.value = 'reverse'
  name.value = ''
  attempted.value = false
  Object.keys(values).forEach((key) => delete values[key])
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
    // 每次打开都从「选类型」开始：上次的选择没有记忆价值
    void catalog.load()
    resetDefaults()
  },
)

// 目录是异步到的：打开时可能还是本地兜底目录，到了之后补齐字段键即可
watch(
  () => catalog.protocols.value,
  () => {
    if (!props.modelValue) return
    ensureFieldKeys()
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
    return onebotMode.value === 'reverse'
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

const canCreate = computed(() => !creating.value && !idError.value && !!kind.value)

function close() {
  if (creating.value) return
  emit('update:modelValue', false)
}

async function create() {
  if (creating.value) return
  if (!kind.value) {
    notify.push('请先选择机器人类型', 'warning')
    return
  }
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
      conn_mode: (kind.value === 'official' ? 'forward' : onebotMode.value) as BotConnMode,
      ...(kind.value === 'official' ? { transport: officialTransport.value as 'ws' | 'webhook' } : {}),
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
    max-width="600"
    scrollable
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <v-card>
      <v-card-title class="text-h5 pa-4 pb-0 pl-6">添加账号</v-card-title>
      <v-card-text class="pa-4 pl-6">
        <div class="app-muted mb-3">先选机器人类型：官方机器人，或使用 OneBot 协议端的第三方机器人</div>

        <!-- 第一步：机器人类型（左：官方，右：第三方） -->
        <div class="kind-list">
          <v-card
            variant="outlined"
            hover
            class="kind-card"
            :class="{ 'kind-card--active': kind === 'official' }"
            @click="chooseKind('official')"
          >
            <v-icon icon="mdi-robot-happy-outline" size="26" class="kind-card__icon" />
            <div class="kind-card__text">
              <div class="kind-card__title">官方机器人</div>
              <div class="kind-card__desc">QQ 开放平台建号，填 AppID / AppSecret；默认只收 @ 消息</div>
            </div>
            <v-icon
              v-if="kind === 'official'"
              icon="mdi-check-circle"
              color="primary"
              size="18"
              class="kind-card__check"
            />
          </v-card>

          <v-card
            variant="outlined"
            hover
            class="kind-card"
            :class="{ 'kind-card--active': kind === 'thirdparty' }"
            @click="chooseKind('thirdparty')"
          >
            <v-icon icon="mdi-robot-outline" size="26" class="kind-card__icon" />
            <div class="kind-card__text">
              <div class="kind-card__title">第三方机器人</div>
              <div class="kind-card__desc">OneBot v11 协议端（NapCat / Lagrange / LLOneBot 等）</div>
            </div>
            <v-icon
              v-if="kind === 'thirdparty'"
              icon="mdi-check-circle"
              color="primary"
              size="18"
              class="kind-card__check"
            />
          </v-card>
        </div>

        <!-- 第二步（官方）：接入方式 ws / webhook -->
        <div v-if="kind === 'official'" class="mt-3">
          <div class="app-muted mb-2">连接方式</div>
          <v-btn-toggle
            v-if="officialTransports.length > 1"
            :model-value="officialTransport"
            mandatory
            density="comfortable"
            variant="outlined"
            divided
            @update:model-value="(v: any) => officialTransport = String(v || '')"
          >
            <v-btn v-for="item in officialTransports" :key="item.value" :value="item.value" size="small">
              {{ item.label }}
            </v-btn>
          </v-btn-toggle>
          <div v-else class="connection-only">{{ officialTransports[0]?.label }}</div>
          <div v-if="currentTransport?.description" class="app-muted mt-2">
            {{ currentTransport.description }}
          </div>
        </div>

        <!-- 第二步（第三方）：连接方向 正 / 反 -->
        <div v-if="kind === 'thirdparty'" class="mt-3">
          <div class="app-muted mb-2">连接方式</div>
          <div class="mode-list mode-list--two">
            <v-card
              v-for="mode in onebotModes"
              :key="mode.value"
              variant="outlined"
              hover
              class="mode-card"
              :class="{ 'mode-card--active': onebotMode === mode.value }"
              @click="onebotMode = mode.value as BotConnMode"
            >
              <v-icon
                :icon="mode.value === 'reverse' ? 'mdi-call-received' : 'mdi-call-made'"
                size="22"
                class="mode-card__icon"
              />
              <div class="mode-card__text">
                <div class="mode-card__title">
                  {{ mode.label }}
                  <span v-if="mode.value === 'reverse'" class="mode-card__rec">(推荐)</span>
                </div>
                <div class="mode-card__desc">{{ mode.description }}</div>
              </div>
              <v-icon
                v-if="onebotMode === mode.value"
                icon="mdi-check-circle"
                color="primary"
                size="18"
                class="mode-card__check"
              />
            </v-card>
          </div>
        </div>

        <!-- 第三步：名称 / 账号标识 / 协议字段（选了类型才出现） -->
        <template v-if="kind">
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
            <ProtocolHelp
              :official="kind === 'official'"
              :mode="onebotMode === 'forward' ? 'forward' : 'reverse'"
            />
          </div>
        </template>
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
/* 机器人类型：官方 / 第三方，两张卡并排；窄屏自动叠成一列 */
.kind-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

@media (max-width: 520px) {
  .kind-list {
    grid-template-columns: 1fr;
  }
}

.kind-card {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 14px;
  border-radius: 12px;
  cursor: pointer;
  transition: border-color .15s, box-shadow .15s, background-color .15s;
}

.kind-card:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, .06);
}

.kind-card--active {
  border-color: rgb(var(--v-theme-primary)) !important;
  background: rgba(var(--v-theme-primary), .06);
}

.kind-card__icon {
  flex: 0 0 auto;
  margin-top: 1px;
  opacity: .55;
}

.kind-card--active .kind-card__icon {
  opacity: 1;
  color: rgb(var(--v-theme-primary));
}

.kind-card__text {
  flex: 1;
  min-width: 0;
}

.kind-card__title {
  font-size: 15px;
  font-weight: 600;
  line-height: 1.4;
  padding-right: 20px;
}

.kind-card__desc {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: rgba(var(--v-theme-on-surface), .6);
}

.kind-card__check {
  position: absolute;
  top: 8px;
  right: 8px;
}

/* 连接方式：默认一列一条；第三方机器人的正/反两张并排 */
.mode-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.mode-list--two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

@media (max-width: 520px) {
  .mode-list--two {
    grid-template-columns: 1fr;
  }
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
