<script setup lang="ts">
/**
 * 「权限管理」页的定制 UI：档位说明 + 跳转到「权限与回复」。
 *
 * 历史：这里曾内嵌一份「名单范围控制」（群/用户黑白名单）。R4 起名单与场景开关
 * 统一收敛到 `/policy`（同一张 channel_overrides 表），本页不再放第二份编辑器——
 * "同一语义两处配置"正是之前"改了不生效"的根源。
 *
 * 本页仍保留：响应权限档位（谁能用 Agent）、拥有者/管理者名册（schema 渲染）、
 * 高危工具审批。
 */
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div>
    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-shield-account-outline" class="mr-2" color="success" /> 群聊开关与回复规则
      </v-card-title>
      <v-card-text>
        <div class="text-body-2 mb-3" style="opacity: 0.8">
          群聊开关、触发方式与免打扰都在「权限与回复」里配置。
        </div>
        <div class="d-flex flex-wrap gap-2">
          <v-btn
            color="success"
            variant="tonal"
            prepend-icon="mdi-account-group-outline"
            @click="router.push('/policy')"
          >
            打开权限与回复
          </v-btn>
        </div>
      </v-card-text>
    </v-card>
  </div>
</template>
