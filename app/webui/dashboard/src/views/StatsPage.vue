<script setup lang="ts">
/**
 * 数据统计页 —— 版式对齐 DeepSeek 用量页。
 *
 * 结构（自上而下）：
 *   ① 大 KPI 卡：累计 Tokens / 请求次数
 *   ② 筛选条：数据维度 · 时间范围 · API · 粒度 · 清除筛选 · 导出
 *   ③ 小 KPI 卡：输入 / 输出 / 缓存命中率（消息维度换成收/发/结局）
 *   ④ 趋势图：带 y 轴刻度与 x 轴时间标签的柱图（单色橙 / 按维度堆叠）
 *   ⑤ 用量表：日期做列、指标做行（含合计列）
 *
 * 数据契约见 docs/DATA_STATS_DESIGN.md；默认 `skip_empty=1` 省略无数据时段（不画 0 灰条）。
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { http } from '@/api/http'

type Kind = 'llm' | 'msg_in' | 'msg_out'
type Granularity = 'hour' | 'day' | 'week' | 'month'
type Pivot = 'metric' | 'series'

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
    { value: 'none', title: '不拆分' },
    { value: 'model', title: '按模型' },
    { value: 'api', title: '按 API' },
  ],
  msg_in: [
    { value: 'none', title: '不拆分' },
    { value: 'outcome', title: '按结局' },
    { value: 'session', title: '按会话' },
  ],
  msg_out: [
    { value: 'none', title: '不拆分' },
    { value: 'session', title: '按会话' },
    { value: 'source', title: '按模块' },
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

const kind = ref<Kind>('llm')
const metric = ref('tokens')
const granularity = ref<Granularity>('day')
const breakdown = ref('none')
const pivot = ref<Pivot>('metric')
const rangeDays = ref(30)
const api = ref<string | null>(null)
const botId = ref<string | null>(null)
const source = ref<string | null>(null)
const model = ref<string | null>(null)
const sessionId = ref<string | null>(null)
const outcome = ref<string | null>(null)

const loading = ref(false)
const error = ref('')
const autoRefresh = ref(true)
const lastUpdated = ref<Date | null>(null)
const REFRESH_MS = 10_000
let refreshTimer: number | undefined
const data = ref<QueryResp | null>(null)
const facets = ref<Record<string, { value: string; events: number }[]>>({})

const metricOptions = computed(() => METRIC_BY_KIND[kind.value] ?? [])
const breakdownOptions = computed(() => BREAKDOWN_BY_KIND[kind.value] ?? [])
const seriesNames = computed(() => data.value?.meta.series_order ?? [])
const buckets = computed(() => data.value?.buckets ?? [])
const unit = computed(() => data.value?.meta.unit ?? 'tokens')
const summary = computed<Summary | null>(() => data.value?.summary ?? null)

/** 时间范围（UTC 秒）。to 取次日 0 点，保证"今天"完整。 */
function rangeParams() {
  const now = new Date()
  const to = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1).getTime() / 1000
  const from = new Date(
    now.getFullYear(), now.getMonth(), now.getDate() - rangeDays.value + 1,
  ).getTime() / 1000
  return { from_ts: Math.floor(from), to_ts: Math.floor(to) }
}

/** 数字格式化：token/字节按 K/M，次数按千分位。 */
function fmt(value: number, u: string): string {
  const n = Number(value || 0)
  if (u === 'count') return n.toLocaleString()
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}
function pct(value: number | null): string {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(1)}%`
}

/* ---------------- 用量表（日期做列） ---------------- */

interface MetricRow {
  key: keyof SeriesItem
  label: string
}
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
const metricKey = computed<keyof SeriesItem>(() => {
  const map: Record<string, keyof SeriesItem> = {
    tokens: 'tokens',
    input: 'input',
    output: 'output',
    cache_hit: 'cache_hit',
    reasoning: 'reasoning',
    requests: 'requests',
    messages: 'messages',
    triggered: 'messages',
    bytes: 'bytes',
    events: 'events',
  }
  return map[data.value?.meta.metric ?? 'tokens'] ?? 'tokens'
})
function bucketValue(b: Bucket, key: keyof SeriesItem): number {
  let sum = 0
  for (const item of Object.values(b.series)) sum += Number(item[key] ?? 0)
  return sum
}
function seriesValue(b: Bucket, series: string, key: keyof SeriesItem): number {
  return Number(b.series[series]?.[key] ?? 0)
}
function metricTotal(key: keyof SeriesItem): number {
  const s = summary.value
  if (!s) return 0
  const map: Record<string, number> = {
    tokens: s.tokens,
    input: s.input,
    output: s.output,
    cache_hit: s.cache_hit,
    reasoning: s.reasoning,
    requests: s.requests,
    messages: s.messages,
    bytes: s.bytes,
    events: s.events,
  }
  return map[key as string] ?? 0
}
function seriesTotal(series: string, key: keyof SeriesItem): number {
  return buckets.value.reduce((acc, b) => acc + seriesValue(b, series, key), 0)
}
/** 真 0 显示 `0`；缓存命中列在"上游未报"时显示 `—`。 */
function cell(value: number, key: keyof SeriesItem): string {
  if (key === 'cache_hit' && value === 0 && (summary.value?.unknown_cache ?? 0) > 0) return '—'
  const u = ['requests', 'messages', 'events'].includes(String(key)) ? 'count' : unit.value
  return fmt(value, u)
}

/* ---------------- 趋势图（带坐标轴） ---------------- */

const CHART_HEIGHT = 200
const PALETTE = ['#ff6b35', '#4f8ef7', '#22c55e', '#a855f7', '#f59e0b', '#14b8a6']

const chartPoints = computed(() =>
  buckets.value.map((b) => ({
    bucket: b.bucket,
    value: bucketValue(b, metricKey.value),
    bucketRef: b,
  })),
)
/** 表格用：只保留"有数据"的时段（表格里一排 0 很难看）。 */
const tableBuckets = computed(() => buckets.value.filter((b) => Object.keys(b.series).length > 0))
const chartMax = computed(() => Math.max(1, ...chartPoints.value.map((p) => p.value)))
/** y 轴三档刻度（0 / 50% / 100%），对齐截图里克制的网格线。 */
const yTicks = computed(() => [chartMax.value, chartMax.value / 2, 0])
const seriesColors = computed(() => {
  const map: Record<string, string> = {}
  seriesNames.value.forEach((name, i) => (map[name] = PALETTE[i % PALETTE.length]))
  return map
})
function barHeight(value: number): number {
  return Math.max(0, (value / chartMax.value) * CHART_HEIGHT)
}
/** x 轴标签：最多 5 个，均匀取点。 */
const xTicks = computed(() => {
  const list = chartPoints.value
  if (!list.length) return []
  if (list.length <= 5) return list.map((p) => p.bucket)
  const step = (list.length - 1) / 4
  return [0, 1, 2, 3, 4].map((i) => list[Math.round(i * step)].bucket)
})
const chartTitle = computed(() => {
  const labels: Record<string, string> = {
    tokens: '总 token',
    input: '输入 token',
    output: '输出 token',
    cache_hit: '缓存命中 token',
    reasoning: '思考 token',
    requests: '请求次数',
    messages: '消息条数',
    triggered: '需响应条数',
    bytes: '字节数',
  }
  const label = labels[data.value?.meta.metric ?? 'tokens'] ?? '用量'
  const total = chartPoints.value.reduce((a, p) => a + p.value, 0)
  return `${label} ${fmt(total, unit.value)}`
})

/* ---------------- 交互 ---------------- */

function clearFilters() {
  api.value = null
  botId.value = null
  source.value = null
  model.value = null
  sessionId.value = null
  outcome.value = null
}

/** 导出当前查询结果为 CSV（前端由 buckets 直接生成）。 */
function exportCsv() {
  const rows: string[][] = []
  rows.push(['时间', ...seriesNames.value.map((n) => n || '（未指定）'), '合计'])
  for (const b of buckets.value) {
    rows.push([
      b.bucket,
      ...seriesNames.value.map((n) => String(seriesValue(b, n, metricKey.value))),
      String(b.total),
    ])
  }
  const csv = rows
    .map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(','))
    .join('\n')
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `stats-${kind.value}-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

async function load(opts: { quiet?: boolean } = {}) {
  if (!opts.quiet) loading.value = true
  error.value = ''
  try {
    const params: Record<string, unknown> = {
      kind: kind.value,
      metric: metric.value,
      granularity: granularity.value,
      breakdown: breakdown.value,
      skip_empty: 0,   // 图表要保留所有时段的位置（不画柱但占位）
      ...rangeParams(),
    }
    if (api.value) params.api = api.value
    if (botId.value) params.bot_id = botId.value
    if (source.value) params.source = source.value
    if (model.value) params.model = model.value
    if (sessionId.value) params.session_id = sessionId.value
    if (outcome.value) params.outcome = outcome.value
    const resp = await http.get<QueryResp>('/api/stats/query', { params })
    data.value = resp.data
    lastUpdated.value = new Date()
  } catch (e: any) {
    error.value = e?.message ?? String(e)
    data.value = null
  } finally {
    if (!opts.quiet) loading.value = false
  }
}

function stopAutoRefresh() {
  if (refreshTimer !== undefined) {
    window.clearInterval(refreshTimer)
    refreshTimer = undefined
  }
}

function startAutoRefresh() {
  stopAutoRefresh()
  if (!autoRefresh.value) return
  refreshTimer = window.setInterval(() => {
    // 页面在后台时不打扰（省流量、省服务端），回到前台会立刻补一次
    if (document.visibilityState === 'visible') load({ quiet: true })
  }, REFRESH_MS)
}

/** 更新于 HH:MM:SS */
const updatedAt = computed(() => {
  const d = lastUpdated.value
  if (!d) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
})

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
  metric.value = METRIC_BY_KIND[kind.value][0].value
  breakdown.value = 'none'
  outcome.value = null
  loadFacets()
  load()
})
watch(
  [metric, granularity, breakdown, rangeDays, api, botId, source, model, sessionId, outcome],
  () => load(),
)
function onVisibilityChange() {
  if (document.visibilityState === 'visible') load({ quiet: true })
}

watch(autoRefresh, (on) => (on ? startAutoRefresh() : stopAutoRefresh()))

onMounted(() => {
  loadFacets()
  load()
  startAutoRefresh()
  document.addEventListener('visibilitychange', onVisibilityChange)
})

onUnmounted(() => {
  stopAutoRefresh()
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<template>
  <div class="stats-page">
    <!-- ① 大 KPI 卡 -->
    <div class="hero">
      <div class="hero-card tip" data-tip="当前筛选范围内所有 LLM 请求的 token 总数（输入 + 输出）">
        <span class="hero-label">累计 Tokens</span>
        <div class="hero-value">
          {{ summary ? fmt(summary.tokens, 'tokens') : '—' }}
          <span class="hero-unit">tokens</span>
        </div>
      </div>
      <div class="hero-card tip" data-tip="实际上游请求次数（含重试与回退，不含未触发的消息）">
        <span class="hero-label">API 请求次数</span>
        <div class="hero-value">
          {{ summary ? summary.requests : '—' }}
          <span class="hero-unit">次</span>
        </div>
      </div>
    </div>

    <!-- ② 筛选条 -->
    <div class="filter-bar">
      <span class="fb-label">数据维度</span>
      <v-select v-model="kind" :items="KIND_OPTIONS" item-title="title" item-value="value"
                density="compact" hide-details variant="solo" flat
                class="pill" style="max-width: 124px" />
      <v-select v-model="rangeDays" :items="RANGES" item-title="title" item-value="value"
                density="compact" hide-details variant="solo" flat
                class="pill" style="max-width: 104px" />
      <v-select v-model="metric" :items="metricOptions" item-title="title" item-value="value"
                density="compact" hide-details variant="solo" flat
                class="pill" style="max-width: 140px" />
      <v-select v-model="granularity" :items="GRANULARITIES" item-title="title" item-value="value"
                density="compact" hide-details variant="solo" flat
                class="pill" style="max-width: 100px" />
      <span class="fb-label">API</span>
      <v-select v-model="api" :items="facets.api ?? []" item-title="value" item-value="value"
                density="compact" hide-details variant="solo" flat clearable
                class="pill" style="max-width: 140px" placeholder="全部" />
      <button class="link-btn" @click="clearFilters">清除筛选条件</button>
      <v-spacer />
      <label class="live">
        <input v-model="autoRefresh" type="checkbox" />
        <span>自动刷新</span>
        <span v-if="autoRefresh && updatedAt" class="live-time">{{ updatedAt }}</span>
      </label>
      <button class="dark-btn" @click="exportCsv">导出</button>
    </div>

    <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mt-3">{{ error }}</v-alert>

    <!-- ③ 小 KPI 卡 -->
    <div v-if="summary" class="kpis">
      <template v-if="kind === 'llm'">
        <div class="kpi tip" data-tip="送给模型的输入 token（含系统提示、历史、工具结果）">
          <span class="kpi-label">输入 Tokens</span>
          <div class="kpi-value">{{ fmt(summary.input, 'tokens') }}</div>
        </div>
        <div class="kpi tip" data-tip="模型生成的输出 token（含思考 token）">
          <span class="kpi-label">输出 Tokens</span>
          <div class="kpi-value">{{ fmt(summary.output, 'tokens') }}</div>
        </div>
        <div class="kpi tip" data-tip="命中 /（命中 + 未命中）。上游不回报缓存时显示 —">
          <span class="kpi-label">缓存命中率</span>
          <div class="kpi-value">{{ pct(summary.cache_rate) }}</div>
        </div>
      </template>
      <template v-else-if="kind === 'msg_in'">
        <div class="kpi tip" data-tip="该范围内所有收到的群聊/私聊消息条数">
          <span class="kpi-label">收到消息</span>
          <div class="kpi-value">{{ summary.messages }}</div>
        </div>
        <div class="kpi tip" data-tip="其中真正需要回应的：指令 + 模块响应 + LLM 响应">
          <span class="kpi-label">需响应</span>
          <div class="kpi-value">{{ summary.triggered }}</div>
        </div>
        <div class="kpi">
          <span class="kpi-label">指令 / 模块 / LLM</span>
          <div class="kpi-value small">
            {{ summary.by_command }} / {{ summary.by_module }} / {{ summary.by_llm }}
          </div>
        </div>
      </template>
      <template v-else>
        <div class="kpi tip" data-tip="Bot 主动发出的消息条数">
          <span class="kpi-label">发出消息</span>
          <div class="kpi-value">{{ summary.messages }}</div>
        </div>
        <div class="kpi">
          <span class="kpi-label">字节数</span>
          <div class="kpi-value">{{ fmt(summary.bytes, 'bytes') }}</div>
        </div>
        <div class="kpi">
          <span class="kpi-label">失败率</span>
          <div class="kpi-value">{{ pct(summary.fail_rate) }}</div>
        </div>
      </template>
    </div>

    <!-- ④ 趋势图 -->
    <div v-if="data" class="chart-card">
      <div class="chart-head">
        <span class="chart-title">{{ chartTitle }}</span>
        <div class="seg">
          <button v-for="opt in breakdownOptions" :key="opt.value"
                  class="tip" :data-tip="opt.value === 'none' ? '所有调用合计成一根柱' : `按 ${opt.title.replace('按 ', '')} 拆成多段堆叠`"
                  :class="{ on: breakdown === opt.value }" @click="breakdown = opt.value">
            {{ opt.title }}
          </button>
        </div>
      </div>

      <div v-if="!chartPoints.length" class="empty-hint">该范围内没有数据</div>

      <template v-else>
        <div class="chart-body">
          <div class="y-axis">
            <span v-for="(tick, i) in yTicks" :key="i">{{ fmt(tick, unit) }}</span>
          </div>
          <div class="plot" :style="{ height: `${CHART_HEIGHT}px` }">
            <div class="grid">
              <span v-for="(_tick, i) in yTicks" :key="i" />
            </div>
            <div class="bars">
              <div v-for="p in chartPoints" :key="p.bucket" class="bar-slot tip"
                   :data-tip="`${p.bucket}　${fmt(p.value, unit)}`">
                <template v-if="breakdown === 'none'">
                  <div v-if="p.value > 0" class="bar single"
                       :style="{ height: `${barHeight(p.value)}px` }" />
                </template>
                <template v-else>
                  <div v-for="name in seriesNames" :key="name"
                       v-show="seriesValue(p.bucketRef, name, metricKey) > 0"
                       class="bar"
                       :style="{
                         height: `${barHeight(seriesValue(p.bucketRef, name, metricKey))}px`,
                         background: seriesColors[name],
                       }" />
                </template>
              </div>
            </div>
          </div>
        </div>
        <div class="x-axis">
          <span v-for="t in xTicks" :key="t">{{ t }}</span>
        </div>
      </template>

      <div class="chart-foot">
        <span v-if="data.meta.truncated" class="hint">已合并 {{ data.meta.truncated }} 个较小序列</span>
        <span v-if="breakdown !== 'none'" class="legend">
          <span v-for="name in seriesNames" :key="name" class="legend-item">
            <i :style="{ background: seriesColors[name] }" />{{ name || '（未指定）' }}
          </span>
        </span>
      </div>
    </div>

    <!-- ⑤ 用量表 -->
    <div v-if="data" class="table-card">
      <div class="chart-head">
        <span class="chart-title">用量明细</span>
        <div class="seg">
          <button class="tip" data-tip="行=指标（输入/输出/缓存…），列=时间"
                  :class="{ on: pivot === 'metric' }" @click="pivot = 'metric'">按指标</button>
          <button class="tip" data-tip="行=拆维序列（模型/模块/会话），列=时间"
                  :class="{ on: pivot === 'series' }" @click="pivot = 'series'">按维度</button>
        </div>
      </div>

      <div v-if="!tableBuckets.length" class="empty-hint">该范围内没有数据</div>

      <div v-else-if="pivot === 'metric'" class="table-wrap">
        <table class="usage-table">
          <thead>
            <tr>
              <th class="sticky-col tip" data-tip="每一行是一类用量口径">指标</th>
              <th v-for="b in tableBuckets" :key="b.bucket" class="num">{{ b.bucket }}</th>
              <th class="num total-col tip" data-tip="整段范围的汇总（不受表格空列省略影响）">合计</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in metricRows" :key="row.key">
              <td class="sticky-col">{{ row.label }}</td>
              <td v-for="b in tableBuckets" :key="b.bucket" class="num">
                {{ cell(bucketValue(b, row.key), row.key) }}
              </td>
              <td class="num total-col">{{ cell(metricTotal(row.key), row.key) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-else class="table-wrap">
        <table class="usage-table">
          <thead>
            <tr>
              <th class="sticky-col">{{ breakdown === 'none' ? '序列' : breakdown }}</th>
              <th v-for="b in tableBuckets" :key="b.bucket" class="num">{{ b.bucket }}</th>
              <th class="num total-col tip" data-tip="整段范围的汇总（不受表格空列省略影响）">合计</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="name in seriesNames" :key="name">
              <td class="sticky-col">{{ name || '（未指定）' }}</td>
              <td v-for="b in tableBuckets" :key="b.bucket" class="num">
                {{ cell(seriesValue(b, name, metricKey), metricKey) }}
              </td>
              <td class="num total-col">{{ cell(seriesTotal(name, metricKey), metricKey) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="loading" class="loading">加载中…</div>
  </div>
</template>

<style scoped>
.stats-page {
  padding: 4px 2px 40px;
  --card-bg: rgb(var(--v-theme-surface));
  --card-border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  --line: rgba(var(--v-theme-on-surface), 0.08);
  --muted: rgba(var(--v-theme-on-surface), 0.55);
  --accent: #ff6b35;
}

/* ① 大 KPI */
.hero { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.hero-card { background: var(--card-bg); border: var(--card-border); border-radius: 10px;
             padding: 16px 22px; }
.hero-label { font-size: 13px; color: var(--muted); }
.hero-value { font-size: 32px; font-weight: 700; line-height: 1.25; margin-top: 4px;
              font-variant-numeric: tabular-nums; }
.hero-unit { font-size: 13px; font-weight: 400; color: var(--muted); margin-left: 6px; }

/* ② 筛选条 */
.filter-bar { display: flex; align-items: center; gap: 8px; margin: 16px 0 14px;
              flex-wrap: wrap; }
.fb-label { font-size: 12px; color: var(--muted); }
.pill :deep(.v-field) { border-radius: 999px; background: var(--card-bg);
                       box-shadow: none; border: var(--card-border); }
.pill :deep(.v-field__input) { min-height: 30px; padding-top: 0; padding-bottom: 0;
                               font-size: 12px; }
.link-btn { background: none; border: none; color: var(--muted); font-size: 12px;
            cursor: pointer; padding: 4px 6px; }
.link-btn:hover { color: rgb(var(--v-theme-primary)); text-decoration: underline; }
.live { display: inline-flex; align-items: center; gap: 6px; font-size: 12px;
        color: var(--muted); cursor: pointer; user-select: none; }
.live input { width: 13px; height: 13px; accent-color: var(--accent); cursor: pointer; }
.live-time { font-variant-numeric: tabular-nums; opacity: 0.75; }

/* 即时悬浮提示：CSS 自绘，零延迟（原生 title 要等约 1 秒） */
.tip { position: relative; }
.tip:hover::after {
  content: attr(data-tip);
  position: absolute;
  left: 50%;
  bottom: calc(100% + 6px);
  transform: translateX(-50%);
  background: rgba(20, 22, 26, 0.94);
  color: #fff;
  font-size: 11px;
  font-weight: 400;
  line-height: 1.4;
  padding: 5px 9px;
  border-radius: 6px;
  white-space: nowrap;
  z-index: 50;
  pointer-events: none;
  opacity: 1;
  transition: none;          /* ★ 关键：不做淡入，放上去立刻显示 */
}
.tip:hover { z-index: 51; }
.dark-btn { background: #1f2328; color: #fff; border: none; border-radius: 999px;
            padding: 7px 18px; font-size: 12px; cursor: pointer; }
.dark-btn:hover { background: #33383f; }

/* ③ 小 KPI */
.kpis { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.kpi { background: var(--card-bg); border: var(--card-border); border-radius: 10px;
       padding: 14px 18px; }
.kpi-label { font-size: 12px; color: var(--muted); }
.kpi-value { font-size: 24px; font-weight: 600; margin-top: 4px;
             font-variant-numeric: tabular-nums; }
.kpi-value.small { font-size: 16px; }

/* ④ 图表 */
.chart-card, .table-card { margin-top: 16px; background: var(--card-bg);
                           border: var(--card-border); border-radius: 10px;
                           padding: 14px 16px 12px; }
.chart-head { display: flex; align-items: center; justify-content: space-between;
              gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.chart-title { font-size: 13px; font-weight: 600; }
.seg { display: inline-flex; background: transparent; border: var(--card-border);
       border-radius: 999px; padding: 2px; gap: 2px; }
.seg button { border: none; background: transparent; color: var(--muted); font-size: 11px;
              padding: 3px 10px; border-radius: 999px; cursor: pointer; white-space: nowrap; }
.seg button.on { background: rgba(var(--v-theme-on-surface), 0.08);
                 color: rgb(var(--v-theme-on-surface)); }

.chart-body { display: flex; gap: 8px; }
.y-axis { display: flex; flex-direction: column; justify-content: space-between;
          font-size: 10px; color: var(--muted); padding: 0; min-width: 46px;
          text-align: right; font-variant-numeric: tabular-nums; }
.plot { position: relative; flex: 1; }
.grid { position: absolute; inset: 0; display: flex; flex-direction: column;
        justify-content: space-between; pointer-events: none; }
.grid span { border-top: 1px solid var(--line); height: 0; }
.bars { position: absolute; inset: 0; display: flex; align-items: flex-end;
        gap: 2px; padding: 0 2px; }
.bar-slot { flex: 1; display: flex; align-items: flex-end; gap: 1px; height: 100%; }
.bar { flex: 1; border-radius: 2px 2px 0 0; }
.bar.single { background: var(--accent); }
.x-axis { display: flex; justify-content: space-between; font-size: 10px;
          color: var(--muted); margin: 6px 0 0 54px; }
.chart-foot { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-top: 8px; }
.hint { font-size: 11px; color: var(--muted); }
.legend { display: inline-flex; flex-wrap: wrap; gap: 10px; font-size: 11px; color: var(--muted); }
.legend-item { display: inline-flex; align-items: center; gap: 4px; }
.legend-item i { width: 9px; height: 9px; border-radius: 2px; display: inline-block; }
.empty-hint { padding: 20px 4px; font-size: 12px; color: var(--muted); }

/* ⑤ 用量表 */
.table-wrap { overflow-x: auto; }
.usage-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.usage-table th, .usage-table td { padding: 6px 10px; border-bottom: 1px solid var(--line);
                                   white-space: nowrap; }
.usage-table thead th { font-weight: 600; color: var(--muted); position: sticky; top: 0;
                        background: var(--card-bg); }
.usage-table .num { text-align: right; font-variant-numeric: tabular-nums; }
.usage-table .sticky-col { position: sticky; left: 0; background: var(--card-bg);
                           text-align: left; font-weight: 500; }
.usage-table .total-col { font-weight: 700;
                          border-left: 1px solid rgba(var(--v-theme-on-surface), 0.14); }
.usage-table tbody tr:hover { background: rgba(var(--v-theme-surface-variant), 0.3); }
.loading { margin-top: 12px; font-size: 12px; color: var(--muted); }
</style>
