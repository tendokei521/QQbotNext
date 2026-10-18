<script setup lang="ts">
/**
 * 「基础配置」页的定制 UI：Agent 总开关 + Provider 模型池 + 人格预设。
 *
 * 三件事必须放在同一页：用户配 Agent 的动作顺序就是「开 → 给模型 → 给人格」。
 * 之前总开关在面板头部（每个配置页都能看到，看着像只影响本页），模型池在「模型」页、
 * 人格预设又在「角色」页，配一次要跳三页。
 *
 * 无 props、自读 store，这样 AgentSectionPage 能用统一的 ``<component :is>`` 挂载。
 */
import AgentModelPool from '@/components/agent/sections/AgentModelPool.vue'
import AgentRolePresets from '@/components/agent/sections/AgentRolePresets.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'

const agent = useAgentConfigStore()
</script>

<template>
  <div>
    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-power" class="mr-2" color="primary" /> Agent 总开关
        <v-spacer />
        <v-switch
          v-model="agent.enabled"
          color="success"
          density="compact"
          hide-details
          inset
          @update:model-value="(v: any) => agent.onEnabledChange(v)"
        />
      </v-card-title>
      <v-card-text class="text-body-2" style="opacity: 0.8">
        关闭后所有 Bot 都不再响应消息（定时任务与主动消息同样停止）。
        下面的群聊/私聊开关决定「哪些场景」回复，这里是「是否回复」的总闸。
      </v-card-text>
    </v-card>

    <AgentModelPool />
    <AgentRolePresets />
  </div>
</template>
