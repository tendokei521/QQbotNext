<script setup lang="ts">
/**
 * 「协议端」说明：首启向导第 1 步与账号卡片共用。
 *
 * 新手最常见的卡点是不知道「协议端 / WebSocket 客户端」指什么，这里用两行说清：
 * 协议端是另外安装的 QQ 客户端插件，要在它里面建一个连接指向本程序。
 */
withDefaults(defineProps<{ mode?: 'reverse' | 'forward' }>(), { mode: 'reverse' })
</script>

<template>
  <div class="protocol-help">
    <div class="protocol-help__title">协议端＝另外安装的 QQ 客户端插件（NapCat / LLOneBot）</div>
    <ul class="protocol-help__list">
      <li v-if="mode === 'reverse'">在协议端里新建「WebSocket 客户端」，地址填本机监听地址</li>
      <li v-else>在协议端里新建「WebSocket 服务端」，把它的 ws 地址填到这里</li>
      <li>保存时记得启用；127.0.0.1 只本机可用，其它机器填本机 IP</li>
    </ul>
  </div>
</template>

<style scoped>
.protocol-help {
  padding: 8px 10px;
  border-radius: 10px;
  background: rgba(var(--v-theme-primary), 0.06);
  font-size: 12.5px;
  line-height: 1.6;
  color: rgba(var(--v-theme-on-surface), 0.72);
}

.protocol-help__title {
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.82);
}

.protocol-help__list {
  margin: 2px 0 0;
  padding-left: 18px;
}
</style>
