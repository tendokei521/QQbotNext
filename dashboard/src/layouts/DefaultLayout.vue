<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useThemeStore } from '@/stores/theme'
import { useBotsStore, type BotStatus } from '@/stores/bots'
import { useWebuiStore } from '@/stores/webui'
import { useNotifyStore } from '@/stores/notify'
import { useAgentNavStore } from '@/stores/agentNav'
import { connectSocket } from '@/api/socket'
import { errorMessage } from '@/api/http'
import MultiGroupDialog from '@/components/MultiGroupDialog.vue'

const themeStore = useThemeStore()
const bots = useBotsStore()
const webui = useWebuiStore()
const notify = useNotifyStore()
const agentNav = useAgentNavStore()
const route = useRoute()

const drawer = ref(false)
const rail = ref(false)
const multiGroupOpen = ref(false)
const busyAction = ref(false)

// 桌面/移动断点：桌面永久展开侧边栏，移动端可收起（导航图标切换）
const isDesktop = ref(window.innerWidth >= 768)
function onResize() {
  isDesktop.value = window.innerWidth >= 768
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
  to?: string
  title: string
  icon?: string
  children?: NavChild[]
}

const navItems = computed<NavItem[]>(() => {
  const showExperimental = !!webui.config.experimental?.show_experimental
  const agentChildren: NavChild[] = agentNav.sections.length
    ? agentNav.sections.map((s) => ({ to: s.to, title: s.title }))
    : [
        { to: '/agent?section=sec-permission', title: '响应范围控制' },
        { to: '/agent?section=sec-models', title: 'Provider 模型池' },
        { to: '/agent?section=sec-agent-panels', title: '定时任务 / 主动消息' },
      ]
  // 长期记忆虽标记为实验性，但作为常驻入口保留
  agentChildren.push({ to: '/agent/memory', title: 'Agent 长期记忆' })
  const items: NavItem[] = [
    { to: '/', title: '总览', icon: 'mdi-view-dashboard-outline' },
    { to: '/bots', title: '账号管理', icon: 'mdi-robot-outline' },
    { to: '/modules', title: '功能模块', icon: 'mdi-cube-outline' },
    { to: '/provider-presets', title: 'Provider 预设', icon: 'mdi-api' },
    { title: 'Agent 面板', icon: 'mdi-creation-outline', children: agentChildren },
    { to: '/logs', title: '日志', icon: 'mdi-console' },
    { to: '/settings', title: '设置', icon: 'mdi-cog-outline' },
  ]
  if (showExperimental) {
    items.splice(6, 0, { to: '/config-profiles', title: '配置档案', icon: 'mdi-book-multiple' })
  }
  return items
})

const openedGroups = ref<string[]>([])
const isAgentActive = computed(() => route.path.startsWith('/agent'))

watch(
  () => route.path,
  () => {
    if (isAgentActive.value) {
      if (!openedGroups.value.includes('Agent 面板')) openedGroups.value.push('Agent 面板')
    } else {
      openedGroups.value = openedGroups.value.filter((g) => g !== 'Agent 面板')
    }
  },
  { immediate: true },
)

const botOptions = computed(() =>
  bots.bots.map((b) => {
    const title = b.bot_id ? `Bot ${b.bot_id}` : `Bot #${b.index}`
    const subtitle = b.login_info?.nickname
      ? `${b.login_info.nickname}${b.bot_id ? '' : ' · 未连接'}`
      : STATUS_TEXT[b.status]
    return {
      title,
      subtitle,
      value: b.index,
      status: b.status,
      bot_id: b.bot_id,
    }
  }),
)

function botStatusOf(index: number | null): BotStatus {
  if (index === null) return 'disconnected'
  return bots.bots.find((b) => b.index === index)?.status ?? 'disconnected'
}

function botStatusColor(status: BotStatus): string {
  return status === 'connected' ? 'success' : status === 'connecting' || status === 'reconnecting' ? 'warning' : 'error'
}

function botStatusText(status: BotStatus): string {
  return STATUS_TEXT[status]
}

const currentStatusText = computed(() => (bots.currentBot ? STATUS_TEXT[bots.currentBot.status] : '离线'))
const isConnected = computed(() => bots.currentBot?.status === 'connected')
const hasCurrentBot = computed(() => bots.currentIndex !== null)

async function onBotSelect() {
  bots.selectBot(bots.currentIndex!)
  try {
    await refreshScopedData()
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
}

async function refreshScopedData() {
  // 账号切换后：模块数据随 bot_id 刷新（各页面按需再拉）
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
  } catch (err) {
    notify.push(errorMessage(err), 'error')
  }
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
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
            <div class="brand-sub">管理后台</div>
          </div>
        </div>
      </template>

      <v-list nav density="comfortable" class="pa-2 nav-list">
        <template v-for="item in navItems" :key="item.title">
          <v-list-group v-if="item.children" v-model="openedGroups" :value="item.title">
            <template #activator="{ props }">
              <v-list-item
                v-bind="props"
                :title="item.title"
                :prepend-icon="item.icon"
                rounded="lg"
                class="nav-item"
                :class="{ 'nav-item--active': isAgentActive }"
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
            :class="{ 'nav-item--active': item.to ? route.path === item.to || (item.to !== '/' && route.path.startsWith(item.to)) : false }"
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
          :model-value="bots.currentIndex"
          :items="botOptions"
          label="当前账号"
          density="compact"
          variant="outlined"
          hide-details
          class="bot-select"
          menu-icon="mdi-chevron-down"
          :menu-props="{ contentClass: 'bot-select-menu', offset: 10 }"
          @update:model-value="(v: number) => { bots.selectBot(v); onBotSelect() }"
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
          :color="isConnected ? 'success' : bots.currentBot?.status === 'connecting' || bots.currentBot?.status === 'reconnecting' ? 'warning' : 'error'"
          size="small"
          variant="tonal"
        >
          <v-icon start :icon="isConnected ? 'mdi-check-circle' : 'mdi-alert-circle'" size="small" />
          <span>{{ currentStatusText }}</span>
          <span v-if="bots.currentBot?.bot_id" class="chip-bot-id"> | {{ bots.currentBot.bot_id }}</span>
        </v-chip>
      </div>

      <v-spacer />

      <div class="topbar-actions">
        <div class="topbar-group" role="group" aria-label="连接控制">
          <v-btn
            color="success"
            variant="tonal"
            prepend-icon="mdi-plug"
            :disabled="isConnected || !hasCurrentBot || busyAction"
            title="连接当前账号"
            @click="act(() => bots.connect(bots.currentIndex!), '已发送连接请求')"
          >
            <span class="btn-label">连接</span>
          </v-btn>
          <v-btn
            color="error"
            variant="tonal"
            prepend-icon="mdi-unlink"
            :disabled="!isConnected || busyAction"
            title="断开当前账号"
            @click="act(() => bots.disconnect(bots.currentIndex!), '已断开')"
          >
            <span class="btn-label">断开</span>
          </v-btn>
          <v-btn
            color="warning"
            variant="tonal"
            prepend-icon="mdi-restart"
            :disabled="!hasCurrentBot || busyAction"
            title="重连当前账号"
            @click="act(() => bots.reconnect(bots.currentIndex!), '已发送重连请求')"
          >
            <span class="btn-label">重连</span>
          </v-btn>
        </div>

        <v-btn size="small" variant="tonal" icon="mdi-refresh" title="刷新状态" @click="act(() => bots.fetchBots(), '状态已刷新')" />
        <v-btn size="small" variant="tonal" prepend-icon="mdi-layers-triple-outline" title="多群管理" @click="multiGroupOpen = true">
          <span class="btn-label">多群管理</span>
        </v-btn>
        <v-btn
          size="small"
          variant="tonal"
          :icon="themeStore.isDark ? 'mdi-weather-night' : 'mdi-white-balance-sunny'"
          :title="themeStore.isDark ? '切换到浅色模式' : '切换到暗色模式'"
          @click="themeStore.toggle()"
        />
      </div>
    </v-app-bar>

    <v-main class="main-area">
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

@media (max-width: 720px) {
  .status-chip {
    display: none;
  }

  .bot-select {
    width: 140px;
    max-width: 36vw;
  }

  .topbar-account {
    gap: 6px;
  }

  .topbar-actions {
    gap: 4px;
  }
}
</style>
