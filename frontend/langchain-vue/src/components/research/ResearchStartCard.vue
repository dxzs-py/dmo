<template>
  <el-card class="start-card">
    <template #header>
      <div class="card-header">
        <span class="page-title">深度研究</span>
      </div>
    </template>
    <el-form :model="researchForm" label-width="120px" @submit.prevent>
      <el-form-item label="研究主题">
        <el-input
          v-model="researchForm.query"
          type="textarea"
          :rows="3"
          placeholder="请输入您想研究的主题..."
        />
      </el-form-item>
      <el-form-item label="启用网络搜索">
        <el-switch v-model="researchForm.enableWebSearch" />
      </el-form-item>
      <el-form-item label="选择知识库">
        <div class="kb-selector">
          <div class="kb-selector-header">
            <el-input
              :model-value="kbSearchQuery"
              placeholder="搜索知识库..."
              clearable
              size="small"
              class="kb-search-input"
              @update:model-value="(v) => emit('update:kb-search-query', v)"
              @input="emit('filter-knowledge-bases')"
            />
            <el-button size="small" :loading="kbLoading" @click="emit('refresh-knowledge-bases')">
              刷新
            </el-button>
          </div>
          <div v-if="kbLoading" class="kb-loading">
            <el-icon class="is-loading"><Loading /></el-icon>
            <span>加载知识库列表...</span>
          </div>
          <div v-else-if="filteredKnowledgeBases.length === 0" class="kb-empty">
            <span v-if="kbSearchQuery">未找到匹配的知识库</span>
            <span v-else>暂无可用知识库，请先在知识库页面创建并上传文档</span>
          </div>
          <div v-else class="kb-list">
            <el-checkbox-group v-model="researchForm.knowledgeBaseIds">
              <div
                v-for="kb in filteredKnowledgeBases"
                :key="kb.id"
                class="kb-item"
              >
                <el-checkbox :label="kb.id" :value="kb.id">
                  <div class="kb-item-content">
                    <span class="kb-name">{{ kb.name }}</span>
                    <span class="kb-meta">
                      <el-tag size="small" type="info">{{ kb.chunk_count || 0 }} 文档块</el-tag>
                      <span v-if="kb.description" class="kb-desc">{{ kb.description }}</span>
                    </span>
                  </div>
                </el-checkbox>
              </div>
            </el-checkbox-group>
          </div>
          <div v-if="researchForm.knowledgeBaseIds.length > 0" class="kb-selected-summary">
            已选择 {{ researchForm.knowledgeBaseIds.length }} 个知识库
          </div>
        </div>
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
      <el-form-item label="工具选择">
        <div class="tool-selector-wrapper">
          <ToolSelector
            :model-value="researchForm.selectedTools"
            @update:model-value="(val) => researchForm.selectedTools = val"
            @update:selected-mcp-servers="(val) => researchForm.selectedMcpServers = val"
            @update:use-mcp="(val) => researchForm.useMcp = val"
          />
          <span v-if="researchForm.selectedTools.length > 0" class="tool-selected-hint">
            已选择 {{ researchForm.selectedTools.length }} 个工具
          </span>
        </div>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="isLoading" @click="emit('start')">
          开始研究
        </el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<script setup>
import { Loading } from '@element-plus/icons-vue'
import ModelSelector from '@/components/common/ModelSelector.vue'
import ToolSelector from '@/components/chat/ToolSelector.vue'

/**
 * 深度研究 - 开始卡片
 * 包含研究输入表单：研究主题 / 网络搜索 / 知识库选择 / 模型选择 / 深度思考 / 工具选择 / 开始按钮
 *
 * 注：researchForm 为 reactive 对象（父组件传入引用），子组件直接 v-model 绑定其属性，
 * 这是 Vue 3 reactive 对象传递的合理用法（修改对象内部属性，不重新赋值 prop 引用）。
 */
defineProps({
  /** 研究表单（reactive 对象，子组件直接绑定其属性） */
  researchForm: {
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
  /** 是否加载中（开始研究按钮 loading） */
  isLoading: {
    type: Boolean,
    default: false,
  },
  /** 知识库列表加载中 */
  kbLoading: {
    type: Boolean,
    default: false,
  },
  /** 知识库搜索关键词 */
  kbSearchQuery: {
    type: String,
    default: '',
  },
  /** 过滤后的知识库列表 */
  filteredKnowledgeBases: {
    type: Array,
    default: () => [],
  },
})

const emit = defineEmits([
  'start',
  'model-change',
  'refresh-knowledge-bases',
  'filter-knowledge-bases',
  'update:use-deep-thinking',
  'update:kb-search-query',
])
</script>

<style scoped>
.start-card {
  margin-bottom: 20px;
}

.page-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.kb-selector {
  width: 100%;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 12px;
  background: var(--el-fill-color-lighter);
}

.kb-selector-header {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.kb-search-input {
  flex: 1;
}

.kb-loading,
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

.kb-desc {
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-selected-summary {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-extra-light);
  font-size: 12px;
  color: var(--el-color-primary);
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
  .page-title {
    font-size: 15px;
  }

  .kb-selector {
    padding: 8px;
  }

  .kb-list {
    max-height: 180px;
  }
}

@media (max-width: 480px) {
  .page-title {
    font-size: 14px;
  }

  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .kb-selector-header {
    flex-direction: column;
  }

  .kb-desc {
    max-width: 180px;
  }
}
</style>
