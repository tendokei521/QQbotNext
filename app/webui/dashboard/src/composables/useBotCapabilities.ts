import { computed } from 'vue'
import { useBotsStore, type BotCapabilities, type BotData } from '@/stores/bots'

/**
 * 一个账号类型「能做什么」由后端能力表决定（`GET /api/bots` 的 `capabilities`）。
 *
 * 页面只问 `supports('能力名')`，不判断协议名：以后加第三种协议只改能力表，
 * 页面不用再改一遍。
 */
export const ONEBOT_CAPABILITIES: BotCapabilities = {
  onebot_tools: true,
  send_text: true,
  send_image: true,
  recall: true,
  group_list: 'native',
  group_admin: true,
  member_list: true,
  proactive: true,
  webhook: false,
}

/**
 * 取某账号的能力表。账号还没加载完（或老接口没返回）时按 OneBot 全能力处理：
 * 首屏先按「都能用」渲染，能力表到了再收窄，避免页面闪一下禁用态。
 */
export function capabilitiesOf(bot?: BotData | null): BotCapabilities {
  const raw = bot?.capabilities
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return { ...ONEBOT_CAPABILITIES }
  return raw
}

/**
 * 某个能力是否可用。能力值可能是布尔、枚举字符串（如群列表 `native/learned/none`）
 * 或数字：只有明确的「没有」才判为不支持，未知一律按支持处理。
 */
export function supportsCapability(bot: BotData | null | undefined, key: string): boolean {
  const value = capabilitiesOf(bot)[key]
  if (value === undefined || value === null) return true
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value > 0
  const text = String(value).trim().toLowerCase()
  return text !== '' && text !== 'none' && text !== 'false' && text !== '0'
}

/** 当前选中账号的能力（页面最常用的是这个） */
export function useBotCapabilities() {
  const bots = useBotsStore()
  const bot = computed<BotData | null>(() => bots.currentBot ?? null)
  const capabilities = computed<BotCapabilities>(() => capabilitiesOf(bot.value))
  const notes = computed<string[]>(() => {
    const raw = bot.value?.capability_notes
    return Array.isArray(raw) ? raw.map(String) : []
  })
  const protocol = computed(() => String(bot.value?.protocol || 'onebot'))

  function supports(key: string): boolean {
    return supportsCapability(bot.value, key)
  }

  return { bot, capabilities, notes, protocol, supports }
}
