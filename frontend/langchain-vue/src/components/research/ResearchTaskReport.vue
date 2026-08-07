<template>
  <div class="report-wrapper">
    <div v-if="task.finalReport" class="report-section">
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
      <div class="report-header">
        <h4>研究报告</h4>
        <div class="report-header-actions">
          <el-button
            v-if="task.status === ResearchTaskStatus.COMPLETED"
            type="success"
            size="small"
            @click="emit('open-continue-dialog', task)"
          >
            继续研究
          </el-button>
          <AiOpenInChat label="在聊天中讨论" @click="emit('open-in-chat')" />
        </div>
      </div>
      <div class="report-content">
        <!-- 局�?ErrorBoundary：研究报�?Markdown 渲染畸形内容时仅替换报告区，保留审批面板与文件列�?-->
        <ErrorBoundary :full-screen="false">
          <MarkdownRenderer :content="task.finalReport" />
        </ErrorBoundary>
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
          <ErrorBoundary :full-screen="false">
            <MarkdownRenderer :content="docAnalysisContent" />
          </ErrorBoundary>
        </div>
      </div>
    </div>

    <el-divider />

    <div class="files-section">
      <h4>生成的文档</h4>
      <FileBrowser
        :ref="fileBrowserRef"
        :task-id="task.taskId"
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
 * 深度研究 - 任务报告
 * 包含：版本链 / 研究报告 Markdown / 文档分析详情 / 生成的文件列表
 * FileBrowser 的 ref 通过 fileBrowserRef prop 透传（view 层级维护）
 * 同时注入 useResearchPolling / useResearchStream 等 composable
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
  /** 文档分析加载�?*/
  docAnalysisLoading: {
    type: Boolean,
    default: false,
  },
  /** FileBrowser 组件 ref（Ref 对象，透传绑定�?*/
  fileBrowserRef: {
    type: Object,
    required: true,
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
}

.report-section h4 {
  margin: 0 0 16px 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.report-content {
  padding: 20px;
  background: var(--el-fill-color-lighter);
  border-radius: 4px;
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

.report-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
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

@media (max-width: 768px) {
  .report-content {
    padding: 12px;
  }
}

@media (max-width: 480px) {
  .report-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
}
</style>
