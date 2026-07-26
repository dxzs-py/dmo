<template>
  <el-form :model="localConfig" :label-width="labelWidth" @submit.prevent>
    <!-- 知识库选择（可选，由 showKnowledgeBase 控制） -->
    <el-form-item v-if="showKnowledgeBase" label="选择知识库">
      <KnowledgeBaseSelector
        :model-value="localConfig.knowledge_base_ids"
        @update:model-value="updateField('knowledge_base_ids', $event)"
      />
    </el-form-item>

    <!-- 模型选择 -->
    <el-form-item label="选择模型">
      <ModelSelector @change="onModelChange" />
    </el-form-item>

    <!-- 深度思考开关（仅当模型支持时可用） -->
    <el-form-item v-if="showDeepThinking" label="深度思考">
      <el-switch
        :model-value="useDeepThinking"
        :disabled="!modelSupportsDeepThinking"
        @change="onDeepThinkingChange"
      />
      <span v-if="!modelSupportsDeepThinking" class="deep-thinking-hint">
        当前模型不支持深度思考
      </span>
    </el-form-item>

    <!-- 工具选择（可选，由 showTools 控制） -->
    <el-form-item v-if="showTools" label="工具选择">
      <div class="tool-selector-wrapper">
        <ToolSelector
          :model-value="localConfig.selected_tools"
          @update:model-value="updateField('selected_tools', $event)"
          @update:selected-mcp-servers="updateField('selected_mcp_servers', $event)"
          @update:use-mcp="updateField('use_mcp', $event)"
        />
        <span v-if="localConfig.selected_tools.length > 0" class="tool-selected-hint">
          已选择 {{ localConfig.selected_tools.length }} 个工具
        </span>
      </div>
    </el-form-item>
  </el-form>
</template>

<script setup>
import { reactive, computed, watch } from 'vue'
import ModelSelector from '@/components/common/ModelSelector.vue'
import ToolSelector from '@/components/chat/ToolSelector.vue'
import KnowledgeBaseSelector from '@/components/common/KnowledgeBaseSelector.vue'
import { useModelStore } from '@/stores/model'

const props = defineProps({
  // 统一配置对象：与后端 ModelConfigSerializer + knowledge_base_ids 字段对齐
  modelValue: {
    type: Object,
    required: true,
    default: () => ({
      knowledge_base_ids: [],
      provider_id: null,
      model_name: null,
      temperature: null,
      max_tokens: null,
      special_params: {},
      selected_tools: [],
      selected_mcp_servers: [],
      use_mcp: false,
      enable_deep_thinking: false,
    }),
  },
  // 各字段显示开关，便于不同业务场景按需裁剪
  showKnowledgeBase: {
    type: Boolean,
    default: true,
  },
  showTools: {
    type: Boolean,
    default: true,
  },
  showDeepThinking: {
    type: Boolean,
    default: true,
  },
  labelWidth: {
    type: String,
    default: '120px',
  },
})

const emit = defineEmits(['update:modelValue'])

const modelStore = useModelStore()

// 内部维护一份配置副本，避免直接 mutate props
const localConfig = reactive({
  knowledge_base_ids: [],
  provider_id: null,
  model_name: null,
  temperature: null,
  max_tokens: null,
  special_params: {},
  selected_tools: [],
  selected_mcp_servers: [],
  use_mcp: false,
  enable_deep_thinking: false,
  ...props.modelValue,
})

// 同步外部变更到内部副本
watch(
  () => props.modelValue,
  (val) => {
    if (!val) return
    Object.assign(localConfig, val)
  },
  { deep: true }
)

const emitChange = () => {
  emit('update:modelValue', { ...localConfig })
}

const updateField = (field, value) => {
  localConfig[field] = value
  emitChange()
}

const onModelChange = ({ providerId, modelName, specialParams }) => {
  localConfig.provider_id = providerId
  localConfig.model_name = modelName
  // ModelSelector 返回的 specialParams 是 store 当前快照，与深度思考开关状态联动
  if (specialParams) {
    localConfig.special_params = { ...specialParams }
  }
  emitChange()
}

const modelSupportsDeepThinking = computed(() => {
  return modelStore.currentModelCapabilities.includes('deep_thinking')
})

const useDeepThinking = computed(() => modelStore.thinkingEnabled)

const onDeepThinkingChange = (val) => {
  const paramCfg = modelStore.currentProviderSpecialParams?.thinking
  if (!paramCfg) return
  modelStore.setSpecialParam('thinking', val ? paramCfg.enabled_value : paramCfg.disabled_value)
  // 同步到 localConfig.special_params（modelStore 已更新，重新读取）
  localConfig.special_params = { ...modelStore.specialParams }
  localConfig.enable_deep_thinking = val
  emitChange()
}
</script>

<style scoped>
.tool-selector-wrapper {
  width: 100%;
}

.tool-selected-hint {
  display: inline-block;
  margin-left: 8px;
  font-size: 12px;
  color: var(--el-color-primary);
}

.deep-thinking-hint {
  display: inline-block;
  margin-left: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
