import { defineStore } from 'pinia'

export interface AgentNavSection {
  to: string
  title: string
}

export const useAgentNavStore = defineStore('agentNav', {
  state: () => ({
    sections: [] as AgentNavSection[],
  }),
  actions: {
    setSections(sections: AgentNavSection[]) {
      this.sections = sections
    },
  },
})
