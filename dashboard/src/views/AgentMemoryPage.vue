<script setup lang="ts">
import { computed } from 'vue'
import ConfigForm from '@/components/config/ConfigForm.vue'
import { useAgentConfigStore } from '@/stores/agentConfig'
import { filterSchemaByGroup } from '@/utils/schema'

const agent = useAgentConfigStore()
const schema = computed(() => filterSchemaByGroup(agent.schema, 'group_memory'))
</script>

<template>
  <v-card variant="outlined" class="mb-4">
    <v-card-title class="d-flex align-center">
      <v-icon icon="mdi-brain" class="mr-2" color="warning" /> 长期记忆（实验性）
    </v-card-title>
    <v-card-text>
      <ConfigForm
        :module-name="'agent'"
        :schema="schema"
        :config="agent.draft"
        :bot-id="agent.botId"
        @change="agent.onChange"
      />
    </v-card-text>
  </v-card>
</template>
