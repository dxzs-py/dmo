<script setup>
import { computed } from 'vue'
import { ElSelect, ElOption, ElTag } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'

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

const models = [
  { value: 'deepseek-chat', label: 'DeepSeek Chat', provider: 'DeepSeek', icon: '🤖' },
  { value: 'gpt-4o', label: 'GPT-4o', provider: 'OpenAI', icon: '🔵' },
  { value: 'gpt-4o-mini', label: 'GPT-4o Mini', provider: 'OpenAI', icon: '🔵' },
  { value: 'claude-3-5-sonnet', label: 'Claude 3.5 Sonnet', provider: 'Anthropic', icon: '🟣' },
  { value: 'qwen-plus', label: 'Qwen Plus', provider: 'Alibaba', icon: '🟠' },
  { value: 'qwen-turbo', label: 'Qwen Turbo', provider: 'Alibaba', icon: '🟠' },
  { value: 'yi-large', label: 'Yi Large', provider: 'ZeroOne', icon: '🟡' },
  { value: 'glm-4', label: 'GLM-4', provider: 'Zhipu', icon: '🟢' },
  { value: 'moonshot-v1-8k', label: 'Moonshot V1 8K', provider: 'Moonshot', icon: '🌙' },
]

const currentModel = computed({
  get() {
    return props.modelValue
  },
  set(value) {
    emit('update:modelValue', value)
    emit('change', value)
  }
})
</script>

<template>
  <div class="model-selector">
    <el-select
      v-model="currentModel"
      :disabled="disabled"
      placeholder="选择模型"
      class="model-select"
      :suffix-icon="disabled ? Loading : undefined"
    >
      <template #header>
        <div class="selector-header">
          <span>选择 AI 模型</span>
        </div>
      </template>
      <el-option-group
        v-for="group in [
          { label: '推荐', options: models.slice(0, 3) },
          { label: 'OpenAI', options: models.filter(m => m.provider === 'OpenAI') },
          { label: ' Anthropic', options: models.filter(m => m.provider === 'Anthropic') },
          { label: 'DeepSeek', options: models.filter(m => m.provider === 'DeepSeek') },
          { label: '阿里巴巴', options: models.filter(m => m.provider === 'Alibaba') },
          { label: '其他', options: models.filter(m => !['OpenAI', 'Anthropic', 'DeepSeek', 'Alibaba'].includes(m.provider)) },
        ]"
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
              <span class="model-provider">{{ model.provider }}</span>
            </div>
            <el-tag v-if="model.value === 'deepseek-chat'" type="primary" size="small">默认</el-tag>
          </div>
        </el-option>
      </el-option-group>
    </el-select>
  </div>
</template>

<style scoped>
.model-selector {
  min-width: 200px;
}

.model-select {
  width: 200px;
}

.selector-header {
  font-weight: 600;
  padding: 8px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.model-option-content {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
}

.model-icon {
  font-size: 20px;
}

.model-info {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.model-name {
  font-size: 14px;
  color: var(--el-text-color-primary);
}

.model-provider {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

:deep(.el-select-dropdown__item) {
  padding: 8px 12px;
}

:deep(.el-select-dropdown__item.is-selected) {
  background-color: var(--el-color-primary-light-9);
}
</style>
