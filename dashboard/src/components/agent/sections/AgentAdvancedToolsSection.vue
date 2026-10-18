<script setup lang="ts">
/**
 * 「工具与能力」页的定制 UI：知识库条目管理。
 *
 * 只放**真·列表管理**（上传/删除文档）这一块。MCP 服务器曾经也在这里放了一张卡片，
 * 但配置表里本来就有 mcp_servers 这个 JSON 字段——同一个东西出现两遍，页尾那张空卡片
 * 还写着「请在下方 JSON 数组中添加」，而 JSON 数组明明在上方。删掉它。
 */
import AgentKnowledgeItems from '@/components/agent/sections/AgentKnowledgeItems.vue'
</script>

<template>
  <div>
    <AgentKnowledgeItems />
  </div>
</template>
