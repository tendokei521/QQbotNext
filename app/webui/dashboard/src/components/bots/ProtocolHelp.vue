<script setup lang="ts">
/**
 * 「协议端」白话说明：首启向导第 1 步与账号卡共用同一份文案，避免两处说法漂移。
 *
 * 新手最常见的卡点是不知道「协议端 / WebSocket 客户端」指什么——这里用两三句话
 * 说清：协议端是另外安装的 QQ 客户端插件，要在它里面建一个连接指向本程序。
 */
withDefaults(defineProps<{ mode?: 'reverse' | 'forward' }>(), { mode: 'reverse' })
</script>

<template>
  <div class="protocol-help">
    <div class="protocol-help__title">协议端＝另外安装的 QQ 客户端插件（NapCat / LLOneBot）。</div>
    <template v-if="mode === 'reverse'">
      <div>在它里面新建「WebSocket 客户端」，URL 填界面上给出的本机监听地址。</div>
      <div>保存时记得启用；127.0.0.1 只本机可用，其它机器要填本机 IP。</div>
    </template>
    <template v-else>
      <div>在它里面新建「WebSocket 服务端」，把它给出的 ws 地址填进来，端口要一致。</div>
    </template>
  </div>
</template>

<style scoped>
.protocol-help {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(var(--v-theme-primary), 0.06);
  font-size: 12.5px;
  line-height: 1.5;
  color: rgba(var(--v-theme-on-surface), 0.75);
}

.protocol-help__title {
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.82);
}
</style>
