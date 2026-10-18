<script setup lang="ts">
/**
 * 「权限管理」页的定制 UI：档位说明 + 群聊/私信黑白名单编辑器。
 *
 * 权限有两层，用户经常分不清，这里按「先看谁、再看在哪」排：
 * 1. 身份档位（permission）：这个人的角色够不够（拥有者 > 管理者 > 群主 > 群管理 > 成员）；
 * 2. 名单范围（PermissionEditor 的 group/user 黑白名单）：在这个群/这个人身上是否放开。
 * 名册字段（agent_owner_list / agent_manager_list）由 schema 表单渲染，紧跟在本卡片后。
 */
import PermissionEditor from '@/components/config/PermissionEditor.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'

const agent = useAgentConfigStore()
</script>

<template>
  <div>
    <v-card variant="outlined" class="mb-4">
      <v-card-title class="d-flex align-center">
        <v-icon icon="mdi-shield-account-outline" class="mr-2" color="success" /> 名单范围控制
      </v-card-title>
      <v-card-text>
        <div class="text-body-2 mb-3" style="opacity: 0.8">
          身份档位决定「谁能用」，这里决定「在哪些群 / 对哪些人放开」。两层都通过才响应。
        </div>
        <PermissionEditor :model-value="agent.permission" @update:model-value="agent.onPermissionChange" />
      </v-card-text>
    </v-card>
  </div>
</template>
