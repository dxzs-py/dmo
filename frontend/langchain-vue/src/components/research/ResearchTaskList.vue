<template>
  <el-card class="task-list-card">
    <template #header>
      <div class="card-header">
        <span>历史任务</span>
        <el-button text type="primary" @click="emit('update:show-file-search', !showFileSearch)">
          {{ showFileSearch ? '收起搜索' : '全局文件搜索' }}
        </el-button>
      </div>
    </template>

    <div v-if="showFileSearch" class="file-search-section">
      <el-input
        :model-value="fileSearchQuery"
        placeholder="搜索所有研究任务的文件..."
        clearable
        @update:model-value="(v) => emit('update:file-search-query', v)"
        @keyup.enter="emit('file-search')"
      >
        <template #append>
          <el-button :loading="fileSearchLoading" @click="emit('file-search')">搜索</el-button>
        </template>
      </el-input>
      <div v-if="fileSearchResults.length" class="file-search-results">
        <el-table :data="fileSearchResults" style="width: 100%" size="small">
          <el-table-column prop="filename" label="文件名" />
          <el-table-column prop="taskId" label="任务ID" width="160" />
          <el-table-column prop="size" label="大小" width="100">
            <template #default="scope">
              {{ scope.row.size ? formatFileSize(scope.row.size) : '-' }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="100">
            <template #default="scope">
              <el-button link type="primary" size="small" @click="emit('view-task', { task_id: scope.row.taskId })">
                查看任务
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <el-empty v-else-if="fileSearchSearched && !fileSearchLoading" description="未找到匹配的文件" :image-size="60" />
    </div>

    <TaskList
      :ref="taskListRef"
      module-type="deep-research"
      :api="deepResearchAPI"
      :status-options="statusOptions"
      @view-task="(val) => emit('view-task', val)"
      @delete-task="emit('delete-task')"
      @continue-task="(val) => emit('continue-task', val)"
    />
  </el-card>
</template>

<script setup>
import TaskList from '@/components/chat/TaskList.vue'
import { deepResearchAPI } from '@/api/research'
import { formatFileSize } from '@/utils/format'

/**
 * 深度研究 - 任务列表卡片
 * 包含：全局文件搜索区 + 历史任务列表（复用 TaskList 组件）
 *
 * TaskList 的 ref 通过 taskListRef prop 透传（view 层级维护）。
 */
defineProps({
  /** 是否显示文件搜索区 */
  showFileSearch: {
    type: Boolean,
    default: false,
  },
  /** 文件搜索关键词 */
  fileSearchQuery: {
    type: String,
    default: '',
  },
  /** 文件搜索结果 */
  fileSearchResults: {
    type: Array,
    default: () => [],
  },
  /** 文件搜索加载中 */
  fileSearchLoading: {
    type: Boolean,
    default: false,
  },
  /** 文件搜索是否已执行（用于空状态展示） */
  fileSearchSearched: {
    type: Boolean,
    default: false,
  },
  /** 任务状态筛选选项 */
  statusOptions: {
    type: Array,
    default: () => [],
  },
  /** TaskList 组件 ref（Ref 对象，透传绑定） */
  taskListRef: {
    type: Object,
    required: true,
  },
})

const emit = defineEmits([
  'update:show-file-search',
  'update:file-search-query',
  'view-task',
  'delete-task',
  'continue-task',
  'file-search',
])
</script>

<style scoped>
.task-list-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.file-search-section {
  margin-bottom: 16px;
  padding: 16px;
  background: var(--el-fill-color-lighter);
  border-radius: 8px;
}

.file-search-results {
  margin-top: 12px;
}

@media (max-width: 768px) {
  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
}

@media (max-width: 480px) {
  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
}
</style>
