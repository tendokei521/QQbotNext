<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import http, { errorMessage } from '@/api/http'
import { connectSocket, onSocketMessage } from '@/api/socket'
import HintText from '@/components/common/HintText.vue'

const props = defineProps<{
  fieldKey: string
  schema: Record<string, any>
  value: any
  /** 资源引用字段需要（account_id 只对"某账号下的资源"有意义，可选） */
  botId?: string | null
}>()

const emit = defineEmits<{
  (e: 'update', value: any): void
}>()

const type = computed(() => String(props.schema.type || 'text').toLowerCase())
const isBool = computed(() => type.value === 'boolean' || type.value === 'bool')
const isNumber = computed(() => type.value === 'integer' || type.value === 'float' || type.value === 'number')
const isSelect = computed(() => type.value === 'select')
const isTextarea = computed(() => type.value === 'textarea')
const isTime = computed(() => type.value === 'time')
const isPassword = computed(() => type.value === 'password')
// 资源引用（见 app/services/resource_registry.py）：值是资源 id，下拉直接从框架拉候选
const isResource = computed(() => type.value === 'resource' || type.value === 'resources')
const isMultiResource = computed(() => type.value === 'resources')

const resourceKind = computed(() => String(props.schema.resource || '').trim())
const resourceOptions = ref<{ value: string; label: string }[]>([])
const resourceError = ref('')
const resourceLoading = ref(false)

/** 只透传 schema.filters 里声明的过滤条件（如 { provider_type: "embedding" }）。 */
const resourceParams = computed(() => {
  const params: Record<string, any> = {}
  const filters = props.schema.filters
  if (filters && typeof filters === 'object' && !Array.isArray(filters)) Object.assign(params, filters)
  if (props.botId !== undefined && props.botId !== null) params.account_id = props.botId
  return params
})

async function loadResourceOptions() {
  const kind = resourceKind.value
  resourceOptions.value = []
  resourceError.value = ''
  if (!kind) {
    resourceError.value = '这个选项暂时不可用，请刷新后重试'
    return
  }
  resourceLoading.value = true
  try {
    const { data } = await http.get(`/api/resources/${encodeURIComponent(kind)}`, { params: resourceParams.value })
    resourceOptions.value = (data?.options || []) as { value: string; label: string }[]
  } catch (e: any) {
    // 取不到候选时给一句能行动的话，不把内部概念丢给用户
    resourceError.value =
      e?.response?.status === 404 ? '没有找到可选项，请刷新后重试' : errorMessage?.(e) || '选项加载失败，请刷新后重试'
  } finally {
    resourceLoading.value = false
  }
}

/** 悬挂引用：存的值已不在候选里（资源被删）时补一条占位，避免显示成空白。 */
watch(
  [resourceOptions, () => props.value],
  () => {
    if (!isResource.value) return
    const values = isMultiResource.value
      ? (Array.isArray(props.value) ? props.value.map(String) : [])
      : props.value !== undefined && props.value !== null && props.value !== ''
        ? [String(props.value)]
        : []
    const missing = values.filter((v) => !resourceOptions.value.some((o) => String(o.value) === v))
    if (missing.length) {
      resourceOptions.value = [
        ...resourceOptions.value,
        ...missing.map((v) => ({ value: v, label: `${v}（已删除或未找到）` })),
      ]
    }
  },
  { immediate: true, deep: false },
)

watch(
  resourceKind,
  () => {
    if (isResource.value) void loadResourceOptions()
  },
  { immediate: true },
)

// 资源增删改 → 只刷新对应种类的候选；断线重连也补一次（期间可能错过变更）。
// 这样配置页开着时新建/改名/删除预设，下拉里立刻能看到。
let unsubscribeResources: (() => void) | null = null
let unsubscribeOpen: (() => void) | null = null
if (isResource.value) {
  connectSocket()
  unsubscribeResources = onSocketMessage('resources_updated', (msg: any) => {
    const kinds = Array.isArray(msg?.kinds) ? msg.kinds.map(String) : []
    if (!kinds.length || kinds.includes(resourceKind.value)) void loadResourceOptions()
  })
  unsubscribeOpen = onSocketMessage('socket_open', () => {
    if (isResource.value) void loadResourceOptions()
  })
}
onBeforeUnmount(() => {
  unsubscribeResources?.()
  unsubscribeOpen?.()
  unsubscribeResources = null
  unsubscribeOpen = null
})

const resourceModelValue = computed(() => {
  if (isMultiResource.value) {
    return Array.isArray(props.value) ? props.value.map(String) : []
  }
  return props.value === undefined || props.value === null || props.value === '' ? undefined : String(props.value)
})

function onResourceInput(v: any) {
  if (isMultiResource.value) {
    emit('update', Array.isArray(v) ? v.map(String) : [])
    return
  }
  emit('update', v ? String(v) : '')
}

const numberAttrs = computed(() => {
  const attrs: Record<string, number | undefined> = {}
  if (props.schema.min !== undefined && props.schema.min !== null) attrs.min = Number(props.schema.min)
  if (props.schema.max !== undefined && props.schema.max !== null) attrs.max = Number(props.schema.max)
  if (props.schema.step !== undefined && props.schema.step !== null) attrs.step = Number(props.schema.step)
  return attrs
})

const selectOptions = computed(() => {
  const opts = props.schema.options
  if (!opts) return []
  // dict {value: label}（旧版） 或 [{value,label}] / string[]
  if (Array.isArray(opts)) {
    return opts.map((o) => (typeof o === 'string' ? { title: o, value: o } : { title: o.label, value: o.value }))
  }
  return Object.entries(opts).map(([value, label]) => ({ title: String(label), value }))
})

const numberValue = computed(() => {
  if (props.value === undefined || props.value === null || props.value === '') return undefined
  return type.value === 'integer' ? parseInt(String(props.value), 10) : parseFloat(String(props.value))
})

function onNumberInput(v: string | number | null) {
  if (v === null || v === '') {
    emit('update', type.value === 'integer' ? 0 : 0)
    return
  }
  const n = type.value === 'integer' ? parseInt(String(v), 10) : parseFloat(String(v))
  emit('update', Number.isNaN(n) ? 0 : n)
}

function onBoolInput(v: boolean | null) {
  emit('update', !!v)
}
</script>

<template>
  <div class="field-control" :class="{ 'field-control--bool': isBool, 'field-control--number': isNumber }">
    <!-- 开关：左侧标题+说明，右侧开关，一行读完；不要把开关甩到标题下方占一整块 -->
    <template v-if="isBool">
      <div class="bool-main">
        <div v-if="schema.label" class="field-label">{{ schema.label }}</div>
        <HintText :text="schema.description" />
      </div>
      <v-switch
        class="bool-switch"
        :model-value="!!value"
        color="primary"
        inset
        hide-details
        @update:model-value="onBoolInput"
      />
    </template>

    <template v-else>
      <div v-if="schema.label" class="field-label">{{ schema.label }}</div>
      <HintText :text="schema.description" />

      <v-autocomplete
        v-if="isResource"
        :model-value="resourceModelValue"
        :items="resourceOptions"
        :multiple="isMultiResource"
        :loading="resourceLoading"
        :error-messages="resourceError"
        :placeholder="schema.placeholder || (resourceLoading ? '加载中…' : '请选择')"
        chips
        closable-chips
        clearable
        hide-details="auto"
        density="comfortable"
        item-title="label"
        item-value="value"
        @update:model-value="onResourceInput"
      />

      <v-select
        v-else-if="isSelect"
        :model-value="value !== undefined && value !== null ? String(value) : undefined"
        :items="selectOptions"
        :placeholder="schema.placeholder"
        hide-details
        density="comfortable"
        @update:model-value="(v: any) => emit('update', v ?? '')"
      />

      <v-textarea
        v-else-if="isTextarea"
        :model-value="value !== undefined && value !== null ? String(value) : ''"
        :rows="schema.rows || 3"
        :placeholder="schema.placeholder"
        hide-details
        density="comfortable"
        auto-grow
        @update:model-value="(v: string) => emit('update', v)"
      />

      <v-text-field
        v-else-if="isTime"
        :model-value="value || '00:00'"
        type="time"
        hide-details
        density="comfortable"
        style="max-width: 160px"
        @update:model-value="(v: string) => emit('update', v)"
      />

      <v-text-field
        v-else-if="isNumber"
        :model-value="numberValue"
        type="number"
        v-bind="numberAttrs"
        hide-details
        density="comfortable"
        @update:model-value="onNumberInput"
      />

      <v-text-field
        v-else
        :model-value="value !== undefined && value !== null ? String(value) : ''"
        :type="isPassword ? 'password' : 'text'"
        :placeholder="schema.placeholder || (isPassword ? '••••••••' : '')"
        hide-details
        density="comfortable"
        @update:model-value="(v: string) => emit('update', v)"
      />
    </template>
  </div>
</template>

<style scoped>
.field-control {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

/* 开关行：标题/说明在左，开关在右；开关只占自身宽度 */
.field-control--bool {
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 44px;
}

.bool-main {
  min-width: 0;
}

.bool-switch {
  flex: 0 0 auto;
  margin: 0;
}

/* 数字输入不需要铺满整个卡片宽度 */
.field-control--number :deep(.v-field) {
  max-width: 260px;
}

.field-label {
  font-size: 13.5px;
  font-weight: 500;
}
</style>
