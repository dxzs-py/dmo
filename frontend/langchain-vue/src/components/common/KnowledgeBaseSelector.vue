<template>
  <div class="kb-selector">
    <div class="kb-selector-header">
      <el-input
        v-model="kbSearchQuery"
        placeholder="搜索知识库..."
        clearable
        size="small"
        class="kb-search-input"
        @input="filterKnowledgeBases"
      />
      <el-button size="small" :loading="kbLoading" @click="refreshKnowledgeBases">
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
      <el-checkbox-group :model-value="modelValue" @update:model-value="$emit('update:modelValue', $event)">
        <div
          v-for="kb in filteredKnowledgeBases"
          :key="kb.id"
          class="kb-item"
        >
          <el-checkbox :label="kb.id" :value="kb.id" :disabled="disabled">
            <div class="kb-item-content">
              <span class="kb-name">{{ kb.name }}</span>
              <span class="kb-meta">
                <el-tag size="small" type="info">{{ kb.chunkCount || 0 }} 文档块</el-tag>
                <span v-if="kb.description" class="kb-desc">{{ kb.description }}</span>
              </span>
            </div>
          </el-checkbox>
        </div>
      </el-checkbox-group>
    </div>
    <div v-if="modelValue.length > 0" class="kb-selected-summary">
      已选择 {{ modelValue.length }} 个知识库
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElIcon } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import { knowledgeAPI } from '@/api/knowledge'
import { logger } from '@/utils/logger'

defineProps({
  modelValue: {
    type: Array,
    default: () => [],
  },
  disabled: {
    type: Boolean,
    default: false,
  },
})

defineEmits(['update:modelValue'])

const knowledgeBases = ref([])
const kbLoading = ref(false)
const kbSearchQuery = ref('')
const filteredKnowledgeBases = ref([])

const refreshKnowledgeBases = async () => {
  kbLoading.value = true
  try {
    const response = await knowledgeAPI.getKnowledgeBases()
    const data = response.data?.data || response.data
    knowledgeBases.value = data?.items || []
    filterKnowledgeBases()
  } catch (error) {
    logger.error('加载知识库列表失败:', error)
    ElMessage.error('加载知识库列表失败')
  } finally {
    kbLoading.value = false
  }
}

const filterKnowledgeBases = () => {
  const query = kbSearchQuery.value.toLowerCase().trim()
  if (!query) {
    filteredKnowledgeBases.value = [...knowledgeBases.value]
  } else {
    filteredKnowledgeBases.value = knowledgeBases.value.filter(
      kb => kb.name?.toLowerCase().includes(query) || kb.description?.toLowerCase().includes(query)
    )
  }
}

onMounted(() => {
  refreshKnowledgeBases()
})

defineExpose({
  refresh: refreshKnowledgeBases,
})
</script>

<style scoped>
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
  color: var(--el-text-color-primary);
}

.kb-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.kb-desc {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 300px;
}

.kb-selected-summary {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-extra-light);
  font-size: 12px;
  color: var(--el-color-primary);
}
</style>
