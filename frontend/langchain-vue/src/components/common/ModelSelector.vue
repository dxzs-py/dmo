<script setup>
import { ref, computed } from 'vue'
import { ElSelect, ElOption, ElInput } from 'element-plus'
import { Loading, Search, Check } from '@element-plus/icons-vue'

const props = defineProps({
  modelValue: {
    type: String,
    default: 'deepseek-chat'
  },
  disabled: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:modelValue', 'change'])

const searchQuery = ref('')
const isDropdownOpen = ref(false)

const models = [
  {
    value: 'deepseek-chat',
    label: 'DeepSeek Chat',
    provider: 'DeepSeek',
    providers: ['deepseek'],
    icon: '🤖'
  },
  {
    value: 'deepseek-coder',
    label: 'DeepSeek Coder',
    provider: 'DeepSeek',
    providers: ['deepseek'],
    icon: '🤖'
  },
  {
    value: 'gpt-4o',
    label: 'GPT-4o',
    provider: 'OpenAI',
    providers: ['openai', 'azure'],
    icon: '🔵'
  },
  {
    value: 'gpt-4o-mini',
    label: 'GPT-4o Mini',
    provider: 'OpenAI',
    providers: ['openai', 'azure'],
    icon: '🔵'
  },
  {
    value: 'gpt-4-turbo',
    label: 'GPT-4 Turbo',
    provider: 'OpenAI',
    providers: ['openai', 'azure'],
    icon: '🔵'
  },
  {
    value: 'claude-opus-4',
    label: 'Claude 4 Opus',
    provider: 'Anthropic',
    providers: ['anthropic', 'azure', 'google', 'amazon-bedrock'],
    icon: '🟣'
  },
  {
    value: 'claude-sonnet-4',
    label: 'Claude 4 Sonnet',
    provider: 'Anthropic',
    providers: ['anthropic', 'azure', 'google', 'amazon-bedrock'],
    icon: '🟣'
  },
  {
    value: 'claude-3-5-sonnet',
    label: 'Claude 3.5 Sonnet',
    provider: 'Anthropic',
    providers: ['anthropic', 'azure'],
    icon: '🟣'
  },
  {
    value: 'gemini-2.0-flash',
    label: 'Gemini 2.0 Flash',
    provider: 'Google',
    providers: ['google'],
    icon: '🟢'
  },
  {
    value: 'gemini-1.5-pro',
    label: 'Gemini 1.5 Pro',
    provider: 'Google',
    providers: ['google'],
    icon: '🟢'
  },
  {
    value: 'qwen-plus',
    label: 'Qwen Plus',
    provider: 'Alibaba',
    providers: ['alibaba'],
    icon: '🟠'
  },
  {
    value: 'qwen-turbo',
    label: 'Qwen Turbo',
    provider: 'Alibaba',
    providers: ['alibaba'],
    icon: '🟠'
  },
  {
    value: 'qwen-max',
    label: 'Qwen Max',
    provider: 'Alibaba',
    providers: ['alibaba'],
    icon: '🟠'
  },
  {
    value: 'yi-large',
    label: 'Yi Large',
    provider: 'ZeroOne',
    providers: ['zeroone'],
    icon: '🟡'
  },
  {
    value: 'glm-4',
    label: 'GLM-4',
    provider: 'Zhipu',
    providers: ['zhipu'],
    icon: '🟢'
  },
  {
    value: 'moonshot-v1-8k',
    label: 'Moonshot V1 8K',
    provider: 'Moonshot',
    providers: ['moonshot'],
    icon: '🌙'
  },
  {
    value: 'moonshot-v1-32k',
    label: 'Moonshot V1 32K',
    provider: 'Moonshot',
    providers: ['moonshot'],
    icon: '🌙'
  },
]

const providerColors = {
  'deepseek': '#3B82F6',
  'openai': '#10A37F',
  'anthropic': '#CC785C',
  'google': '#4285F4',
  'azure': '#0078D4',
  'alibaba': '#FF6A00',
  'amazon-bedrock': '#FF9900',
  'zeroone': '#FFD700',
  'zhipu': '#00A86B',
  'moonshot': '#6B5B95',
}

const filteredModels = computed(() => {
  if (!searchQuery.value.trim()) {
    return models
  }
  const query = searchQuery.value.toLowerCase()
  return models.filter(m =>
    m.label.toLowerCase().includes(query) ||
    m.provider.toLowerCase().includes(query)
  )
})

const groupedModels = computed(() => {
  const groups = {}
  for (const model of filteredModels.value) {
    if (!groups[model.provider]) {
      groups[model.provider] = []
    }
    groups[model.provider].push(model)
  }
  return groups
})

const providerOrder = ['OpenAI', 'Anthropic', 'Google', 'DeepSeek', 'Alibaba', 'ZeroOne', 'Zhipu', 'Moonshot']

const sortedGroups = computed(() => {
  const result = []
  for (const provider of providerOrder) {
    if (groupedModels.value[provider]) {
      result.push({
        label: provider,
        options: groupedModels.value[provider]
      })
    }
  }
  for (const provider of Object.keys(groupedModels.value)) {
    if (!providerOrder.includes(provider)) {
      result.push({
        label: provider,
        options: groupedModels.value[provider]
      })
    }
  }
  return result
})

const currentModel = computed({
  get() {
    return props.modelValue
  },
  set(value) {
    emit('update:modelValue', value)
    emit('change', value)
  }
})

const handleOpenChange = (open) => {
  isDropdownOpen.value = open
  if (!open) {
    searchQuery.value = ''
  }
}

const getProviderStyle = (provider) => {
  const color = providerColors[provider] || '#999'
  return {
    backgroundColor: color + '20',
    color: color,
    borderColor: color + '40',
  }
}
</script>

<template>
  <div class="model-selector">
    <el-select
      v-model="currentModel"
      :disabled="disabled"
      placeholder="选择模型"
      class="model-select"
      :suffix-icon="disabled ? Loading : undefined"
      filterable
      :filter-method="(val) => { searchQuery = val }"
      popper-class="model-selector-popper"
      @visible-change="handleOpenChange"
    >
      <template #header>
        <div class="selector-header">
          <el-input
            v-model="searchQuery"
            placeholder="搜索模型..."
            size="small"
            :prefix-icon="Search"
            clearable
            @click.stop
          />
        </div>
      </template>
      <el-option-group
        v-for="group in sortedGroups"
        :key="group.label"
        :label="group.label"
      >
        <el-option
          v-for="model in group.options"
          :key="model.value"
          :label="model.label"
          :value="model.value"
          class="model-option"
        >
          <div class="model-option-content">
            <span class="model-icon">{{ model.icon }}</span>
            <div class="model-info">
              <span class="model-name">{{ model.label }}</span>
              <div class="model-providers">
                <span
                  v-for="provider in model.providers"
                  :key="provider"
                  class="provider-badge"
                  :style="getProviderStyle(provider)"
                >
                  {{ provider }}
                </span>
              </div>
            </div>
            <el-icon v-if="model.value === currentModel" class="check-icon" color="#409EFF">
              <Check />
            </el-icon>
          </div>
        </el-option>
      </el-option-group>
    </el-select>
  </div>
</template>

<style scoped>
.model-selector {
  min-width: 220px;
}

.model-select {
  width: 220px;
}

.selector-header {
  padding: 4px 0;
}

.model-option-content {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
  width: 100%;
}

.model-icon {
  font-size: 20px;
  width: 28px;
  text-align: center;
}

.model-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.model-name {
  font-weight: 500;
  font-size: 14px;
}

.model-providers {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.provider-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid;
  text-transform: capitalize;
}

.check-icon {
  margin-left: auto;
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
</style>
