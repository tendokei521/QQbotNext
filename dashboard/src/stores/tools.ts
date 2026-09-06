import { defineStore } from 'pinia'

const STORAGE_KEY = 'qqbot_tool_page_state'

export type ToolTab = 'system' | 'napcat' | 'mcp' | 'module'

interface ToolsPageState {
  activeTab: ToolTab
  systemCollapsed: Record<string, boolean>
  napcatCollapsed: Record<string, boolean>
  mcpCollapsed: Record<string, boolean>
  moduleCollapsed: Record<string, boolean>
  scrollTop: Record<string, number>
}

function readState(): ToolsPageState {
  const fallback: ToolsPageState = {
    activeTab: 'system',
    systemCollapsed: {},
    napcatCollapsed: {},
    mcpCollapsed: {},
    moduleCollapsed: {},
    scrollTop: {},
  }
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return fallback
    const parsed = JSON.parse(raw)
    return { ...fallback, ...parsed }
  } catch {
    return fallback
  }
}

export const useToolsStore = defineStore('tools', {
  state: (): ToolsPageState => readState(),
  actions: {
    persist() {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        activeTab: this.activeTab,
        systemCollapsed: this.systemCollapsed,
        napcatCollapsed: this.napcatCollapsed,
        mcpCollapsed: this.mcpCollapsed,
        moduleCollapsed: this.moduleCollapsed,
        scrollTop: this.scrollTop,
      }))
    },
    setActiveTab(tab: ToolTab) {
      this.activeTab = tab
      this.persist()
    },
    setCollapsed(kind: ToolTab, collapsed: Record<string, boolean>) {
      if (kind === 'system') this.systemCollapsed = collapsed
      else if (kind === 'napcat') this.napcatCollapsed = collapsed
      else if (kind === 'mcp') this.mcpCollapsed = collapsed
      else if (kind === 'module') this.moduleCollapsed = collapsed
      this.persist()
    },
    collapsedOf(kind: ToolTab): Record<string, boolean> {
      if (kind === 'system') return this.systemCollapsed
      if (kind === 'napcat') return this.napcatCollapsed
      if (kind === 'mcp') return this.mcpCollapsed
      return this.moduleCollapsed
    },
    setScroll(tab: ToolTab, value: number) {
      this.scrollTop = { ...this.scrollTop, [tab]: value }
      this.persist()
    },
  },
})
