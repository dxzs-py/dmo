<script setup>
/**
 * 工具调用展示区。
 *
 * 渲染消息关联的工具调用列表：
 * - 多个工具调用时用 AiQueue 队列包裹，提供折叠/展开能力
 * - 单个工具调用直接渲染 ToolCallCard
 *
 * 审批确认/拒绝事件从 ToolCallCard 冒泡至父组件统一处理。
 */
import { Tools } from '@element-plus/icons-vue'
import ToolCallCard from './ToolCallCard.vue'
import AiQueue from '../ai-elements/AiQueue.vue'
import { ToolCallStatus } from '../../types'

defineProps({
  toolCalls: {
    type: Array,
    default: () => [],
  },
})

const emit = defineEmits({
  approve: (toolCall) => toolCall && typeof toolCall === 'object',
  reject: (toolCall) => toolCall && typeof toolCall === 'object',
})

/** 将后端 state 字段映射为前端 ToolCallStatus 枚举 */
function mapStatus(toolCall) {
  if (toolCall.status) return toolCall.status
  if (toolCall.state === 'output-error') return ToolCallStatus.FAILED
  if (toolCall.state === 'output-available') return ToolCallStatus.COMPLETED
  return ToolCallStatus.RUNNING
}
</script>

<template>
  <div class="message-tool-calls">
    <div class="tool-calls-header">
      <span class="tool-calls-label">
        <el-icon :size="14"><Tools /></el-icon>
        工具调用 ({{ toolCalls.length }})
      </span>
    </div>
    <TransitionGroup name="tool-call" tag="div" class="tool-calls-list">
      <AiQueue v-if="toolCalls.length > 1" :items="toolCalls" class="tool-calls-queue">
        <ToolCallCard
          v-for="(toolCall, idx) in toolCalls"
          :key="toolCall.id || idx"
          :tool-name="toolCall.name"
          :input="toolCall.input || toolCall.parameters"
          :output="toolCall.output || toolCall.result"
          :status="mapStatus(toolCall)"
          :tool-call="toolCall"
          @approve="(tc) => emit('approve', tc)"
          @reject="(tc) => emit('reject', tc)"
        />
      </AiQueue>
      <ToolCallCard
        v-else-if="toolCalls.length === 1"
        :tool-name="toolCalls[0].name"
        :input="toolCalls[0].input || toolCalls[0].parameters"
        :output="toolCalls[0].output || toolCalls[0].result"
        :status="mapStatus(toolCalls[0])"
        :tool-call="toolCalls[0]"
        @approve="(tc) => emit('approve', tc)"
        @reject="(tc) => emit('reject', tc)"
      />
    </TransitionGroup>
  </div>
</template>

<style scoped>
.message-tool-calls {
  margin-top: 12px;
}

.tool-calls-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.tool-calls-label {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--muted-foreground);
  font-weight: 500;
}

.tool-calls-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tool-calls-queue :deep(.queue-items) {
  max-height: 300px;
}

.tool-calls-queue :deep(.queue-header) {
  padding: 8px 12px;
}

.tool-calls-queue :deep(.queue-title) {
  font-size: 13px;
}

.tool-call-enter-active {
  transition: all 0.3s ease;
}

.tool-call-enter-from {
  opacity: 0;
  transform: translateY(-8px);
}
</style>
