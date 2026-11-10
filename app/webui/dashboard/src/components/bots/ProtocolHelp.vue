<script setup lang="ts">
/**
 * 「协议端」说明：首启向导第 1 步、账号卡片与「添加账号」共用。
 *
 * 新手最常见的卡点是不知道「协议端 / WebSocket 客户端」指什么，这里用两行说清：
 * 协议端是另外安装的 QQ 客户端插件，要在它里面建一个连接指向本程序。
 * 官方 QQ 机器人不需要协议端，改成在开放平台建机器人、把它拉进群。
 */
withDefaults(
  defineProps<{
    mode?: 'reverse' | 'forward'
    /** 官方 QQ 机器人：说明改成开放平台的建号步骤 */
    official?: boolean
    /** 官方账号的接入方式，webhook 时补一句回调地址要求 */
    transport?: string
  }>(),
  { mode: 'reverse', official: false, transport: '' },
)
</script>

<template>
  <div class="protocol-help">
    <template v-if="official">
      <div class="protocol-help__title">官方 QQ 机器人＝在 QQ 开放平台建的机器人</div>
      <ul class="protocol-help__list">
        <li>去 q.qq.com 建一个机器人，拿到 AppID 与 AppSecret</li>
        <li>把它拉进一个测试群，群主在群里开「接收所有消息」权限</li>
        <li>默认只收 @ 的消息；没开全量权限时普通群消息收不到</li>
        <li>官方没有沙箱：只能用真实机器人和测试群联调</li>
        <li v-if="transport === 'webhook'">Webhook 还要在开放平台填公网能访问的回调地址</li>
      </ul>
    </template>
    <template v-else>
      <div class="protocol-help__title">协议端＝另外安装的 QQ 客户端插件（NapCat / LLOneBot）</div>
      <ul class="protocol-help__list">
        <li v-if="mode === 'reverse'">在协议端里新建「WebSocket 客户端」，地址填本机监听地址</li>
        <li v-else>在协议端里新建「WebSocket 服务端」，把它的 ws 地址填到这里</li>
        <li>保存时记得启用；127.0.0.1 只本机可用，其它机器填本机 IP</li>
      </ul>
    </template>
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
