<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElSelect, ElOption, ElOptionGroup, ElInput, ElSwitch, ElButton, ElTooltip, ElDivider, ElTag, ElSlider, ElInputNumber, ElPopover } from 'element-plus'
import { Check, Loading, Setting, Connection, Warning, Close } from '@element-plus/icons-vue'
import { useModelStore } from '../../stores/model'
import { ElMessage } from 'element-plus'

const props = defineProps({
  disabled: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['change'])

const modelStore = useModelStore()
const searchQuery = ref('')
const settingsVisible = ref(false)

const localTemperature = computed({
  get: () => modelStore.temperature,
  set: (val) => modelStore.setTemperature(val)
})

const localMaxTokens = computed({
  get: () => modelStore.maxTokens,
  set: (val) => modelStore.setMaxTokens(val)
})

const selectValue = computed(() => {
  if (!modelStore.currentProviderId) return ''
  return `${modelStore.currentProviderId}::${modelStore.currentModelName || ''}`
})

const handleSelect = (val) => {
  const [providerId, modelName] = val.split('::')
  modelStore.selectProvider(providerId, modelName || null)
  emit('change', {
    providerId: modelStore.currentProviderId,
    modelName: modelStore.currentModelName,
    specialParams: modelStore.specialParams,
  })
  ElMessage.success(`已切换到 ${modelStore.currentModelName || providerId}`)
}

const groupedOptions = computed(() => {
  const groups = {}
  for (const provider of modelStore.providers) {
    const label = provider.label
    if (!groups[label]) {
      groups[label] = {
        label,
        icon: provider.icon,
        available: provider.available,
        options: [],
      }
    }
    for (const model of provider.models) {
      groups[label].options.push({
        value: `${provider.id}::${model}`,
        label: model,
        providerId: provider.id,
        modelName: model,
        isDefault: model === provider.default_model,
      })
    }
  }
  return Object.values(groups)
})

const filteredGroups = computed(() => {
  if (!searchQuery.value.trim()) return groupedOptions.value
  const q = searchQuery.value.toLowerCase()
  return groupedOptions.value
    .map(group => ({
      ...group,
      options: group.options.filter(
        opt => opt.label.toLowerCase().includes(q) || group.label.toLowerCase().includes(q)
      ),
    }))
    .filter(group => group.options.length > 0)
})

const handleTest = async () => {
  await modelStore.testConnection()
}

const handleThinkingToggle = (val) => {
  const paramCfg = modelStore.currentProviderSpecialParams?.thinking
  if (!paramCfg) return
  modelStore.setSpecialParam('thinking', val ? paramCfg.enabled_value : paramCfg.disabled_value)
  if (val) {
    ElMessage.info('思考模式已启用，Temperature 参数将被忽略')
  } else {
    ElMessage.info('思考模式已关闭')
  }
}

const handleReasoningEffortChange = (val) => {
  modelStore.setSpecialParam('reasoning_effort', val)
}

const thinkingEnabled = computed(() => {
  const paramCfg = modelStore.currentProviderSpecialParams?.thinking
  if (!paramCfg) return false
  const current = modelStore.specialParams?.thinking
  return current?.type === 'enabled'
})

const reasoningEffort = computed(() => {
  return modelStore.specialParams?.reasoning_effort ||
    modelStore.currentProviderSpecialParams?.reasoning_effort?.default ||
    'high'
})

const hasSpecialParams = computed(() => {
  return Object.keys(modelStore.currentProviderSpecialParams).length > 0
})

onMounted(() => {
  modelStore.loadProviders()
})
</script>

<template>
  <div class="model-selector">
    <div class="selector-main">
      <el-select
        :model-value="selectValue"
        :disabled="disabled || modelStore.isLoading"
        placeholder="选择模型"
        class="model-select"
        :suffix-icon="modelStore.isLoading ? Loading : undefined"
        filterable
        :filter-method="(val) => { searchQuery = val }"
        popper-class="model-selector-popper"
        @update:model-value="handleSelect"
      >
        <template #header>
          <div class="selector-header">
            <el-input
              v-model="searchQuery"
              placeholder="搜索模型..."
              size="small"
              clearable
              @click.stop
            />
          </div>
        </template>
        <el-option-group
          v-for="group in filteredGroups"
          :key="group.label"
          :label="group.label"
        >
          <template #label>
            <span class="group-label">
              <span class="group-icon">{{ group.icon }}</span>
              <span>{{ group.label }}</span>
              <el-tag v-if="!group.available" type="danger" size="small" class="unavailable-tag">未配置</el-tag>
            </span>
          </template>
          <el-option
            v-for="opt in group.options"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
            :disabled="!group.available"
          >
            <div class="model-option-content">
              <span class="model-name">{{ opt.label }}</span>
              <el-tag v-if="opt.isDefault" type="success" size="small">默认</el-tag>
              <el-icon v-if="opt.value === selectValue" class="check-icon" color="#409EFF">
                <Check />
              </el-icon>
            </div>
          </el-option>
        </el-option-group>
      </el-select>

      <el-popover
        v-model:visible="settingsVisible"
        placement="bottom-end"
        :width="340"
        trigger="click"
        :show-arrow="true"
        popper-class="model-settings-popover"
      >
        <template #reference>
          <el-button
            :class="['settings-trigger', { active: settingsVisible }]"
            :disabled="!modelStore.currentProviderId"
            size="small"
            title="模型参数配置"
          >
            <el-icon :size="14"><Setting /></el-icon>
          </el-button>
        </template>

        <div class="settings-panel">
          <div class="settings-header">
            <span class="settings-title">参数配置</span>
            <span class="settings-model-name">{{ modelStore.currentModelName }}</span>
          </div>

          <div class="settings-section">
            <div class="section-title">通用参数</div>

            <div class="param-row">
              <span class="param-label">
                Temperature
                <el-tooltip content="控制生成随机性，0 更确定，1 更随机。思考模式开启时此参数无效" placement="top">
                  <el-icon class="param-help"><Warning /></el-icon>
                </el-tooltip>
              </span>
              <div class="param-control">
                <el-slider
                  v-model="localTemperature"
                  :min="0"
                  :max="1"
                  :step="0.05"
                  :disabled="thinkingEnabled"
                  :show-tooltip="false"
                  style="flex: 1; min-width: 80px"
                />
                <el-input-number
                  v-model="localTemperature"
                  :min="0"
                  :max="1"
                  :step="0.05"
                  :precision="2"
                  :disabled="thinkingEnabled"
                  size="small"
                  style="width: 76px"
                  controls-position="right"
                />
              </div>
            </div>

            <div class="param-row">
              <span class="param-label">
                Max Tokens
                <el-tooltip content="模型生成的最大 token 数量" placement="top">
                  <el-icon class="param-help"><Warning /></el-icon>
                </el-tooltip>
              </span>
              <div class="param-control">
                <el-input-number
                  v-model="localMaxTokens"
                  :min="64"
                  :max="8192"
                  :step="256"
                  size="small"
                  style="width: 120px"
                  controls-position="right"
                />
              </div>
            </div>
          </div>

          <template v-if="hasSpecialParams">
            <div class="settings-section">
              <div class="section-title">{{ modelStore.currentProvider?.label }} 专属</div>

              <template v-if="modelStore.currentProviderSpecialParams?.thinking">
                <div class="param-row">
                  <span class="param-label">
                    {{ modelStore.currentProviderSpecialParams.thinking.label }}
                    <el-tooltip :content="modelStore.currentProviderSpecialParams.thinking.description" placement="top">
                      <el-icon class="param-help"><Warning /></el-icon>
                    </el-tooltip>
                  </span>
                  <el-switch
                    :model-value="thinkingEnabled"
                    @change="handleThinkingToggle"
                  />
                </div>
              </template>

              <template v-if="modelStore.currentProviderSpecialParams?.reasoning_effort && thinkingEnabled">
                <div class="param-row">
                  <span class="param-label">
                    {{ modelStore.currentProviderSpecialParams.reasoning_effort.label }}
                    <el-tooltip :content="modelStore.currentProviderSpecialParams.reasoning_effort.description" placement="top">
                      <el-icon class="param-help"><Warning /></el-icon>
                    </el-tooltip>
                  </span>
                  <el-select
                    :model-value="reasoningEffort"
                    size="small"
                    style="width: 100px"
                    @update:model-value="handleReasoningEffortChange"
                  >
                    <el-option
                      v-for="opt in modelStore.currentProviderSpecialParams.reasoning_effort.options"
                      :key="opt"
                      :label="opt"
                      :value="opt"
                    />
                  </el-select>
                </div>
              </template>
            </div>
          </template>

          <div class="settings-footer">
            <el-button
              size="small"
              :loading="modelStore.isTesting"
              @click="handleTest"
            >
              <el-icon style="margin-right: 4px"><Connection /></el-icon>
              测试连接
            </el-button>
            <div v-if="modelStore.testResult" class="test-result-inline" :class="modelStore.testResult.success ? 'success' : 'error'">
              <el-icon v-if="modelStore.testResult.success"><Check /></el-icon>
              <el-icon v-else><Warning /></el-icon>
              <span>{{ modelStore.testResult.message }}</span>
            </div>
          </div>
        </div>
      </el-popover>
    </div>
  </div>
</template>

<style scoped>
.model-selector {
  display: flex;
  flex-direction: column;
}

.selector-main {
  display: flex;
  align-items: center;
  gap: 4px;
}

.model-select {
  width: 200px;
}

.settings-trigger {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--el-border-color) !important;
  border-radius: 6px !important;
  background: transparent !important;
  color: var(--el-text-color-secondary) !important;
  cursor: pointer;
  transition: all 0.2s;
  padding: 0 !important;
  --el-button-size: 28px;
}

.settings-trigger:hover:not(:disabled):not(.is-disabled) {
  background: var(--el-fill-color-light) !important;
  color: var(--el-color-primary) !important;
  border-color: var(--el-color-primary-light-5) !important;
}

.settings-trigger.active {
  background: var(--el-color-primary-light-9) !important;
  color: var(--el-color-primary) !important;
  border-color: var(--el-color-primary-light-5) !important;
}

.settings-trigger:disabled,
.settings-trigger.is-disabled {
  opacity: 0.5;
  cursor: not-allowed;
  background: transparent !important;
}

.settings-panel {
  padding: 4px 0;
}

.settings-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 4px 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  margin-bottom: 12px;
}

.settings-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.settings-model-name {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
  padding: 2px 8px;
  border-radius: 4px;
}

.settings-section {
  margin-bottom: 12px;
}

.settings-section:last-of-type {
  margin-bottom: 8px;
}

.section-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 8px;
  padding: 0 4px;
}

.param-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 4px;
  border-radius: 4px;
  transition: background 0.15s;
}

.param-row:hover {
  background: var(--el-fill-color-lighter);
}

.param-label {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  white-space: nowrap;
}

.param-control {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  justify-content: flex-end;
  margin-left: 12px;
}

.param-help {
  color: var(--el-text-color-placeholder);
  cursor: help;
  font-size: 12px;
}

.settings-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.test-result-inline {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
}

.test-result-inline.success {
  color: var(--el-color-success);
}

.test-result-inline.error {
  color: var(--el-color-danger);
}

.selector-header {
  padding: 4px 0;
}

.group-label {
  display: flex;
  align-items: center;
  gap: 6px;
}

.group-icon {
  font-size: 16px;
}

.unavailable-tag {
  margin-left: 4px;
}

.model-option-content {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 0;
  width: 100%;
}

.model-name {
  font-weight: 500;
  font-size: 13px;
  flex: 1;
}

.check-icon {
  margin-left: auto;
}

@media (max-width: 768px) {
  .model-select {
    width: 160px;
  }
}
</style>

<style>
.model-selector-popper .el-select-dropdown__header {
  padding: 8px 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.model-selector-popper .el-select-dropdown__item {
  padding: 8px 12px;
  height: auto;
}

.model-selector-popper .el-select-dropdown__item.hover,
.model-selector-popper .el-select-dropdown__item:hover {
  background-color: var(--el-fill-color-light);
}

.model-selector-popper .el-select-dropdown__item.selected {
  color: var(--el-color-primary);
}

.model-settings-popover {
  padding: 16px !important;
}
</style>
