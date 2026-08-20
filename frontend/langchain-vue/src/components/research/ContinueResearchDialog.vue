<template>
  <el-dialog
    :model-value="modelValue"
    title="继续研究"
    width="600px"
    :close-on-click-modal="false"
    @update:model-value="handleVisibleChange"
  >
    <p style="margin-bottom: 12px; color: var(--el-text-color-secondary)">
      基于已有研究继续深入探索
    </p>
    <el-descriptions :column="1" border size="small" style="margin-bottom: 16px">
      <el-descriptions-item label="原研究主题">
        {{ parentTask?.query?.substring(0, 100) }}
      </el-descriptions-item>
      <el-descriptions-item label="版本">
        v{{ parentTask?.version || 1 }} → v{{ (parentTask?.version || 1) + 1 }}
      </el-descriptions-item>
    </el-descriptions>
    <el-form :model="continueForm" label-width="100px" @submit.prevent>
      <el-form-item label="补充说明">
        <el-input
          v-model="continueForm.additionalQuery"
          type="textarea"
          :rows="3"
          placeholder="描述你想继续探索的方向（可选）..."
        />
      </el-form-item>
      <el-form-item label="选择模型">
        <ModelSelector @change="onContinueModelChange" />
      </el-form-item>
      <el-form-item label="深度思考">
        <el-switch
          :model-value="useDeepThinking"
          :disabled="!modelSupportsDeepThinking"
          @change="useDeepThinking = $event"
        />
        <span v-if="!modelSupportsDeepThinking" class="deep-thinking-hint">
          当前模型不支持深度思考
        </span>
      </el-form-item>
      <el-form-item label="启用网络搜索">
        <el-switch v-model="continueForm.enableWebSearch" />
      </el-form-item>
      <el-form-item label="选择知识库">
        <div class="kb-selector">
          <div v-if="filteredKnowledgeBases.length === 0" class="kb-empty">
            <span>暂无可用知识库</span>
          </div>
          <div v-else class="kb-list">
            <el-checkbox-group v-model="continueForm.knowledgeBaseIds">
              <div v-for="kb in filteredKnowledgeBases" :key="kb.id" class="kb-item">
                <el-checkbox :label="kb.name" :value="kb.id">
                  <div class="kb-item-content">
                    <span class="kb-name">{{ kb.name }}</span>
                    <span class="kb-meta">
                      <el-tag size="small" type="info">{{ kb.chunkCount || 0 }} 文档块</el-tag>
                    </span>
                  </div>
                </el-checkbox>
              </div>
            </el-checkbox-group>
          </div>
        </div>
      </el-form-item>
      <el-form-item label="工具选择">
        <div class="tool-selector-wrapper">
          <ToolSelector
            :model-value="continueForm.selectedTools"
            @update:model-value="(val) => continueForm.selectedTools = val"
            @update:selected-mcp-servers="(val) => continueForm.selectedMcpServers = val"
            @update:use-mcp="(val) => continueForm.useMcp = val"
          />
          <span v-if="continueForm.selectedTools.length > 0" class="tool-selected-hint">
            已选择 {{ continueForm.selectedTools.length }} 个工具
          </span>
        </div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="handleCancel">取消</el-button>
      <el-button type="primary" :loading="submitLoading" @click="handleSubmit">
        开始续研
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { reactive, watch, computed } from 'vue'
import ModelSelector from '@/components/common/ModelSelector.vue'
import ToolSelector from '@/components/chat/ToolSelector.vue'
import { useResearchSettingsStore } from '@/stores/researchSettings'

const props = defineProps({
  /** 对话框显隐（v-model） */
  modelValue: { type: Boolean, default: false },
  /** 续研的父任务数据 */
  parentTask: { type: Object, default: null },
  /** 可选知识库列表（与主表单同源） */
  filteredKnowledgeBases: { type: Array, default: () => [] },
  /** 提交按钮 loading（开始研究/续研共用） */
  submitLoading: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue', 'submit'])

// 与视图同源的独立设置 store（根因 C：深度研究模块专用），
// ModelSelector 经视图 provide('modelSettingsStore') 注入同一实例
const researchSettings = useResearchSettingsStore()

const continueForm = reactive({
  additionalQuery: '',
  enableWebSearch: true,
  knowledgeBaseIds: [],
  providerId: null,
  modelName: null,
  useMcp: false,
  selectedMcpServers: [],
  selectedTools: [],
})

// 深度思考开关绑定设置 store（与主表单同源派生，行为一致）
const useDeepThinking = computed({
  get: () => researchSettings.thinkingEnabled,
  set: (val) => {
    const paramCfg = researchSettings.currentProviderSpecialParams?.thinking
    if (!paramCfg) return
    researchSettings.setSpecialParam('thinking', val ? paramCfg.enabledValue : paramCfg.disabledValue)
  },
})

const modelSupportsDeepThinking = computed(() => {
  return researchSettings.currentModelCapabilities.includes('deep_thinking')
})

const onContinueModelChange = ({ providerId, modelName }) => {
  continueForm.providerId = providerId
  continueForm.modelName = modelName
}

// 对话框打开（或打开状态下父任务切换）时从父任务初始化表单，
// 并将设置 store 当前模型切换为父任务模型（原 openContinueDialog 表单初始化段）
watch(
  () => [props.modelValue, props.parentTask],
  ([visible]) => {
    if (!visible) return
    const taskData = props.parentTask
    if (!taskData) return
    continueForm.additionalQuery = ''
    continueForm.enableWebSearch = taskData.enableWebSearch ?? true
    continueForm.knowledgeBaseIds = taskData.knowledgeBaseIds || []
    continueForm.providerId = taskData.providerId || null
    continueForm.modelName = taskData.modelName || null
    continueForm.useMcp = taskData.useMcp ?? false
    continueForm.selectedMcpServers = taskData.selectedMcpServers || []
    continueForm.selectedTools = taskData.selectedTools || []
    if (continueForm.providerId && continueForm.modelName) {
      researchSettings.selectProvider(continueForm.providerId, continueForm.modelName)
    }
  }
)

const handleVisibleChange = (visible) => {
  emit('update:modelValue', visible)
}

const handleCancel = () => {
  emit('update:modelValue', false)
}

const handleSubmit = () => {
  emit('submit', { ...continueForm })
}
</script>

<style scoped>
.kb-selector {
  width: 100%;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 12px;
  background: var(--el-fill-color-lighter);
}

.kb-empty {
  padding: 20px;
  text-align: center;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.kb-list {
  max-height: 240px;
  overflow-y: auto;
}

.kb-item {
  padding: 8px 4px;
  border-bottom: 1px solid var(--el-border-color-extra-light);
}

.kb-item:last-child {
  border-bottom: none;
}

.kb-item-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.kb-name {
  font-weight: 500;
  font-size: 14px;
}

.kb-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.tool-selector-wrapper {
  display: flex;
  align-items: center;
  gap: 12px;
}

.tool-selected-hint {
  font-size: 12px;
  color: var(--el-color-primary);
}

.deep-thinking-hint {
  margin-left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

@media (max-width: 768px) {
  .kb-list {
    max-height: 180px;
  }
}
</style>
