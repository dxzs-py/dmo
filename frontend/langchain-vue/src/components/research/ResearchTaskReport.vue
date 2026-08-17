<template>
  <div class="report-wrapper">
    <!-- 研究报告（research 特殊展示，独立组件，spec 8.2） -->
    <div v-if="task.finalReport" class="report-section">
      <div class="report-header">
        <h4>研究报告</h4>
      </div>
      <div class="report-content">
        <!-- 局部 ErrorBoundary：研究报告 Markdown 渲染畸形内容时仅替换报告区，保留审批面板与文件列表 -->
        <ErrorBoundary>
          <MarkdownRenderer :content="task.finalReport" />
        </ErrorBoundary>
      </div>
    </div>

    <div v-if="task.versionChain && task.versionChain.length > 1" class="version-chain">
      <span v-for="(v, idx) in task.versionChain" :key="v.taskId">
        <el-tag
          :type="v.taskId === task.taskId ? 'primary' : 'info'"
          size="small"
          class="version-tag"
          :style="v.taskId === task.taskId ? '' : 'cursor: pointer'"
          @click="v.taskId !== task.taskId && emit('view-task', { taskId: v.taskId })"
        >
          v{{ v.version }}
        </el-tag>
        <span v-if="idx < task.versionChain.length - 1" class="version-arrow">&rarr;</span>
      </span>
    </div>

    <div class="report-actions">
      <el-button
        v-if="task.status === ResearchTaskStatus.COMPLETED"
        type="success"
        size="small"
        @click="emit('open-continue-dialog', task)"
      >
        继续研究
      </el-button>
      <!-- 在聊天中讨论：与"继续研究"一致，仅任务完成后可跳转讨论研究结果 -->
      <AiOpenInChat
        v-if="task.status === ResearchTaskStatus.COMPLETED"
        label="在聊天中讨论"
        @click="emit('open-in-chat')"
      />
    </div>

    <div v-if="docAnalysisFile" class="analysis-section">
      <el-divider />
      <div class="analysis-header">
        <h4>文档分析详情</h4>
        <el-button
          v-if="!docAnalysisContent"
          size="small"
          :loading="docAnalysisLoading"
          @click="emit('load-doc-analysis')"
        >
          查看分析依据
        </el-button>
      </div>
      <div v-if="docAnalysisLoading" class="analysis-loading">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>加载分析详情...</span>
      </div>
      <div v-else-if="docAnalysisContent" class="analysis-content">
        <ErrorBoundary>
          <MarkdownRenderer :content="docAnalysisContent" />
        </ErrorBoundary>
      </div>
    </div>

    <el-divider />

    <div class="files-section">
      <h4>生成的文档</h4>
      <FileBrowser
        :task-id="task.taskId"
        :task-status="task.status"
        :api="deepResearchAPI"
      />
    </div>
  </div>
</template>

<script setup>
import { Loading } from '@element-plus/icons-vue'
import FileBrowser from '@/components/chat/FileBrowser.vue'
import MarkdownRenderer from '@/components/common/MarkdownRenderer.vue'
import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
import AiOpenInChat from '@/components/ai-elements/AiOpenInChat.vue'
import { deepResearchAPI } from '@/api/research'
import { ResearchTaskStatus } from '@/types'
/**
 * 深度研究 - 任务结果区
 * 包含：版本链 / 文档分析详情 / 生成的文件列表
 * 最终报告（final_report）已内联进 ResearchTaskDetail 的研究过程正文（spec 8.1），
 * 本组件不再单独渲染报告 Markdown。
 */
defineProps({
  /** 当前任务对象 */
  task: {
    type: Object,
    required: true,
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
})

const emit = defineEmits([
  'view-task',
  'open-continue-dialog',
  'open-in-chat',
  'load-doc-analysis',
])
</script>

<style scoped>
.report-section {
  margin-top: 24px;
}

.report-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.report-header h4 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.report-content {
  padding: 20px;
  background: var(--el-fill-color-lighter);
  border-radius: 4px;
  margin-bottom: 16px;
}

.report-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.version-chain {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.version-tag {
  cursor: default;
}

.version-arrow {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.analysis-section {
  margin-top: 8px;
}

.analysis-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.analysis-header h4 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.analysis-loading {
  padding: 20px;
  text-align: center;
  color: var(--el-text-color-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.analysis-content {
  padding: 16px;
  background: var(--el-fill-color-lighter);
  border-radius: 4px;
  border-left: 3px solid var(--el-color-primary);
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

@media (max-width: 480px) {
  .report-actions {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
}
</style>
