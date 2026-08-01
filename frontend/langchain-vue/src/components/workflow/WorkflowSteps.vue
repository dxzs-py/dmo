<template>
  <div class="workflow-progress">
    <!-- 局部 ErrorBoundary：步骤状态机渲染异常时仅替换进度区，保留学习计划与文件列表 -->
    <ErrorBoundary :full-screen="false">
      <AiCanvas class="progress-canvas">
        <div class="progress-steps">
          <template v-for="(step, idx) in workflowSteps" :key="step.key">
            <div
              :class="['progress-step', {
                'is-completed': completedSteps.includes(step.key),
                'is-active': currentStep === step.key,
                'is-pending': !completedSteps.includes(step.key) && currentStep !== step.key,
              }]"
            >
              <AiCheckpoint v-if="completedSteps.includes(step.key)" class="step-checkpoint">
                <span class="step-label">{{ step.label }}</span>
              </AiCheckpoint>
              <div v-else class="step-node-wrapper">
                <AiNode :title="step.label" :status="currentStep === step.key ? 'running' : ''" />
              </div>
            </div>
            <AiEdge
              v-if="idx < workflowSteps.length - 1"
              :status="completedSteps.includes(step.key) ? 'success' : (currentStep === step.key ? 'active' : 'default')"
            />
          </template>
        </div>
      </AiCanvas>
    </ErrorBoundary>
  </div>
</template>

<script setup>
import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
import AiCheckpoint from '@/components/ai-elements/AiCheckpoint.vue'
import AiNode from '@/components/ai-elements/AiNode.vue'
import AiEdge from '@/components/ai-elements/AiEdge.vue'
import AiCanvas from '@/components/ai-elements/AiCanvas.vue'

/**
 * 学习工作流 - 步骤可视化
 * 渲染 8 个固定步骤的 checkpoint / node / edge 状态机：
 *   - 已完成步骤（completedSteps）：渲染为 AiCheckpoint
 *   - 当前步骤（currentStep）：渲染为 AiNode（running 状态，带脉冲动画）
 *   - 待执行步骤：渲染为 AiNode（默认状态，半透明）
 */
defineProps({
  /** 步骤定义数组（{ key, label }） */
  workflowSteps: {
    type: Array,
    required: true,
  },
  /** 已完成步骤 key 数组（currentStep 之前的所有步骤） */
  completedSteps: {
    type: Array,
    default: () => [],
  },
  /** 当前步骤 key */
  currentStep: {
    type: String,
    default: '',
  },
})
</script>

<style scoped>
.workflow-progress {
  margin-bottom: 20px;
  padding: 16px;
  background: var(--el-fill-color-lighter);
  border-radius: 8px;
  overflow-x: auto;
}

.progress-canvas {
  background: transparent;
  min-height: auto;
}

.progress-steps {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: max-content;
}

.progress-step {
  flex-shrink: 0;
}

.progress-step.is-completed .step-label {
  color: var(--el-color-success);
  font-weight: 600;
}

.progress-step.is-active .step-node-wrapper {
  animation: pulse-border 2s ease-in-out infinite;
}

.progress-step.is-pending {
  opacity: 0.5;
}

.step-checkpoint {
  padding: 6px 10px;
}

.step-label {
  font-size: 13px;
}

.step-node-wrapper :deep(.ai-node) {
  min-width: 80px;
}

@keyframes pulse-border {
  0%, 100% { box-shadow: 0 0 0 0 rgba(64, 158, 255, 0.3); }
  50% { box-shadow: 0 0 0 4px rgba(64, 158, 255, 0.1); }
}

@media (max-width: 480px) {
  .workflow-progress {
    padding: 8px;
  }
}
</style>
