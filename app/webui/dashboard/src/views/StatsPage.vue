<script setup lang="ts">
/**
 * 数据统计页。
 *
 * 三类统计同级（token / 入站消息 / 出站消息），靠「类型 + 度量 + 粒度 + 堆叠维度」
 * 四个下拉 + 一组筛选条件切换；后端一个接口返回 buckets（含空桶补零）+ summary。
 *
 * 设计见 docs/DATA_STATS_DESIGN.md。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { http } from '@/api/http'

type Kind = 'llm' | 'msg_in' | 'msg_out'
type Granularity = 'minute' | 'hour' | 'day' | 'week' | 'month'

interface SeriesItem {
  value: number
  messages: number
  requests: number
  input: number
  output: number
  cache_hit: number
  cache_miss: number
  tokens: number
  reasoning: number
  bytes: number
  events: number
}
interface Bucket {
  bucket: string
  series: Record<string, SeriesItem>
  total: number
}
interface Summary {
  events: number
  requests: number
  messages: number
  triggered: number
  by_command: number
  by_module: number
  by_llm: number
  input: number
  output: number
  tokens: number
  reasoning: number
  cache_hit: number
  cache_miss: number
  cache_rate: number | null
  bytes: number
  fail_rate: number
  unknown_cache: number
}
interface QueryResp {
  meta: {
    kind: string
    metric: string
    granularity: string
    breakdown: string
    unit: string
    series_order: string[]
    truncated: number
    hidden_buckets: number
  }
  buckets: Bucket[]
  summary: Summary
}

const KIND_OPTIONS = [
  { value: 'llm', title: 'LLM 用量' },
  { value: 'msg_in', title: '收到的消息' },
  { value: 'msg_out', title: '发出的消息' },
]
const METRIC_BY_KIND: Record<Kind, { value: string; title: string }[]> = {
  llm: [
    { value: 'tokens', title: '总 token' },
    { value: 'input', title: '输入 token' },
    { value: 'output', title: '输出 token' },
    { value: 'cache_hit', title: '缓存命中 token' },
    { value: 'reasoning', title: '思考 token' },
    { value: 'requests', title: '请求次数' },
  ],
  msg_in: [
    { value: 'messages', title: '消息条数' },
    { value: 'triggered', title: '需响应条数' },
    { value: 'bytes', title: '字节数' },
  ],
  msg_out: [
    { value: 'messages', title: '消息条数' },
    { value: 'bytes', title: '字节数' },
  ],
}
const BREAKDOWN_BY_KIND: Record<Kind, { value: string; title: string }[]> = {
  llm: [
    { value: 'model', title: '按模型' },
    { value: 'api', title: '按 API' },
    { value: 'source', title: '按模块' },
    { value: 'session', title: '按会话' },
    { value: 'none', title: '不拆分' },
  ],
  msg_in: [
    { value: 'outcome', title: '按结局' },
    { value: 'session', title: '按会话' },
    { value: 'source', title: '按模块' },
    { value: 'none', title: '不拆分' },
  ],
  msg_out: [
    { value: 'session', title: '按会话' },
    { value: 'source', title: '按模块' },
    { value: 'none', title: '不拆分' },
  ],
}
const GRANULARITIES: { value: Granularity; title: string }[] = [
  { value: 'hour', title: '按小时' },
  { value: 'day', title: '按天' },
  { value: 'week', title: '按周' },
  { value: 'month', title: '按月' },
]
const RANGES = [
  { value: 1, title: '今天' },
  { value: 7, title: '近 7 天' },
  { value: 30, title: '近 30 天' },
  { value: 90, title: '近 90 天' },
  { value: 365, title: '近一年' },
]

type Pivot = 'metric' | 'series'

const kind = ref<Kind>('llm')
const metric = ref('tokens')
const pivot = ref<Pivot>('metric')
const granularity = ref<Granularity>('day')
const breakdown = ref('model')
const rangeDays = ref(30)
const botId = ref<string | null>(null)
const source = ref<string | null>(null)
const api = ref<string | null>(null)
const model = ref<string | null>(null)
const sessionId = ref<string | null>(null)
const outcome = ref<string | null>(null)

const loading = ref(false)
const error = ref('')
const data = ref<QueryResp | null>(null)
const facets = ref<Record<string, { value: string; events: number }[]>>({})

const metricOptions = computed(() => METRIC_BY_KIND[kind.value] ?? [])
const breakdownOptions = computed(() => BREAKDOWN_BY_KIND[kind.value] ?? [])

/** 时间范围（UTC 秒）。to 取明天 0 点，保证"今天"完整。 */
function rangeParams() {
  const now = new Date()
  const to = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1).getTime() / 1000
  const from = new Date(now.getFullYear(), now.getMonth(), now.getDate() - rangeDays.value + 1).getTime() / 1000
  return { from_ts: Math.floor(from), to_ts: Math.floor(to) }
}

/** 单位换算：K / M。 */
function fmt(value: number, unit: string): string {
  const n = Number(value || 0)
  if (unit === 'tokens' || unit === 'bytes') {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
    return String(n)
  }
  return n.toLocaleString()
}

function pct(value: number | null): string {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(1)}%`
}

const seriesNames = computed(() => data.value?.meta.series_order ?? [])
const buckets = computed(() => data.value?.buckets ?? [])
const unit = computed(() => data.value?.meta.unit ?? 'tokens')

/** 指标行的键（同时用于取值与表头文案）。 */
interface MetricRow {
  key: keyof SeriesItem
  label: string
}

/** 透视模式=metric 时展示哪些指标行（跟 kind 走）。 */
const metricRows = computed<MetricRow[]>(() => {
  if (kind.value === 'llm') {
    return [
      { key: 'tokens', label: '总 token' },
      { key: 'input', label: '输入' },
      { key: 'output', label: '输出' },
      { key: 'cache_hit', label: '缓存命中' },
      { key: 'requests', label: '请求次数' },
    ]
  }
  if (kind.value === 'msg_in') {
    return [
      { key: 'messages', label: '消息条数' },
      { key: 'events', label: '事件数' },
      { key: 'bytes', label: '字节数' },
    ]
  }
  return [
    { key: 'messages', label: '消息条数' },
    { key: 'bytes', label: '字节数' },
  ]
})

/** 某桶某指标的值（跨序列求和）。 */
function bucketValue(bucket: Bucket, key: keyof SeriesItem): number {
  let sum = 0
  for (const item of Object.values(bucket.series)) sum += Number(item[key] ?? 0)
  return sum
}

/** 某序列某指标的值。 */
function seriesValue(bucket: Bucket, series: string, key: keyof SeriesItem): number {
  return Number(bucket.series[series]?.[key] ?? 0)
}

/** 合计：整段区间的总量（来自 summary，不受截断影响）。 */
function metricTotal(key: keyof SeriesItem): number {
  const s = data.value?.summary
  if (!s) return 0
  if (key === 'requests') return s.requests
  if (key === 'messages') return s.messages
  if (key === 'bytes') return s.bytes
  if (key === 'tokens') return s.tokens
  if (key === 'input') return s.input
  if (key === 'output') return s.output
  if (key === 'cache_hit') return s.cache_hit
  if (key === 'reasoning') return s.reasoning
  if (key === 'events') return s.events
  return 0
}

/** 当前 `metric` 映射到 SeriesItem 的键（用于"按序列"透视的取值列）。 */
const metricKey = computed<keyof SeriesItem>(() => {
  const m = data.value?.meta.metric ?? 'tokens'
  const map: Record<string, keyof SeriesItem> = {
    tokens: 'tokens',
    input: 'input',
    output: 'output',
    cache_hit: 'cache_hit',
    reasoning: 'reasoning',
    requests: 'requests',
    messages: 'messages',
    bytes: 'bytes',
    events: 'events',
  }
  return map[m] ?? 'tokens'
})

function seriesTotal(series: string, key: keyof SeriesItem): number {
  return buckets.value.reduce((acc, b) => acc + seriesValue(b, series, key), 0)
}

/** 显示：真 0 显示 `0`；缓存命中列在"上游未报"时显示 `—`。 */
function cell(value: number, key: keyof SeriesItem): string {
  if (key === 'cache_hit' && value === 0 && (data.value?.summary.unknown_cache ?? 0) > 0) {
    return '—'
  }
  return value === 0 ? '0' : fmt(value, key === 'requests' || key === 'messages' || key === 'events' ? 'count' : unit.value)
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params: Record<string, unknown> = {
      kind: kind.value,
      metric: metric.value,
      granularity: granularity.value,
      breakdown: breakdown.value,
      skip_empty: 1,
      ...rangeParams(),
    }
    if (botId.value) params.bot_id = botId.value
    if (source.value) params.source = source.value
    if (api.value) params.api = api.value
    if (model.value) params.model = model.value
    if (sessionId.value) params.session_id = sessionId.value
    if (outcome.value) params.outcome = outcome.value
    const resp = await http.get<QueryResp>('/api/stats/query', { params })
    data.value = resp.data
  } catch (e: any) {
    error.value = e?.message ?? String(e)
    data.value = null
  } finally {
    loading.value = false
  }
}

async function loadFacets() {
  try {
    const resp = await http.get<{ facets: Record<string, { value: string; events: number }[]> }>(
      '/api/stats/facets',
      { params: { kind: kind.value, ...rangeParams() } },
    )
    facets.value = resp.data.facets ?? {}
  } catch {
    facets.value = {}
  }
}

watch([kind], () => {
  // 切换类型时把度量/拆分拉到该类型的合法值
  metric.value = METRIC_BY_KIND[kind.value][0].value
  breakdown.value = BREAKDOWN_BY_KIND[kind.value][0].value
  outcome.value = null
  loadFacets()
  load()
})
watch([metric, granularity, breakdown, rangeDays, botId, source, api, model, sessionId, outcome], load)

onMounted(() => {
  loadFacets()
  load()
})
</script>

<template>
  <div class="stats-page">
    <header class="page-head">
      <h2>数据统计</h2>
      <p class="hint">
        token 用量 / 收到的消息 / 发出的消息，三类同级；按时间与任意维度汇总。
      </p>
    </header>

    <!-- 筛选行 -->
    <v-card class="filters" variant="tonal">
      <div class="filter-row">
        <v-select v-model="kind" :items="KIND_OPTIONS" item-title="title" item-value="value"
                  label="类型" density="compact" hide-details style="max-width: 160px" />
        <v-select v-model="metric" :items="metricOptions" item-title="title" item-value="value"
                  label="度量" density="compact" hide-details style="max-width: 170px" />
        <v-select v-model="granularity" :items="GRANULARITIES" item-title="title" item-value="value"
                  label="粒度" density="compact" hide-details style="max-width: 120px" />
        <v-select v-model="breakdown" :items="breakdownOptions" item-title="title" item-value="value"
                  label="拆分" density="compact" hide-details style="max-width: 140px" />
        <v-select v-model="rangeDays" :items="RANGES" item-title="title" item-value="value"
                  label="范围" density="compact" hide-details style="max-width: 130px" />
        <v-select v-model="botId" :items="facets.bot_id ?? []" item-title="value" item-value="value"
                  label="账号" density="compact" hide-details clearable style="max-width: 160px" />
        <v-select v-model="source" :items="facets.source ?? []" item-title="value" item-value="value"
                  label="模块" density="compact" hide-details clearable style="max-width: 170px" />
        <v-select v-if="kind === 'llm'" v-model="api" :items="facets.api ?? []" item-title="value"
                  item-value="value" label="API" density="compact" hide-details clearable
                  style="max-width: 150px" />
        <v-select v-if="kind === 'llm'" v-model="model" :items="facets.model ?? []" item-title="value"
                  item-value="value" label="模型" density="compact" hide-details clearable
                  style="max-width: 190px" />
        <v-select v-if="kind === 'msg_in'" v-model="outcome" :items="['ignored', 'command', 'module', 'llm']"
                  label="结局" density="compact" hide-details clearable style="max-width: 140px" />
        <v-select v-model="sessionId" :items="facets.session_id ?? []" item-title="value" item-value="value"
                  label="会话" density="compact" hide-details clearable style="max-width: 220px" />
      </div>
    </v-card>

    <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mt-3">
      {{ error }}
    </v-alert>

    <!-- 概览卡 -->
    <div v-if="data" class="cards">
      <template v-if="kind === 'llm'">
        <div class="card"><span class="card-label">总 token</span>
          <span class="card-value">{{ fmt(data.summary.tokens, 'tokens') }}</span></div>
        <div class="card"><span class="card-label">输入</span>
          <span class="card-value">{{ fmt(data.summary.input, 'tokens') }}</span></div>
        <div class="card"><span class="card-label">输出</span>
          <span class="card-value">{{ fmt(data.summary.output, 'tokens') }}</span></div>
        <div class="card"><span class="card-label">缓存命中</span>
          <span class="card-value">{{ pct(data.summary.cache_rate) }}</span>
          <span class="card-sub">{{ fmt(data.summary.cache_hit, 'tokens') }} / {{ fmt(data.summary.cache_miss, 'tokens') }}</span></div>
        <div class="card"><span class="card-label">请求次数</span>
          <span class="card-value">{{ data.summary.requests }}</span>
          <span v-if="data.summary.unknown_cache" class="card-sub">未知缓存 {{ data.summary.unknown_cache }} 次</span></div>
      </template>
      <template v-else-if="kind === 'msg_in'">
        <div class="card"><span class="card-label">收到消息</span>
          <span class="card-value">{{ data.summary.messages }}</span></div>
        <div class="card"><span class="card-label">需响应</span>
          <span class="card-value">{{ data.summary.triggered }}</span></div>
        <div class="card"><span class="card-label">指令响应</span>
          <span class="card-value">{{ data.summary.by_command }}</span></div>
        <div class="card"><span class="card-label">模块响应</span>
          <span class="card-value">{{ data.summary.by_module }}</span></div>
        <div class="card"><span class="card-label">LLM 响应</span>
          <span class="card-value">{{ data.summary.by_llm }}</span></div>
      </template>
      <template v-else>
        <div class="card"><span class="card-label">发出消息</span>
          <span class="card-value">{{ data.summary.messages }}</span></div>
        <div class="card"><span class="card-label">字节数</span>
          <span class="card-value">{{ fmt(data.summary.bytes, 'bytes') }}</span></div>
        <div class="card"><span class="card-label">失败率</span>
          <span class="card-value">{{ pct(data.summary.fail_rate) }}</span></div>
      </template>
    </div>

    <!-- 用量表（日期做列、指标做行，参照 DeepSeek 用量页）-->
    <v-card v-if="data" class="mt-4" variant="outlined">
      <div class="chart-head">
        <span>
          {{ data.meta.granularity === 'hour' ? '按小时' : data.meta.granularity === 'day' ? '按天'
             : data.meta.granularity === 'week' ? '按周' : '按月' }}
          · {{ data.meta.metric }}
        </span>
        <span class="hint">
          <template v-if="data.meta.hidden_buckets">已省略 {{ data.meta.hidden_buckets }} 个无数据时段 · </template>
          <template v-if="data.meta.truncated">已合并 {{ data.meta.truncated }} 个较小序列 · </template>
          <span class="pivot-toggle">
            <button :class="{ on: pivot === 'metric' }" @click="pivot = 'metric'">按指标</button>
            <button :class="{ on: pivot === 'series' }" @click="pivot = 'series'">按{{ breakdown === 'none' ? '序列' : '维度' }}</button>
          </span>
        </span>
      </div>

      <div v-if="!buckets.length" class="empty-hint">该范围内没有数据</div>

      <!-- 透视 A：行 = 指标，列 = 日期 -->
      <div v-else-if="pivot === 'metric'" class="table-wrap">
        <table class="usage-table">
          <thead>
            <tr>
              <th class="sticky-col">指标</th>
              <th v-for="b in buckets" :key="b.bucket" class="num">{{ b.bucket }}</th>
              <th class="num total-col">合计</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in metricRows" :key="row.key">
              <td class="sticky-col">{{ row.label }}</td>
              <td v-for="b in buckets" :key="b.bucket" class="num">
                {{ cell(bucketValue(b, row.key), row.key) }}
              </td>
              <td class="num total-col">{{ cell(metricTotal(row.key), row.key) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 透视 B：行 = 拆维序列，列 = 日期 -->
      <div v-else class="table-wrap">
        <table class="usage-table">
          <thead>
            <tr>
              <th class="sticky-col">{{ breakdown === 'none' ? '序列' : breakdown }}</th>
              <th v-for="b in buckets" :key="b.bucket" class="num">{{ b.bucket }}</th>
              <th class="num total-col">合计</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="name in seriesNames" :key="name">
              <td class="sticky-col">{{ name || '（未指定）' }}</td>
              <td v-for="b in buckets" :key="b.bucket" class="num">
                {{ cell(seriesValue(b, name, metricKey), metricKey) }}
              </td>
              <td class="num total-col">{{ cell(seriesTotal(name, metricKey), metricKey) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </v-card>


    <div v-if="loading" class="loading">加载中…</div>
  </div>
</template>

<style scoped>
.stats-page { padding: 4px 2px 40px; }
.page-head h2 { margin: 0 0 4px; font-size: 20px; }
.hint { color: rgba(var(--v-theme-on-surface), 0.6); font-size: 12px; margin: 0; }
.filters { padding: 10px; margin-top: 12px; }
.filter-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.cards { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 12px; }
.card { min-width: 150px; padding: 12px 16px; border-radius: 10px;
        background: rgba(var(--v-theme-surface-variant), 0.35); display: flex;
        flex-direction: column; gap: 2px; }
.card-label { font-size: 12px; color: rgba(var(--v-theme-on-surface), 0.6); }
.card-value { font-size: 22px; font-weight: 600; }
.card-sub { font-size: 11px; color: rgba(var(--v-theme-on-surface), 0.55); }
.chart-head { display: flex; justify-content: space-between; align-items: center;
              font-weight: 600; padding: 10px 14px 6px; gap: 8px; flex-wrap: wrap; }
.pivot-toggle { display: inline-flex; gap: 4px; margin-left: 6px; }
.pivot-toggle button { border: 1px solid rgba(var(--v-theme-on-surface), 0.2); background: transparent;
                       color: inherit; font-size: 11px; padding: 1px 8px; border-radius: 10px; cursor: pointer; }
.pivot-toggle button.on { background: rgb(var(--v-theme-primary)); color: #fff; border-color: transparent; }
.table-wrap { overflow-x: auto; }
.usage-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.usage-table th, .usage-table td { padding: 5px 10px; border-bottom: 1px solid
                                   rgba(var(--v-theme-on-surface), 0.08); white-space: nowrap; }
.usage-table thead th { font-weight: 600; color: rgba(var(--v-theme-on-surface), 0.7);
                        position: sticky; top: 0; background: rgb(var(--v-theme-surface)); }
.usage-table .num { text-align: right; font-variant-numeric: tabular-nums; }
.usage-table .sticky-col { position: sticky; left: 0; background: rgb(var(--v-theme-surface));
                           text-align: left; font-weight: 500; }
.usage-table .total-col { font-weight: 700; border-left: 1px solid rgba(var(--v-theme-on-surface), 0.14); }
.usage-table tbody tr:hover { background: rgba(var(--v-theme-surface-variant), 0.25); }
.empty-hint { padding: 18px 14px; font-size: 12px; color: rgba(var(--v-theme-on-surface), 0.55); }
.loading { margin-top: 12px; font-size: 12px; color: rgba(var(--v-theme-on-surface), 0.6); }
</style>
