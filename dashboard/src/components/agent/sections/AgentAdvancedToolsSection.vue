<script setup lang="ts">
/**
 * 「工具与能力」页的定制 UI：MCP 服务器 + 知识库条目。
 *
 * 这两块是真·列表管理（增删改查、测试连接、上传文档），不是单个配置项，
 * schema 表单表达不了，所以放在这里的卡片里；它们的开关/参数仍由 schema 表单渲染。
 */
import AgentMcpServers from '@/components/agent/sections/AgentMcpServers.vue'
import AgentKnowledgeItems from '@/components/agent/sections/AgentKnowledgeItems.vue'
</script>

<template>
  <div>
    <AgentMcpServers />
    <AgentKnowledgeItems />
  </div>
</template>
