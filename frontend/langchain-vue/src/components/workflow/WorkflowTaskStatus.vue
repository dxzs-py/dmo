<template>
  <div>
    <el-card class="status-card">
      <template #header>
        <div class="card-header">
          <span>状态信息</span>
        </div>
      </template>

      <el-descriptions :column="1" border size="small">
        <el-descriptions-item label="线程ID">{{ execution.threadId }}</el-descriptions-item>
        <el-descriptions-item label="查询">{{ execution.userQuestion }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ formatDate(execution.createdAt) }}</el-descriptions-item>
        <el-descriptions-item label="更新时间">{{ formatDate(execution.updatedAt) }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-divider />

    <div v-if="autoLoadLoading" class="auto-load-section">
      <el-card>
        <div class="auto-load-loading">
          <el-icon class="is-loading" :size="16"><Loading /></el-icon>
          <span>正在加载学习资料...</span>
        </div>
      </el-card>
    </div>

    <div v-else-if="autoLoadContent" class="auto-load-section">
      <el-card>
        <template #header>
          <div class="card-header">
            <span>学习笔记</span>
          </div>
        </template>
        <MarkdownRenderer :content="autoLoadContent" />
      </el-card>
    </div>

    <div class="files-section">
      <h4>生成的文件</h4>
      <FileBrowser
        :ref="fileBrowserRef"
        :task-id="execution.threadId"
        :api="workflowAPI"
      />
    </div>
  </div>
</template>

<script setup>
import { Loading } from '@element-plus/icons-vue'
import FileBrowser from '@/components/chat/FileBrowser.vue'
import MarkdownRenderer from '@/components/common/MarkdownRenderer.vue'
import { formatDate } from '@/utils/format'
import { workflowAPI } from '@/api/workflow'

/**
 * 学习工作流 - 任务状态信息
 * 包含：状态信息卡片（thread_id / 查询 / 时间戳）+ 自动加载的学习笔记 + 生成的文件列表
 *
 * FileBrowser 的 ref 通过 fileBrowserRef prop 透传（view 层级维护，
 * 同时注入到 useWorkflowExecution / useWorkflowQuiz 等 composable）。
 */
defineProps({
  /** 当前工作流执行状态对象 */
  execution: {
    type: Object,
    required: true,
  },
  /** 自动加载的学习资料内容（null 表示未加载/无内容） */
  autoLoadContent: {
    type: String,
    default: null,
  },
  /** 学习资料加载中 */
  autoLoadLoading: {
    type: Boolean,
    default: false,
  },
  /** FileBrowser 组件 ref（Ref 对象，透传绑定） */
  fileBrowserRef: {
    type: Object,
    required: true,
  },
})
</script>

<style scoped>
.status-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.auto-load-section {
  margin-bottom: 20px;
}

.auto-load-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 20px;
  color: var(--el-text-color-secondary);
}

.files-section {
  margin-top: 8px;
}

.files-section h4 {
  margin: 0 0 16px 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

@media (max-width: 768px) {
  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
}
</style>
