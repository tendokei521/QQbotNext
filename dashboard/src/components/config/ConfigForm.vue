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

// 展开状态（按分组 id）：boolean 为唯一真相源。
// 注意：v-expansion-panels 在 accordion（非 multiple）模式下，@update:model-value
// emit 的是「单个索引」——展开为数字 0，收起为 undefined；只有 multiple 模式才是数组。
// 所以 handler 必须兼容数组与单值两种形态，否则 0 这个 falsy 值会被误判成「关闭」，
// 导致“收缩后再次点击打不开”。
const open = reactive<Record<string, boolean>>({})

function isPanelOpen(v: unknown): boolean {
  if (v === undefined || v === null) return false
  if (Array.isArray(v)) {
    const arr = v as number[]
    return arr.length > 0 && arr.includes(0)
  }
  // 单值形态（accordion / 非 multiple）：展开 = 序号 0
  return Number(v) === 0
}

function panelsValue(gid: string): number[] {
  // 传回 prop 的始终是数组形态（Vuetify 内部会 wrapInArray），缺省 = 展开
  return open[gid] === false ? [] : [0]
}

function panelsUpdate(gid: string, v: unknown): void {
  open[gid] = isPanelOpen(v)
}

function effectiveValue(key: string): any {
  if (props.config[key] !== undefined) return props.config[key]
  const def = parsed.value.items.find((it) => it.key === key)?.schema?.default
  return def
}

function isVisible(item: ItemDef): boolean {
  const cond = item.schema.showIf
  if (!cond) return true
  // 用“含 schema 默认值的有效值”判断，避免控制项仅存在于默认时依赖项被误隐藏
  return effectiveValue(cond.key) === cond.value
}

/** 复杂 widget 需要单独渲染 label/description，简单字段由 FieldControl 自带 */
function isComplexType(item: ItemDef): boolean {
  return ['string_list', 'list', 'dynamic', 'repeater'].includes(String(item.schema.type).toLowerCase())
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
        :model-value="panelsValue(g.id)"
        @update:model-value="(v: unknown) => panelsUpdate(g.id, v)"
      >
        <v-expansion-panel>
          <v-expansion-panel-title class="group-title">
            <v-icon size="small" class="mr-1" :icon="open[g.id] === false ? 'mdi-chevron-right' : 'mdi-chevron-down'" />
            {{ g.label }}
          </v-expansion-panel-title>
          <v-expansion-panel-text>
            <div class="group-body">
              <template v-for="item in g.items" :key="item.key">
                <div v-if="isVisible(item)" class="config-item">
                  <template v-if="isComplexType(item)">
                    <div v-if="item.schema.label" class="field-label">{{ item.schema.label || item.key }}</div>
                    <div v-if="item.schema.description" class="field-desc">{{ item.schema.description }}</div>
                  </template>
                  <FieldControl
                    v-if="!isComplexType(item)"
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

.field-label {
  font-size: 13.5px;
  font-weight: 500;
  margin-bottom: 4px;
}

.field-desc {
  font-size: 12px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  line-height: 1.5;
  white-space: pre-wrap;
  margin-bottom: 8px;
}

.flat-fields {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
</style>
