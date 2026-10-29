<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useRoute } from 'vue-router'
import { useThemeStore } from '@/stores/theme'
import { useBotsStore, displayTitle, resolveAccount, resolvedBotId, type BotStatus } from '@/stores/bots'
import { useWebuiStore } from '@/stores/webui'
import { useNotifyStore } from '@/stores/notify'
import { connectSocket, onSocketMessage } from '@/api/socket'
import { errorMessage } from '@/api/http'
import MultiGroupDialog from '@/components/MultiGroupDialog.vue'
import { agentSidebarEntries } from '@/agent/sections'

const themeStore = useThemeStore()
const bots = useBotsStore()
const webui = useWebuiStore()
const notify = useNotifyStore()
const route = useRoute()

// 实时连接状态：断线时页面数据会变旧，顶部给一条常驻提示（socket.ts 负责广播）
const liveState = ref<'connecting' | 'connected' | 'reconnecting'>('connecting')
const retryCount = ref(0)
const offSocketStatus = onSocketMessage('socket_status', (msg: any) => {
  const status = String(msg?.status || '')
  if (status === 'connected' || status === 'open') {
    liveState.value = 'connected'
    retryCount.value = 0
  } else if (status === 'reconnecting' || status === 'closed') {
    liveState.value = 'reconnecting'
    retryCount.value = Number(msg?.attempt || 0) || retryCount.value
  } else if (status === 'connecting') {
    liveState.value = 'connecting'
  }
})
// 兼容旧事件名（socket_open / socket_close），两者任一到达都能更新提示
const offSocketOpen = onSocketMessage('socket_open', () => {
  liveState.value = 'connected'
  retryCount.value = 0
})
const offSocketClose = onSocketMessage('socket_close', () => {
  liveState.value = 'reconnecting'
})

// 自动打开向导只做一次（用户关掉后不再反复弹）
let setupAutoOpened = false

const router = useRouter()
const drawer = ref(false)
const rail = ref(false)
const multiGroupOpen = ref(false)
const busyAction = ref(false)
/** 顶栏「更多」菜单（窄屏收纳操作） */
const moreMenu = ref(false)

/** 侧栏分组标题：路由 watch 与激活态判断共用，避免同名字符串散落多处。 */
const AGENT_GROUP = 'AI 设置'
const TOOLS_GROUP = '高级工具'
/** 群概览入口统一用这个标题，顶栏按钮与弹窗标题一致 */
const GROUP_OVERVIEW_TITLE = '群与账号概览'

// 桌面/移动断点：桌面永久展开侧边栏，移动端可收起（导航图标切换）
const isDesktop = ref(window.innerWidth >= 768)
/** 窄屏（<720）顶栏只留账号选择、状态、主题与「更多」菜单，避免按钮挤出屏幕 */
const isNarrow = ref(window.innerWidth < 720)
function onResize() {
  isDesktop.value = window.innerWidth >= 768
  isNarrow.value = window.innerWidth < 720
  if (isDesktop.value) drawer.value = true
}

// 小屏默认收起侧边栏（抽屉模式）
if (isDesktop.value) drawer.value = true

const STATUS_TEXT: Record<BotStatus, string> = {
  connected: '已连接',
  disconnected: '离线',
  connecting: '连接中…',
  reconnecting: '重连中…',
  error: '错误',
}

interface NavChild {
  to: string
  title: string
  icon?: string
}

interface NavItem {
  /** 分组小标题（只有标题，不可点） */
  header?: string
  to?: string
  title?: string
  icon?: string
  children?: NavChild[]
}

/** 高级工具默认隐藏在实验性开关后；但用户直接打开 /tools/* 时临时显示分组（不改持久化开关） */
const isToolsActive = computed(() => route.path.startsWith('/tools'))

const navItems = computed<NavItem[]>(() => {
  // AI 设置二级菜单由 @/agent/sections 派生，避免与路由各抄一份
  const agentChildren: NavChild[] = agentSidebarEntries()
  const toolChildren: NavChild[] = [
    { to: '/tools/system', title: '系统工具' },
    { to: '/tools/onebot', title: 'OneBot 工具' },
    { to: '/tools/mcp', title: '外部工具' },
    { to: '/tools/module', title: '插件工具' },
  ]
  // 历史：曾有「高级设置」独立区（内容拆成模块后只剩空概览页，已移除），
  // 以及实验性「配置档案」入口（整套能力已下线）。
  // 「高级工具」属于实验性入口：默认隐藏，设置里打开实验性选项后才常驻。
  // 用户经直链/旧书签进入 /tools/* 时临时显示该分组并高亮当前子页，避免"进来了却没有归属"。
  const showExperimental = Boolean(webui.config.experimental?.show_experimental) || isToolsActive.value
  // 顺序按「配好一个账号」的实际路径排：运行（账号管理、模型服务、人格设定）→ 能力
  // （AI 对话面板 / 回复与权限 / AI 设置 / 功能插件 / 配置包）→ 数据 → 系统。
  return [
    { to: '/', title: '总览', icon: 'mdi-view-dashboard-outline' },
    { header: '运行' },
    { to: '/bots', title: '账号管理', icon: 'mdi-robot-outline' },
    { to: '/provider-presets', title: '模型服务', icon: 'mdi-api' },
    // 人格是"配好一个号"就要定的东西，跟账号/模型放在同一个运行分组
    { to: '/personas', title: '人格设定', icon: 'mdi-account-heart-outline' },
    { header: '能力' },
    // 日常操作台：回复开关 / 触发方式 / 权限与模型摘要
    { to: '/ai', title: 'AI 对话面板', icon: 'mdi-chat-processing-outline' },
    // 在哪回、回谁、按什么规则（原「回复设置」+「权限管理」合并）
    { to: '/reply', title: '回复与权限', icon: 'mdi-shield-account-outline' },
    { title: AGENT_GROUP, icon: 'mdi-creation-outline', children: agentChildren },
    { to: '/modules', title: '功能插件', icon: 'mdi-cube-outline' },
    { to: '/bundles', title: '配置包', icon: 'mdi-package-variant-closed' },
    ...(showExperimental
      ? [{ title: TOOLS_GROUP, icon: 'mdi-toolbox-outline', children: toolChildren }]
      : []),
    { header: '数据' },
    { to: '/sessions', title: '会话数据', icon: 'mdi-chat-outline' },
    { to: '/stats', title: '数据统计', icon: 'mdi-chart-bar' },
    { to: '/logs', title: '日志', icon: 'mdi-console' },
    { header: '系统' },
    { to: '/settings', title: '设置', icon: 'mdi-cog-outline' },
  ]
})

const openedGroups = ref<string[]>([])
/** 只有 /ai/settings/* 展开「AI 设置」分组；/ai 面板本身是独立入口 */
const isAiSettingsActive = computed(() => route.path.startsWith('/ai/settings'))

/** 导航高亮：/ai、/reply 精确匹配（避免设置页把面板也点亮），其余允许子路径 */
function isNavActive(item: NavItem): boolean {
  if (!item.to) return false
  if (item.to === '/ai' || item.to === '/reply') return route.path === item.to
  return route.path === item.to || (item.to !== '/' && route.path.startsWith(item.to + '/'))
}

/** 退出登录：会话 cookie 由服务端清除，随后 303 回独立登录页。 */
function logout() {
  window.location.href = '/logout'
}

watch(
  () => route.path,
  () => {
    if (isAiSettingsActive.value) {
      if (!openedGroups.value.includes(AGENT_GROUP)) openedGroups.value.push(AGENT_GROUP)
    } else {
      openedGroups.value = openedGroups.value.filter((g) => g !== AGENT_GROUP)
    }
    if (isToolsActive.value) {
      if (!openedGroups.value.includes(TOOLS_GROUP)) openedGroups.value.push(TOOLS_GROUP)
    } else {
      openedGroups.value = openedGroups.value.filter((g) => g !== TOOLS_GROUP)
    }
  },
  { immediate: true },
)

const botOptions = computed(() =>
  bots.bots.map((b) => {
    const acc = resolveAccount(b)
    const botId = resolvedBotId(b)
    // 标题用「名字（必要时补 QQ 号）」；内部标识只做判断，不展示给用户
    const title = displayTitle(b)
    const qq = botId ? `QQ ${botId}` : ''
    const subtitle = `${
      acc?.nickname ? `${acc.nickname}${b.status === 'connected' ? '' : ' · 未连接'}` : STATUS_TEXT[b.status]
    }${qq ? ` · ${qq}` : ''}`
    return {
      title,
      subtitle,
      value: b.id,
      status: b.status,
      bot_id: b.bot_id,
    }
  }),
)

function botStatusOf(accountId: string | null): BotStatus {
  if (accountId === null) return 'disconnected'
  return bots.bots.find((b) => b.id === accountId)?.status ?? 'disconnected'
}

function botStatusColor(status: BotStatus): string {
  return status === 'connected' ? 'success' : status === 'connecting' || status === 'reconnecting' ? 'warning' : 'error'
}

function botStatusText(status: BotStatus): string {
  return STATUS_TEXT[status]
}

// 顶栏连接状态：反向账号「监听已就绪但协议端还没连进来」是正常过渡态，
// 必须显示 warning + 接入图标（与「账号管理」卡片口径一致），不能按 isConnected 一律判红。
const currentStatusChip = computed(() => {
  const bot = bots.currentBot
  if (!bot) return { text: '离线', color: 'error', icon: 'mdi-alert-circle' }
  if (bot.conn_mode === 'reverse' && bot.status !== 'connected' && bot.listening) {
    return { text: '等待协议端接入', color: 'warning', icon: 'mdi-access-point-network' }
  }
  if (bot.status === 'connected') {
    return { text: STATUS_TEXT[bot.status], color: 'success', icon: 'mdi-check-circle' }
  }
  if (bot.status === 'connecting' || bot.status === 'reconnecting') {
    return { text: STATUS_TEXT[bot.status], color: 'warning', icon: 'mdi-progress-clock' }
  }
  return { text: STATUS_TEXT[bot.status], color: 'error', icon: 'mdi-alert-circle' }
})
const isConnected = computed(() => bots.currentBot?.status === 'connected')
const hasCurrentBot = computed(() => bots.currentId !== null)
/** 账号管理页本身有每张卡片的连接按钮，顶栏不再重复给一套 */
const isBotsRoute = computed(() => route.path.startsWith('/bots'))
const showConnectControls = computed(() => !isBotsRoute.value)
/** 窄屏状态 chip 用短文案，避免把顶栏撑出屏幕 */
const narrowStatusText = computed(() => {
  if (currentStatusChip.value.text === '等待协议端接入') return '等待接入'
  return currentStatusChip.value.text
})

async function moreAct(fn: () => Promise<unknown>, okMsg: string) {
  moreMenu.value = false
  await act(fn, okMsg)
}

function openGroupOverview() {
  moreMenu.value = false
  multiGroupOpen.value = true
}

async function onBotSelect() {
  bots.selectBot(bots.currentId!)
  try {
    await refreshScopedData()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function refreshScopedData() {
  // 账号切换后：模块数据按当前账号 id 重新拉（各页面按需再拉）
  const { useModulesStore } = await import('@/stores/modules')
  await useModulesStore().load()
}

async function act(fn: () => Promise<unknown>, okMsg: string) {
  // 防抖：连接/断开/重连进行中忽略重复点击
  if (busyAction.value) return
  busyAction.value = true
  try {
    await fn()
    notify.push(okMsg, 'success')
    await bots.fetchBots()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  } finally {
    busyAction.value = false
  }
}

onMounted(async () => {
  connectSocket()
  window.addEventListener('resize', onResize)
  try {
    await Promise.all([bots.fetchBots(), webui.load()])
    bots.restoreSelection()
    if (bots.currentBot) await refreshScopedData()
    // 首次打开（还没有任何账号）默认展示「首次配置」；之后入口在「设置」里
    if (!bots.bots.length && !setupAutoOpened) {
      setupAutoOpened = true
      router.push('/setup')
    }
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  offSocketStatus()
  offSocketOpen()
  offSocketClose()
})
</script>

<template>
  <v-layout class="rounded-0">
    <v-navigation-drawer
      v-model="drawer"
      :rail="rail"
      rail-width="72"
      width="248"
      class="app-drawer"
      :permanent="isDesktop"
    >
      <template #prepend>
        <div class="drawer-brand">
          <div class="brand-logo">
            <v-icon icon="mdi-robot-happy" color="white" />
          </div>
          <div v-if="!rail" class="brand-text">
            <div class="brand-name">QQBot Next</div>
            <div class="brand-sub">多账号 QQ 机器人</div>
          </div>
        </div>
      </template>

      <v-list nav density="comfortable" class="pa-2 nav-list">
        <template v-for="item in navItems" :key="item.header || item.title || item.to">
          <v-list-subheader v-if="item.header" class="nav-subheader">{{ item.header }}</v-list-subheader>
          <v-list-group v-else-if="item.children" v-model="openedGroups" :value="item.title">
            <template #activator="{ props }">
              <v-list-item
                v-bind="props"
                :title="item.title"
                :prepend-icon="item.icon"
                rounded="lg"
                class="nav-item"
                :class="{ 'nav-item--active': item.title === AGENT_GROUP ? isAiSettingsActive : item.title === TOOLS_GROUP ? isToolsActive : false }"
              />
            </template>
            <v-list-item
              v-for="child in item.children"
              :key="child.to"
              :to="child.to"
              :title="child.title"
              density="compact"
              class="nav-child"
              :class="{ 'nav-child--active': route.fullPath === child.to }"
            />
          </v-list-group>
          <v-list-item
            v-else
            :key="item.to"
            :to="item.to"
            :title="item.title"
            :prepend-icon="item.icon"
            rounded="lg"
            class="nav-item"
            :class="{ 'nav-item--active': isNavActive(item) }"
          />
        </template>
      </v-list>

      <template #append>
        <div class="drawer-footer">
          <v-btn variant="text" size="small" icon="mdi-chevron-left" :title="rail ? '展开侧边栏' : '收起侧边栏'" @click="rail = !rail" />
        </div>
      </template>
    </v-navigation-drawer>

    <v-app-bar flat class="app-topbar" height="56">
      <v-app-bar-nav-icon v-if="!isDesktop" @click="drawer = !drawer" />

      <div class="topbar-account">
        <v-select
          :model-value="bots.currentId"
          :items="botOptions"
          label="当前账号"
          density="compact"
          variant="outlined"
          hide-details
          class="bot-select"
          menu-icon="mdi-chevron-down"
          :menu-props="{ contentClass: 'bot-select-menu', offset: 10 }"
          @update:model-value="(v: string) => { bots.selectBot(v); onBotSelect() }"
        >
          <template #selection="{ item }">
            <div class="bot-select-value">
              <span class="bot-status-dot" :class="`is-${botStatusOf(item.value)}`" />
              <span class="bot-select-title">{{ item.title }}</span>
            </div>
          </template>
          <template #item="{ item, props }">
            <v-list-item v-bind="props" class="bot-select-option">
              <div class="bot-option">
                <span class="bot-status-dot" :class="`is-${botStatusOf(item.value)}`" />
                <div class="bot-option-main">
                  <div class="bot-option-title">{{ item.title }}</div>
                  <div class="bot-option-sub">{{ item.raw.subtitle }}</div>
                </div>
                <v-chip
                  v-if="item.raw.status"
                  size="x-small"
                  :color="botStatusColor(item.raw.status)"
                  variant="tonal"
                >
                  {{ botStatusText(item.raw.status) }}
                </v-chip>
              </div>
            </v-list-item>
          </template>
        </v-select>
        <v-chip
          class="status-chip"
          :color="currentStatusChip.color"
          size="small"
          variant="tonal"
        >
          <v-icon start :icon="currentStatusChip.icon" size="small" />
          <span>{{ isNarrow ? narrowStatusText : currentStatusChip.text }}</span>
          <span v-if="!isNarrow && bots.currentBot && resolvedBotId(bots.currentBot)" class="chip-bot-id">
            | {{ resolvedBotId(bots.currentBot) }}
          </span>
        </v-chip>
      </div>

      <v-spacer />

      <div class="topbar-actions">
        <!-- 窄屏：主题 + 「更多」菜单，避免一排按钮横向溢出 -->
        <template v-if="isNarrow">
          <v-btn
            size="small"
            variant="tonal"
            :icon="themeStore.isDark ? 'mdi-weather-night' : 'mdi-white-balance-sunny'"
            :title="themeStore.isDark ? '切换到浅色模式' : '切换到暗色模式'"
            @click="themeStore.toggle()"
          />
          <v-menu v-model="moreMenu" location="bottom end">
            <template #activator="{ props }">
              <v-btn v-bind="props" size="small" variant="tonal" icon="mdi-dots-vertical" title="更多操作" />
            </template>
            <v-list density="compact" min-width="180">
              <template v-if="showConnectControls">
                <v-list-item
                  prepend-icon="mdi-plug"
                  title="连接当前账号"
                  :disabled="isConnected || !hasCurrentBot || busyAction"
                  @click="moreAct(() => bots.connect(bots.currentId!), '已发送连接请求')"
                />
                <v-list-item
                  prepend-icon="mdi-unlink"
                  title="断开当前账号"
                  :disabled="!isConnected || busyAction"
                  @click="moreAct(() => bots.disconnect(bots.currentId!), '已断开')"
                />
                <v-list-item
                  prepend-icon="mdi-restart"
                  title="重连当前账号"
                  :disabled="!hasCurrentBot || busyAction"
                  @click="moreAct(() => bots.reconnect(bots.currentId!), '已发送重连请求')"
                />
                <v-divider class="my-1" />
              </template>
              <v-list-item prepend-icon="mdi-refresh" title="刷新状态" @click="moreAct(() => bots.fetchBots(), '状态已刷新')" />
              <v-list-item prepend-icon="mdi-layers-triple-outline" :title="GROUP_OVERVIEW_TITLE" @click="openGroupOverview" />
              <v-divider class="my-1" />
              <v-list-item prepend-icon="mdi-logout-variant" title="退出登录" @click="logout" />
            </v-list>
          </v-menu>
        </template>

        <!-- 桌面：保持一行；账号管理页隐藏连接组（卡片上的按钮才是权威入口） -->
        <template v-else>
          <div v-if="showConnectControls" class="topbar-group" role="group" aria-label="连接控制">
            <v-btn
              color="success"
              variant="tonal"
              prepend-icon="mdi-plug"
              :disabled="isConnected || !hasCurrentBot || busyAction"
              title="连接当前账号"
              @click="act(() => bots.connect(bots.currentId!), '已发送连接请求')"
            >
              <span class="btn-label">连接</span>
            </v-btn>
            <v-btn
              color="error"
              variant="tonal"
              prepend-icon="mdi-unlink"
              :disabled="!isConnected || busyAction"
              title="断开当前账号"
              @click="act(() => bots.disconnect(bots.currentId!), '已断开')"
            >
              <span class="btn-label">断开</span>
            </v-btn>
            <v-btn
              color="warning"
              variant="tonal"
              prepend-icon="mdi-restart"
              :disabled="!hasCurrentBot || busyAction"
              title="重连当前账号"
              @click="act(() => bots.reconnect(bots.currentId!), '已发送重连请求')"
            >
              <span class="btn-label">重连</span>
            </v-btn>
          </div>

          <v-btn size="small" variant="tonal" icon="mdi-refresh" title="刷新状态" @click="act(() => bots.fetchBots(), '状态已刷新')" />
          <v-btn size="small" variant="tonal" prepend-icon="mdi-layers-triple-outline" :title="GROUP_OVERVIEW_TITLE" @click="multiGroupOpen = true">
            <span class="btn-label">群与账号概览</span>
          </v-btn>
          <v-btn
            size="small"
            variant="tonal"
            :icon="themeStore.isDark ? 'mdi-weather-night' : 'mdi-white-balance-sunny'"
            :title="themeStore.isDark ? '切换到浅色模式' : '切换到暗色模式'"
            @click="themeStore.toggle()"
          />
          <!-- 登出：清会话 cookie 后回独立登录页（服务端 303 重定向，无需前端处理） -->
          <v-btn
            size="small"
            variant="tonal"
            icon="mdi-logout-variant"
            title="退出登录"
            @click="logout"
          />
        </template>
      </div>
    </v-app-bar>

    <v-main class="main-area">
      <v-alert
        v-if="liveState === 'reconnecting'"
        type="warning"
        density="compact"
        variant="tonal"
        class="live-banner"
      >
        <div class="d-flex align-center flex-wrap gap-2">
          <span>实时连接已断开{{ retryCount > 1 ? `，正在第 ${retryCount} 次重连` : '，正在自动重连' }}……页面数据可能不是最新。</span>
          <v-btn size="x-small" variant="text" prepend-icon="mdi-refresh" @click="connectSocket()">立即重试</v-btn>
        </div>
      </v-alert>
      <v-container fluid class="pa-4 pa-md-6 page-container">
        <router-view v-slot="{ Component }">
          <transition name="app-router-fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </v-container>
    </v-main>

    <MultiGroupDialog v-model="multiGroupOpen" />
  </v-layout>
</template>

<style scoped>
.nav-child {
  background: transparent !important;
}

.nav-child :deep(.v-list-item) {
  background: transparent !important;
}

.nav-child :deep(.v-list-item__overlay) {
  opacity: 0 !important;
  background: transparent !important;
}

/* hover 时也不要有背景 */
.nav-child:hover :deep(.v-list-item) {
  background: transparent !important;
}

.nav-child:hover :deep(.v-list-item__overlay) {
  opacity: 0 !important;
}

/* 侧边栏固定停留：长页面滚动时始终保持可见 */
.app-drawer {
  position: fixed !important;
  top: 0 !important;
  bottom: 0 !important;
  left: 0 !important;
  height: 100vh !important;
  border-right: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  overflow: hidden;
}

.app-drawer :deep(.v-navigation-drawer__content) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}

.drawer-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 64px;
  padding: 0 16px;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  flex-shrink: 0;
}

.brand-logo {
  width: 38px;
  height: 38px;
  border-radius: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: linear-gradient(135deg, rgb(var(--v-theme-primary)) 0%, rgb(var(--v-theme-darkprimary)) 100%);
  box-shadow: 0 4px 12px rgba(var(--v-theme-primary), 0.35);
}

.brand-name {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.01em;
  line-height: 1.2;
}

.brand-sub {
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.5);
}

/* 侧边栏导航项：悬浮/激活态 */
.nav-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

/* 分组小标题：低对比度、紧凑，只用来分区，不抢视觉 */
.nav-subheader {
  min-height: 26px !important;
  height: 26px !important;
  margin-top: 6px;
  padding-left: 16px !important;
  font-size: 11px !important;
  font-weight: 600 !important;
  letter-spacing: 0.08em !important;
  color: rgba(var(--v-theme-on-surface), 0.38) !important;
  text-transform: none !important;
}

/* 断线提示条：粘在内容区顶部，滚动时仍可见 */
.live-banner {
  margin: 12px 16px 0;
  border-radius: 12px;
}

.nav-item {
  transition: background-color 0.16s ease, color 0.16s ease;
}

.nav-item :deep(.v-list-item__overlay) {
  opacity: 0;
}

.nav-item:hover {
  background: rgba(var(--v-theme-primary), 0.08);
}

.nav-item--active {
  background: linear-gradient(90deg, rgba(var(--v-theme-primary), 0.16), rgba(var(--v-theme-primary), 0.05)) !important;
  color: rgb(var(--v-theme-primary)) !important;
  font-weight: 600;
  box-shadow: inset 3px 0 0 rgb(var(--v-theme-primary));
}

.nav-item--active :deep(.v-list-item__prepend) {
  color: rgb(var(--v-theme-primary));
}

/* 子菜单容器保持透明，根侧边栏选中效果不受影响 */
.nav-list :deep(.v-list-group__items) {
  background: transparent !important;
}

/* Agent 配置下的二级子菜单：更小字号、缩进、弱化颜色；始终透明无背景 */
.nav-child {
  font-size: 12.5px;
  min-height: 32px;
  padding-left: 30px !important;
  color: rgba(var(--v-theme-on-surface), 0.65);
  background: transparent !important;
}

.nav-child:hover {
  background: transparent !important;
}

.nav-child--active {
  color: rgb(var(--v-theme-primary)) !important;
  font-weight: 600;
  background: transparent !important;
}

.drawer-footer {
  padding: 10px 12px;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  display: flex;
  justify-content: flex-end;
  flex-shrink: 0;
}

.app-topbar {
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.app-topbar :deep(.v-toolbar__content) {
  gap: 10px;
}

.topbar-account {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 0 1 auto;
  height: 40px;
}

.bot-select {
  width: 240px;
  max-width: 32vw;
  flex: 0 1 auto;
}

/* 质感：柔和底色 + 微边框 + 轻阴影 + 聚焦光晕 */
.bot-select :deep(.v-field) {
  height: 40px;
  min-height: 40px;
  background: rgba(var(--v-theme-on-surface), 0.045) !important;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  border-radius: 12px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03), 0 4px 12px rgba(0, 0, 0, 0.05);
  transition: border-color 0.18s ease, box-shadow 0.18s ease, background-color 0.18s ease;
}

.bot-select :deep(.v-field:hover) {
  background: rgba(var(--v-theme-on-surface), 0.06) !important;
  border-color: rgba(var(--v-theme-primary), 0.35);
}

.bot-select :deep(.v-field--focused) {
  border-color: rgb(var(--v-theme-primary)) !important;
  box-shadow: 0 0 0 3px rgba(var(--v-theme-primary), 0.14), 0 4px 16px rgba(var(--v-theme-primary), 0.08);
}

.bot-select :deep(.v-field__field) {
  height: 40px;
  min-height: 40px;
  padding-left: 10px;
  overflow: hidden;
}

.bot-select :deep(.v-field__append-inner) {
  color: rgba(var(--v-theme-on-surface), 0.45);
}

.bot-select-value {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1 1 auto;
  min-width: 0;
  height: 100%;
  overflow: hidden;
}

.bot-select-title {
  flex: 1 1 auto;
  min-width: 0;
  font-weight: 600;
  font-size: 13px;
  line-height: 1.35;
  letter-spacing: -0.01em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 下拉选项统一排版（v-select 菜单 teleport 到 body，需全局生效） */
:global(.bot-select-option) {
  min-height: 48px;
}

:global(.bot-select-option .v-list-item__content) {
  width: 100%;
  min-width: 0;
}

:global(.bot-option) {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-width: 0;
}

:global(.bot-option-main) {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

:global(.bot-option-title) {
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.35;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

:global(.bot-option-sub) {
  font-size: 11.5px;
  line-height: 1.35;
  color: rgba(var(--v-theme-on-surface), 0.55);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 状态指示点：与顶栏状态色一致，带一圈柔和光晕
   （v-select 下拉是 teleport 到 body，因此用 :global 保证菜单内同样生效） */
:global(.bot-status-dot) {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  flex-shrink: 0;
  background: rgba(var(--v-theme-on-surface), 0.25);
}

:global(.bot-status-dot.is-connected) {
  background: rgb(var(--v-theme-success));
  box-shadow: 0 0 0 3px rgba(var(--v-theme-success), 0.16);
}

:global(.bot-status-dot.is-connecting),
:global(.bot-status-dot.is-reconnecting) {
  background: rgb(var(--v-theme-warning));
  box-shadow: 0 0 0 3px rgba(var(--v-theme-warning), 0.16);
}

:global(.bot-status-dot.is-error) {
  background: rgb(var(--v-theme-error));
  box-shadow: 0 0 0 3px rgba(var(--v-theme-error), 0.14);
}

/* 下拉菜单质感 */
:global(.bot-select-menu) {
  border-radius: 14px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.12);
  overflow: hidden;
}

.status-chip {
  height: 28px;
  flex: 0 0 auto;
}

.status-chip :deep(.v-chip__content) {
  gap: 2px;
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
  height: 40px;
}

/* 顶栏按钮：统一 40px 高度与内边距 */
.topbar-actions :deep(.v-btn) {
  flex: 0 0 auto;
  height: 40px;
  min-height: 40px;
  padding-left: 12px;
  padding-right: 12px;
}

.topbar-actions :deep(.v-btn--icon) {
  width: 40px;
  padding-left: 0;
  padding-right: 0;
}

/* 连接/断开/重连：胶囊分组 */
.topbar-group {
  display: flex;
  align-items: stretch;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: 10px;
  overflow: hidden;
  flex: 0 0 auto;
}

.topbar-group :deep(.v-btn) {
  border-radius: 0;
  border-right: 1px solid rgba(var(--v-theme-on-surface), 0.1);
}

.topbar-group :deep(.v-btn:last-child) {
  border-right: none;
}

.main-area {
  background: rgb(var(--v-theme-background));
}

.page-container {
  max-width: 1560px;
  margin: 0 auto;
}

/* 空间不足时：顶栏按钮先退化为纯图标，保证单行高度一致 */
@media (max-width: 1360px) {
  .btn-label {
    display: none;
  }

  .topbar-actions :deep(.v-btn:not(.v-btn--icon)) {
    width: 40px;
    padding-left: 0;
    padding-right: 0;
  }

  .bot-select {
    width: 170px;
  }
}

/* 窄屏：只留「账号选择 + 连接状态 + 主题 + 更多菜单」。
   账号栏与选择框都可以收缩（min-width:0），保证 390px 下不产生横向滚动。 */
@media (max-width: 719px) {
  .app-topbar :deep(.v-toolbar__content) {
    padding-inline: 8px;
    gap: 6px;
  }

  .topbar-account {
    flex: 1 1 auto;
    min-width: 0;
    gap: 6px;
  }

  .bot-select {
    width: auto;
    max-width: none;
    flex: 1 1 120px;
    min-width: 0;
  }

  .status-chip {
    height: 26px;
    flex: 0 0 auto;
    max-width: 42vw;
  }

  .status-chip :deep(.v-chip__content) {
    font-size: 11px;
    overflow: hidden;
    white-space: nowrap;
  }

  .topbar-actions {
    gap: 4px;
    flex: 0 0 auto;
  }
}
</style>
