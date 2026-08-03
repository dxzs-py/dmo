<template>
  <el-dialog
    :model-value="modelValue"
    title="继续研究"
    width="600px"
    :close-on-click-modal="false"
    @update:model-value="(v) => emit('update:model-value', v)"
  >
    <p style="margin-bottom: 12px; color: var(--el-text-color-secondary)">
      基于已有研究继续深入探索
    </p>
    <el-descriptions :column="1" border size="small" style="margin-bottom: 16px">
      <el-descriptions-item label="原研究主题">
        {{ continueParentTask?.query?.substring(0, 100) }}
      </el-descriptions-item>
      <el-descriptions-item label="版本">
        v{{ continueParentTask?.version || 1 }} → v{{ (continueParentTask?.version || 1) + 1 }}
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
        <ModelSelector @change="(val) => emit('model-change', val)" />
      </el-form-item>
      <el-form-item label="深度思考">
        <el-switch
          :model-value="useDeepThinking"
          :disabled="!modelSupportsDeepThinking"
          @change="(v) => emit('update:use-deep-thinking', v)"
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
      <el-button @click="emit('update:model-value', false)">取消</el-button>
      <el-button type="primary" :loading="isLoading" @click="emit('submit')">
        开始续研
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import ModelSelector from '@/components/common/ModelSelector.vue'
import ToolSelector from '@/components/chat/ToolSelector.vue'

/**
 * 深度研究 - 继续研究对话框
 * 基于已完成任务派生新版本，包含：补充说明 / 模型选择 / 深度思考 / 网络搜索 / 知识库 / 工具选择
 *
 * continueForm 为 reactive 对象（父组件传入引用），子组件直接 v-model 绑定其属性。
 */
defineProps({
  /** 对话框可见性（v-model） */
  modelValue: {
    type: Boolean,
    default: false,
  },
  /** 续研父任务对象 */
  continueParentTask: {
    type: Object,
    default: null,
  },
  /** 续研表单（reactive 对象，子组件直接绑定其属性） */
  continueForm: {
    type: Object,
    required: true,
  },
  /** 深度思考开关值 */
  useDeepThinking: {
    type: Boolean,
    default: false,
  },
  /** 当前模型是否支持深度思考 */
  modelSupportsDeepThinking: {
    type: Boolean,
    default: false,
  },
  /** 是否加载中（开始续研按钮 loading） */
  isLoading: {
    type: Boolean,
    default: false,
  },
  /** 过滤后的知识库列表 */
  filteredKnowledgeBases: {
    type: Array,
    default: () => [],
  },
})

const emit = defineEmits([
  'update:model-value',
  'update:use-deep-thinking',
  'model-change',
  'submit',
])
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
  .kb-selector {
    padding: 8px;
  }

  .kb-list {
    max-height: 180px;
  }
}
</style>
