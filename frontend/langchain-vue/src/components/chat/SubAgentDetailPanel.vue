<script setup>
/**
 * SubAgentDetailPanel —— 子代理独立展开面板
 *
 * 在摘要卡片展开后原地渲染：思考 + 工具调用 + 消息流 + 审批控件。
 * 嵌套子 Agent 仍为摘要卡片（不在此面板内递归展开内层）。
 *
 * 审批控件数据源：subagent.pendingInterruptInfo.requests（批量审批），
 * 通过 confirm/reject 事件交由父组件调用 resume_subagent 接口。
 */
import { computed } from 'vue'
import AiReasoning from '../ai-elements/AiReasoning.vue'
import {
  getSubagentStatusTagType,
  getSubagentStatusText,
} from '../../utils/subagentStatus'

const props = defineProps({
  subagent: {
    type: Object,
    required: true,
  },
  /** 子代理正文（父组件按 subagent_thread_id 聚合后传入） */
  content: {
    type: String,
    default: '',
  },
  /** 子代理中间思考 */
  reasoningContent: {
    type: String,
    default: '',
  },
  /** 子代理工具调用列表（父组件按 subagent_thread_id 聚合后传入） */
  toolCalls: {
    type: Array,
    default: () => [],
  },
})

defineOptions({ name: 'SubAgentDetailPanel' })

const emit = defineEmits(['confirm', 'reject'])

const statusText = computed(() => getSubagentStatusText(props.subagent.status))
const statusTagType = computed(() => getSubagentStatusTagType(props.subagent.status))

const interruptInfo = computed(() => {
  const info = props.subagent.pendingInterruptInfo
  return info && typeof info === 'object' ? info : null
})

const requests = computed(() => {
  const info = interruptInfo.value
  if (!info) return []
  const list = info.requests
  return Array.isArray(list) ? list : []
})

const interruptId = computed(() => (interruptInfo.value ? interruptInfo.value.interruptId || interruptInfo.value.interrupt_id || '' : ''))

const handleConfirm = (request) => emit('confirm', { threadId: props.subagent.threadId, interruptId: interruptId.value, request })
const handleReject = (request) => emit('reject', { threadId: props.subagent.threadId, interruptId: interruptId.value, request })

const requestTitle = (request) => request.tool_name || request.toolName || request.tool_call_id || request.toolCallId || '工具'
</script>

<template>
  <div class="subagent-detail">
    <div class="subagent-detail__header">
      <span class="subagent-detail__name">{{ subagent.agentName || subagent.name || '子代理' }}</span>
      <el-tag size="small" :type="statusTagType" effect="plain">{{ statusText }}</el-tag>
    </div>

    <!-- 审批控件：等待你的确认时渲染在展开面板内部 -->
    <div v-if="requests.length > 0" class="subagent-detail__approvals">
      <div class="subagent-detail__approval-title">待确认操作</div>
      <div v-for="(request, index) in requests" :key="request.tool_call_id || request.toolCallId || index" class="subagent-detail__approval">
        <div class="subagent-detail__approval-name">{{ requestTitle(request) }}</div>
        <div v-if="request.reason" class="subagent-detail__approval-reason">{{ request.reason }}</div>
        <div class="subagent-detail__approval-actions">
          <el-button size="small" type="primary" @click.stop="handleConfirm(request)">确认执行</el-button>
          <el-button size="small" @click.stop="handleReject(request)">拒绝</el-button>
        </div>
      </div>
    </div>

    <!-- 思考 -->
    <AiReasoning v-if="reasoningContent" :content="reasoningContent" :is-streaming="false" source="deep_thinking" />

    <!-- 正文 -->
    <div v-if="content" class="subagent-detail__content">{{ content }}</div>

    <!-- 工具调用（由父组件注入渲染；此处仅做计数占位，避免与既有 ToolCallCard 耦合） -->
    <div v-if="toolCalls.length > 0" class="subagent-detail__tools">
      <slot name="tools" :tool-calls="toolCalls" />
    </div>
  </div>
</template>

<style scoped>
.subagent-detail {
  width: 100%;
  padding: 8px 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.subagent-detail__header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.subagent-detail__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.subagent-detail__approvals {
  margin-bottom: 10px;
}

.subagent-detail__approval-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-color-warning);
  margin-bottom: 6px;
}

.subagent-detail__approval {
  border: 1px solid var(--el-color-warning-light-5);
  border-radius: 8px;
  padding: 8px;
  margin-bottom: 6px;
}

.subagent-detail__approval-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.subagent-detail__approval-reason {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin: 4px 0 8px;
}

.subagent-detail__approval-actions {
  display: flex;
  gap: 8px;
}

.subagent-detail__content {
  font-size: 13px;
  color: var(--el-text-color-regular);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  margin-bottom: 8px;
}
</style>
