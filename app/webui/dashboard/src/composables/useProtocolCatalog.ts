import { computed, ref } from 'vue'
import http from '@/api/http'
import type { ProtocolCatalogEntry, ProtocolField, ProtocolMode } from '@/stores/bots'

/**
 * 协议目录（`GET /api/bots/protocols`）：新建账号时「选哪种账号、连接方式怎么选、
 * 要填哪几个字段」全部以后端返回为准，前端不硬编码协议能力。
 *
 * 拿不到目录（接口还没上 / 网络失败）时用本地这份兜底目录，页面照常能建 OneBot 账号，
 * 不至于白屏。目录是跨页只读数据，模块级缓存一次即可。
 */

/** 事件接入方式（官方账号二选一）的取值；用来把「连接方式」与「连接方向」区分开 */
const TRANSPORT_VALUES = ['ws', 'webhook']

const FALLBACK_PROTOCOLS: ProtocolCatalogEntry[] = [
  {
    protocol: 'qqbot',
    label: '官方 QQ 机器人',
    description: '用 QQ 开放平台建的机器人接入，不用另外装协议端',
    modes: [
      { value: 'ws', label: 'WebSocket', description: '本机连出，不用公网地址' },
      { value: 'webhook', label: 'Webhook', description: '需要公网能访问的回调地址' },
    ],
    fields: [
      {
        name: 'app_id',
        label: 'AppID',
        required: true,
        hint: '开放平台「开发设置」里的机器人 AppID',
      },
      {
        name: 'app_secret',
        label: 'AppSecret',
        type: 'password',
        secret: true,
        required: true,
        hint: '只在本机保存，不会发到别处',
      },
    ],
  },
  {
    protocol: 'onebot',
    label: 'OneBot',
    description: '连本机或局域网里的 OneBot 协议端（NapCat / LLOneBot 等）',
    modes: [
      { value: 'forward', label: 'OneBot 正向', description: '本程序主动连协议端' },
      { value: 'reverse', label: 'OneBot 反向（本地监听）', description: '本机等协议端连进来，推荐' },
    ],
    fields: [
      { name: 'ws_url', label: '协议端地址', hint: '留空用默认监听地址' },
      { name: 'access_token', label: '连接令牌（可选）', hint: '与协议端一致，可留空' },
    ],
  },
]

const FALLBACK_LABELS: Record<string, string> = {
  onebot: 'OneBot',
  qqbot: '官方 QQ 机器人',
}

const protocols = ref<ProtocolCatalogEntry[]>(FALLBACK_PROTOCOLS)
const loading = ref(false)
const loaded = ref(false)
let inflight: Promise<void> | null = null

function normalize(raw: unknown): ProtocolCatalogEntry[] {
  if (!Array.isArray(raw)) return []
  return raw
    .map((row) => row as Record<string, unknown>)
    .map((row) => ({
      protocol: String(row.protocol || ''),
      label: String(row.label || row.protocol || ''),
      description: String(row.description || ''),
      modes: Array.isArray(row.modes)
        ? (row.modes as Record<string, unknown>[]).map<ProtocolMode>((mode) => ({
            value: String(mode.value || ''),
            label: String(mode.label || mode.value || ''),
            description: String(mode.description || ''),
          }))
        : [],
      fields: Array.isArray(row.fields)
        ? (row.fields as Record<string, unknown>[]).map<ProtocolField>((field) => ({
            name: String(field.name || ''),
            label: String(field.label || field.name || ''),
            type: String(field.type || 'text'),
            required: !!field.required,
            secret: !!field.secret || String(field.type || '') === 'password',
            hint: String(field.hint || ''),
          }))
        : [],
      capabilities: (row.capabilities as ProtocolCatalogEntry['capabilities']) || undefined,
      notes: Array.isArray(row.notes) ? row.notes.map(String) : [],
    }))
    .filter((row) => row.protocol && row.label)
}

/** 拉一次协议目录（成功才覆盖本地兜底；失败保留兜底目录） */
async function load(force = false): Promise<void> {
  if (loaded.value && !force) return
  if (inflight) return inflight
  loading.value = true
  inflight = (async () => {
    try {
      const res = await http.get<{ ok?: boolean; protocols: unknown }>('/api/bots/protocols')
      const rows = normalize(res.data?.protocols)
      if (rows.length) {
        protocols.value = rows
        loaded.value = true
      }
    } catch {
      /* 目录拿不到就用本地兜底：页面照常可用，只是选项少一些 */
    } finally {
      loading.value = false
      inflight = null
    }
  })()
  return inflight
}

/** 连接方式（ws/webhook）与连接方向（forward/reverse）靠取值区分，不靠协议名判断 */
export function isTransportProtocol(entry: ProtocolCatalogEntry): boolean {
  return entry.modes.length > 0 && entry.modes.every((mode) => TRANSPORT_VALUES.includes(String(mode.value)))
}

export function entryOf(list: ProtocolCatalogEntry[], protocol: string): ProtocolCatalogEntry | null {
  return list.find((item) => item.protocol === protocol) || null
}

/** 协议显示名（缺省按 OneBot 处理，老配置不带 protocol） */
export function protocolLabelOf(list: ProtocolCatalogEntry[], protocol: string): string {
  return entryOf(list, protocol)?.label || FALLBACK_LABELS[protocol] || protocol || 'OneBot'
}

/** 官方账号的连接方式选项：目录里有就用目录的写法 */
export function transportOptionsOf(list: ProtocolCatalogEntry[]): { title: string; value: string }[] {
  const transportEntry = list.find(isTransportProtocol)
  if (transportEntry) return transportEntry.modes.map((mode) => ({ title: mode.label, value: mode.value }))
  return [
    { title: 'WebSocket', value: 'ws' },
    { title: 'Webhook', value: 'webhook' },
  ]
}

/** 字段名 → 界面文案：目录里偶尔直接给裸标识符（access_token），界面上换成中文 */
const FIELD_LABELS: Record<string, string> = {
  ws_url: '协议端地址',
  access_token: '连接令牌（可选）',
  app_id: 'AppID',
  app_secret: 'AppSecret',
}

/** 只在目录给的是裸标识符时才替换，目录自带中文/jargon-free 文案就原样用 */
const BARE_IDENTIFIER = /^[a-z][a-z0-9_]*$/

export function fieldLabelOf(field: ProtocolField): string {
  const label = String(field.label || '').trim()
  const known = FIELD_LABELS[field.name]
  if (known && (!label || BARE_IDENTIFIER.test(label))) return known
  return label || known || '未命名字段'
}

export function useProtocolCatalog() {
  void load()
  const transportOptions = computed(() => transportOptionsOf(protocols.value))

  function protocolLabel(protocol: string): string {
    return protocolLabelOf(protocols.value, protocol)
  }

  function transportLabel(value: string): string {
    const hit = transportOptions.value.find((item) => item.value === value)
    return hit ? hit.title : value
  }

  /** 新建表单字段：目录优先，目录没给字段时按协议给一份最小字段集 */
  function fieldsOf(protocol: string): ProtocolField[] {
    const entry = entryOf(protocols.value, protocol)
    if (entry && entry.fields.length) return entry.fields
    if (entry && entry.modes.length && isTransportProtocol(entry)) {
      return [
        { name: 'app_id', label: 'AppID', required: true },
        { name: 'app_secret', label: 'AppSecret', type: 'password', secret: true, required: true },
      ]
    }
    return [
      { name: 'ws_url', label: '协议端地址' },
      { name: 'access_token', label: '连接令牌（可选）' },
    ]
  }

  return {
    protocols,
    loading,
    loaded,
    load,
    transportOptions,
    protocolLabel,
    transportLabel,
    fieldLabelOf,
    fieldsOf,
  }
}
