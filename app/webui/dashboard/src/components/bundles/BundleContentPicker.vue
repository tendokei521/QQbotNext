<script setup lang="ts">
/**
 * 配置包内容勾选面板：导出时选择要带哪些内容，导入/应用时选择要写哪些内容。
 *
 * 形状来自后端 `contents`（导出选项与导入预检报告同构），四个分类：
 * 人格 / 模型配置 / AI 设置 / 插件；插件可再逐个勾选。
 */
import { computed } from 'vue'
import {
  BUNDLE_SECTIONS,
  type BundleContentSelection,
  type BundleContents,
  type BundleSectionKey,
} from '@/stores/bundles'

const props = defineProps<{
  contents: BundleContents | null
  mode: 'export' | 'import'
  modelValue: BundleContentSelection
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: BundleContentSelection): void
}>()

const modules = computed(() => props.contents?.modules || [])
const bundledModules = computed(() => modules.value.filter((item) => item.bundled))
const selectedSections = computed(() => new Set(props.modelValue.sections))
const selectedModules = computed(() => new Set(props.modelValue.modules))

function available(key: BundleSectionKey): boolean {
  if (key === 'persona') return Boolean(props.contents?.persona?.available)
  if (key === 'provider') return Boolean(props.contents?.provider?.available)
  if (key === 'agent') return Boolean(props.contents?.agent?.available)
  return modules.value.length > 0
}

function personaDetail(): string {
  if (!props.contents?.persona?.available) {
    return props.mode === 'export' ? '当前账号还没有选人格' : '配置包里没有人格'
  }
  const name = props.contents.persona.name || '未命名'
  const fields = props.contents.persona.fields || []
  return fields.length ? `${name} · ${fields.join(' / ')}` : name
}

function providerDetail(): string {
  const provider = props.contents?.provider
  if (!provider?.available) {
    return props.mode === 'export' ? '当前账号还没有配模型' : '配置包里没有模型配置'
  }
  const parts = [provider.preset_name || provider.provider || '模型服务']
  if (provider.api_base) parts.push(provider.api_base)
  parts.push(`${provider.models?.length || 0} 个模型`)
  return parts.join(' · ')
}

function agentDetail(): string {
  if (!props.contents?.agent?.available) {
    return props.mode === 'export' ? '当前账号还没有改过 AI 设置' : '配置包里没有 AI 设置'
  }
  return `${props.contents.agent.key_count || 0} 项设置`
}

function modulesDetail(): string {
  if (!modules.value.length) {
    return props.mode === 'export' ? '当前账号没有可导出的插件' : '配置包里没有插件'
  }
  return `已选 ${selectedModules.value.size} / ${modules.value.length} 个`
}

function formatSize(bytes: number | undefined): string {
  if (!bytes) return ''
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function apply(sections: Iterable<string>, moduleNames: Iterable<string>) {
  emit('update:modelValue', {
    sections: [...new Set(sections)],
    modules: [...new Set(moduleNames)],
  })
}

function toggleSection(key: BundleSectionKey, value: boolean) {
  const sections = new Set(props.modelValue.sections)
  if (value) sections.add(key)
  else sections.delete(key)
  // 重新勾上插件时，默认把全部插件带上（上次取消全选后也能一键恢复）
  const moduleNames = value && key === 'modules' && !props.modelValue.modules.length
    ? modules.value.map((item) => item.name)
    : props.modelValue.modules
  if (!value && key === 'modules') {
    apply(sections, [])
    return
  }
  apply(sections, moduleNames)
}

function toggleModule(name: string, value: boolean) {
  const moduleNames = new Set(props.modelValue.modules)
  if (value) moduleNames.add(name)
  else moduleNames.delete(name)
  const sections = new Set(props.modelValue.sections)
  if (moduleNames.size) sections.add('modules')
  else sections.delete('modules')
  apply(sections, moduleNames)
}

function selectAllModules(value: boolean) {
  const sections = new Set(props.modelValue.sections)
  if (value) sections.add('modules')
  else sections.delete('modules')
  apply(sections, value ? modules.value.map((item) => item.name) : [])
}

function selectAll(value: boolean) {
  if (!value) {
    apply([], [])
    return
  }
  const sections = BUNDLE_SECTIONS.filter((item) => available(item.key)).map((item) => item.key)
  apply(sections, modules.value.map((item) => item.name))
}
</script>

<template>
  <v-card variant="outlined" class="content-picker">
    <v-card-text class="py-2">
      <div class="d-flex align-center flex-wrap gap-2 mb-1">
        <div class="text-body-2 font-weight-medium">
          {{ mode === 'export' ? '选择要导出的内容' : '选择要应用的内容' }}
        </div>
        <v-spacer />
        <v-btn size="x-small" variant="text" @click="selectAll(true)">全选</v-btn>
        <v-btn size="x-small" variant="text" @click="selectAll(false)">全不选</v-btn>
      </div>
      <div class="text-caption app-muted mb-2">
        没勾的内容不会写进配置包；{{ mode === 'export' ? '也不占用包体积' : '目标账号保持原样' }}。
      </div>

      <div v-for="section in BUNDLE_SECTIONS" :key="section.key" class="picker-row">
        <v-checkbox
          :model-value="selectedSections.has(section.key)"
          :disabled="!available(section.key)"
          density="compact"
          hide-details
          class="picker-check"
          @update:model-value="(value: boolean | null) => toggleSection(section.key, Boolean(value))"
        />
        <div class="picker-body">
          <div class="d-flex align-center flex-wrap gap-2">
            <span class="text-body-2" :class="{ 'app-muted': !available(section.key) }">
              {{ section.label }}
            </span>
            <span class="text-caption app-muted">
              {{ section.key === 'persona'
                ? personaDetail()
                : section.key === 'provider'
                  ? providerDetail()
                  : section.key === 'agent'
                    ? agentDetail()
                    : modulesDetail() }}
            </span>
          </div>

          <div v-if="section.key === 'modules' && selectedSections.has('modules')" class="mt-1">
            <div class="d-flex align-center flex-wrap gap-2 mb-1">
              <span class="text-caption app-muted">
                {{ mode === 'export'
                  ? '第三方插件会连代码一起打包'
                  : bundledModules.length
                    ? '第三方插件会随包安装到本机'
                    : '只写入插件参数与启停' }}
              </span>
              <v-spacer />
              <v-btn size="x-small" variant="text" @click="selectAllModules(true)">全选</v-btn>
              <v-btn size="x-small" variant="text" @click="selectAllModules(false)">全不选</v-btn>
            </div>
            <div
              v-for="mod in modules"
              :key="mod.name"
              class="d-flex align-center flex-wrap gap-2 py-1"
            >
              <v-checkbox
                :model-value="selectedModules.has(mod.name)"
                density="compact"
                hide-details
                class="picker-check"
                @update:model-value="(value: boolean | null) => toggleModule(mod.name, Boolean(value))"
              />
              <span class="text-caption font-weight-medium">{{ mod.display_name || mod.name }}</span>
              <v-chip size="x-small" variant="text">v{{ mod.version || '—' }}</v-chip>
              <v-chip size="x-small" variant="text">{{ mod.builtin ? '内置' : '第三方' }}</v-chip>
              <span v-if="!mod.builtin && mod.size" class="text-caption app-muted">
                {{ formatSize(mod.size) }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </v-card-text>
  </v-card>
</template>

<style scoped>
.content-picker {
  background: rgba(var(--v-theme-on-surface), 0.02);
}

.picker-row {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  padding: 2px 0;
}

.picker-check {
  flex: 0 0 auto;
  margin-top: -6px;
}

.picker-body {
  flex: 1 1 auto;
  min-width: 0;
  padding: 6px 0 4px;
}
</style>
