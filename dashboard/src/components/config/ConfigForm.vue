<script setup lang="ts">
import { computed, reactive } from 'vue'
import FieldControl from './FieldControl.vue'
import StringListWidget from './StringListWidget.vue'
import ListWidget from './ListWidget.vue'
import DynamicWidget from './DynamicWidget.vue'
import RepeaterWidget from './RepeaterWidget.vue'

const props = defineProps<{
  moduleName: string
  schema: Record<string, any>
  config: Record<string, any>
  botId: number | null
}>()

const emit = defineEmits<{
  (e: 'change', key: string, value: any): void
}>()

interface GroupDef {
  id: string
  label: string
  collapsible: boolean
}

interface ItemDef {
  key: string
  schema: Record<string, any>
  group?: string
}

// 统一为 {groups: [{id,label,collapsible}], items: [{key,schema,group}]}
const parsed = computed(() => {
  const raw = props.schema || {}
  let groupsRaw: Record<string, any> = {}
  let itemsRaw: Record<string, any> = {}

  if (raw.items && typeof raw.items === 'object') {
    // 新式 {groups, items}
    groupsRaw = raw.groups || {}
    itemsRaw = raw.items || {}
  } else if (raw && typeof raw === 'object') {
    // 旧版扁平：type==='group' 的键为分组，其余为字段
    Object.entries(raw).forEach(([key, def]: [string, any]) => {
      if (def && typeof def === 'object' && def.type === 'group') groupsRaw[key] = def
      else itemsRaw[key] = def
    })
  }

  const groups: GroupDef[] = Object.entries(groupsRaw).map(([id, g]) => ({
    id,
    label: g?.label || id,
    collapsible: g?.collapsible !== false,
  }))

  const items: ItemDef[] = Object.entries(itemsRaw)
    .filter(([, def]) => def && typeof def === 'object' && def.type !== 'group')
    .map(([key, def]) => ({
      key,
      schema: def as Record<string, any>,
      group: def.group,
    }))

  return { groups, items, hasItems: items.length > 0 }
})

const groupDefs = computed(() => {
  const { groups, items } = parsed.value
  const defs = groups.map((g) => ({ ...g, items: items.filter((it) => it.group === g.id) }))
  const ungrouped = items.filter((it) => !it.group)
  if (ungrouped.length) {
    defs.push({ id: '__default__', label: '默认配置', collapsible: true, items: ungrouped })
  }
  return defs
})

// 折叠状态（按分组 id）
const collapsed = reactive<Record<string, boolean>>({})

function isVisible(item: ItemDef): boolean {
  const cond = item.schema.showIf
  if (!cond) return true
  return props.config[cond.key] === cond.value
}

function currentValue(item: ItemDef): any {
  const v = props.config[item.key]
  return v !== undefined ? v : item.schema.default
}
</script>

<template>
  <div class="config-form">
    <!-- 完全退化形态：schema 不可解析时按值类型渲染 -->
    <template v-if="!parsed.hasItems">
      <div class="flat-fields">
        <FieldControl
          v-for="(value, key) in config"
          :key="String(key)"
          :field-key="String(key)"
          :schema="{ type: typeof value === 'boolean' ? 'boolean' : typeof value === 'number' ? 'number' : 'text', label: String(key) }"
          :value="value"
          @update="(v: any) => emit('change', String(key), v)"
        />
      </div>
    </template>

    <template v-else>
      <v-expansion-panels
        v-for="g in groupDefs"
        :key="g.id"
        variant="accordion"
        class="mb-3"
        :model-value="collapsed[g.id] ? [] : [0]"
        @update:model-value="(v: unknown) => { collapsed[g.id] = !((v as number[]) || []).includes(0) }"
      >
        <v-expansion-panel>
          <v-expansion-panel-title class="group-title">
            {{ g.label }}
          </v-expansion-panel-title>
          <v-expansion-panel-text>
            <div class="group-body">
              <template v-for="item in g.items" :key="item.key">
                <div v-if="isVisible(item)" class="config-item">
                  <FieldControl
                    v-if="!['string_list', 'list', 'dynamic', 'repeater'].includes(String(item.schema.type).toLowerCase())"
                    :field-key="item.key"
                    :schema="item.schema"
                    :value="currentValue(item)"
                    @update="(v: any) => emit('change', item.key, v)"
                  />
                  <StringListWidget
                    v-else-if="String(item.schema.type).toLowerCase() === 'string_list'"
                    :model-value="(currentValue(item) as string[]) || []"
                    @update:model-value="(v: string[]) => emit('change', item.key, v)"
                  />
                  <ListWidget
                    v-else-if="String(item.schema.type).toLowerCase() === 'list'"
                    :module-name="moduleName"
                    :schema="item.schema"
                    :model-value="(currentValue(item) as Record<string, { enabled: boolean; index: number }>) || {}"
                    :mode-value="String(config[item.key + '_mode'] || 'all')"
                    :bot-id="botId"
                    @update:model-value="(v: any) => emit('change', item.key, v)"
                    @update:mode-value="(v: string) => emit('change', item.key + '_mode', v)"
                  />
                  <DynamicWidget
                    v-else-if="String(item.schema.type).toLowerCase() === 'dynamic'"
                    :module-name="moduleName"
                    :schema="item.schema"
                    :model-value="(currentValue(item) as Record<string, Record<string, any>>) || {}"
                    :selected-value="String(config[item.key + '_selected'] || '')"
                    :bot-id="botId"
                    @update:model-value="(v: any) => emit('change', item.key, v)"
                    @update:selected-value="(v: string) => emit('change', item.key + '_selected', v)"
                  />
                  <RepeaterWidget
                    v-else
                    :schema="item.schema"
                    :model-value="(currentValue(item) as Record<string, any>[]) || []"
                    @update:model-value="(v: Record<string, any>[]) => emit('change', item.key, v)"
                  />
                </div>
              </template>
            </div>
          </v-expansion-panel-text>
        </v-expansion-panel>
      </v-expansion-panels>
    </template>
  </div>
</template>

<style scoped>
.config-form {
  display: flex;
  flex-direction: column;
}

.group-title {
  font-size: 14px;
  font-weight: 600;
}

.group-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 4px 0;
}

.config-item {
  padding: 12px 14px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.07);
  border-left: 3px solid rgba(var(--v-theme-primary), 0.35);
  border-radius: 8px;
  background: rgba(var(--v-theme-on-surface), 0.015);
}

.flat-fields {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
</style>
