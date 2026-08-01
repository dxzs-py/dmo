<template>
  <el-card class="task-detail-card">
    <template #header>
      <div class="card-header">
        <span>研究任务详情</span>
        <div class="header-actions">
          <el-tag v-if="progressMessage" type="info" class="progress-message">
            {{ progressMessage }}
          </el-tag>
          <el-tag :type="getStatusType(task.status)">
            {{ getStatusText(task.status) }}
          </el-tag>
          <el-button link type="primary" size="small" @click="emit('back')">
            返回列表
          </el-button>
        </div>
      </div>
    </template>

    <el-descriptions :column="2" border>
      <el-descriptions-item label="任务ID">{{ task.task_id }}</el-descriptions-item>
      <el-descriptions-item label="创建时间">{{ formatDate(task.created_at) }}</el-descriptions-item>
      <el-descriptions-item label="研究主题" :span="2">{{ task.query }}</el-descriptions-item>
      <el-descriptions-item label="网络搜索">
        <el-tag :type="task.enable_web_search ? 'success' : 'info'">
          {{ task.enable_web_search ? '已启用' : '未启用' }}
        </el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="文档分析">
        <el-tag :type="task.enable_doc_analysis ? 'success' : 'info'">
          {{ task.enable_doc_analysis ? '已启用' : '未启用' }}
        </el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="来源">
        <el-tag v-if="task.source === 'chat'" type="primary">
          <el-icon style="vertical-align: middle; margin-right: 4px;"><ChatDotRound /></el-icon>
          聊天触发
        </el-tag>
        <el-tag v-else type="info">独立研究</el-tag>
      </el-descriptions-item>
      <el-descriptions-item
        v-if="task.enable_doc_analysis && task.knowledge_base_ids && task.knowledge_base_ids.length"
        label="关联知识库"
        :span="2"
      >
        <el-tag
          v-for="kbId in task.knowledge_base_ids"
          :key="kbId"
          size="small"
          class="kb-tag"
        >
          {{ kbId }}
        </el-tag>
      </el-descriptions-item>
    </el-descriptions>

    <div v-if="task.status === 'running' || task.status === 'pending'" class="progress-section">
      <el-progress
        :percentage="progressPercentage"
        :status="task.status === 'pending' ? '' : undefined"
        :stroke-width="8"
        striped
        striped-flow
      />
      <p class="progress-hint">深度研究通常需要 5-10 分钟，请耐心等待...</p>
    </div>

    <!-- 审批面板 -->
    <div v-if="taskPendingApprovals.size > 0" class="approval-section">
      <!-- 局部 ErrorBoundary：审批卡片渲染畸形 approval 数据时仅替换审批区，保留报告与文件列表 -->
      <ErrorBoundary :full-screen="false">
        <ToolCallCard
          v-for="[id, entry] in taskPendingApprovals"
          :key="id"
          :tool-name="entry.approvalData?.tool_name || 'unknown'"
          :description="entry.approvalData?.description || ''"
          :status="mapApprovalStateToStatus(entry.approvalData?.state) || 'pending_approval'"
          :tool-call="{ approval: entry.approvalData, id: entry.approvalData?.interrupt_id || entry.approvalData?.tool_call_id || id }"
          @approve="(data) => emit('approve', data)"
          @reject="(data) => emit('reject', data)"
        />
      </ErrorBoundary>
    </div>

    <ResearchTaskReport
      :task="task"
      :doc-analysis-file="docAnalysisFile"
      :doc-analysis-content="docAnalysisContent"
      :doc-analysis-loading="docAnalysisLoading"
      :file-browser-ref="fileBrowserRef"
      @view-task="(val) => emit('view-task', val)"
      @open-continue-dialog="(val) => emit('open-continue-dialog', val)"
      @open-in-chat="emit('open-in-chat')"
      @load-doc-analysis="emit('load-doc-analysis')"
    />
  </el-card>
</template>

<script setup>
import { ChatDotRound } from '@element-plus/icons-vue'
import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
import ToolCallCard from '@/components/chat/ToolCallCard.vue'
import ResearchTaskReport from './ResearchTaskReport.vue'
import { formatDate } from '@/utils/format'
import { mapApprovalStateToStatus } from '@/types'

/**
 * 深度研究 - 任务详情卡片
 * 包含：任务描述 / 进度条 / 审批面板 / 报告区（嵌入 ResearchTaskReport）
 *
 * 状态展示辅助函数（getStatusType / getStatusText）保留在本组件内，
 * 与 TaskList.vue 中的同名函数职责一致但映射表不同（本组件仅覆盖研究任务状态）。
 */
defineProps({
  /** 当前任务对象 */
  task: {
    type: Object,
    required: true,
  },
  /** 进度提示消息（SSE 推送） */
  progressMessage: {
    type: String,
    default: '',
  },
  /** 进度百分比 */
  progressPercentage: {
    type: Number,
    default: 0,
  },
  /** 当前任务的待审批条目（Map：interruptId -> entry） */
  taskPendingApprovals: {
    type: Map,
    default: () => new Map(),
  },
  /** 文档分析文件路径 */
  docAnalysisFile: {
    type: String,
    default: null,
  },
  /** 文档分析内容 */
  docAnalysisContent: {
    type: String,
    default: null,
  },
  /** 文档分析加载中 */
  docAnalysisLoading: {
    type: Boolean,
    default: false,
  },
  /** FileBrowser 组件 ref（Ref 对象，透传给 ResearchTaskReport） */
  fileBrowserRef: {
    type: Object,
    required: true,
  },
})

const emit = defineEmits([
  'back',
  'view-task',
  'open-continue-dialog',
  'open-in-chat',
  'load-doc-analysis',
  'approve',
  'reject',
])

const getStatusType = (status) => {
  const typeMap = {
    pending: 'info',
    progress: 'warning',
    running: 'warning',
    completed: 'success',
    failed: 'danger',
  }
  return typeMap[status] || 'info'
}

const getStatusText = (status) => {
  const textMap = {
    pending: '待执行',
    progress: '执行中',
    running: '执行中',
    completed: '已完成',
    failed: '失败',
  }
  return textMap[status] || status
}
</script>

<style scoped>
.task-detail-card {
  margin-bottom: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.progress-message {
  animation: pulse-opacity 2s ease-in-out infinite;
}

@keyframes pulse-opacity {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

.progress-section {
  margin-top: 24px;
  padding: 16px;
  background: var(--el-fill-color-lighter);
  border-radius: 8px;
}

.progress-hint {
  margin: 12px 0 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  text-align: center;
}

.kb-tag {
  margin-right: 6px;
  margin-bottom: 4px;
}

.approval-section {
  margin: 16px 0;
}

@media (max-width: 768px) {
  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .header-actions {
    width: 100%;
    flex-wrap: wrap;
  }

  .progress-section {
    padding: 12px;
  }
}

@media (max-width: 480px) {
  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .header-actions {
    flex-wrap: wrap;
    gap: 8px;
  }
}
</style>
