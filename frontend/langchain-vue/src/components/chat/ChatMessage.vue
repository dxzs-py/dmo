<script setup>
import { computed, ref } from 'vue'
import { User, ChatDotRound, Refresh, CopyDocument, Check, InfoFilled, Tools } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import MarkdownRenderer from '../common/MarkdownRenderer.vue'
import ChainOfThought from './ChainOfThought.vue'
import ToolCallCard from './ToolCallCard.vue'
import Sources from './Sources.vue'
import Plan from './Plan.vue'
import { AiReasoning, AiContext } from '../ai-elements'
import AiTask from '../ai-elements/AiTask.vue'
import AiImage from '../ai-elements/AiImage.vue'
import AiControls from '../ai-elements/AiControls.vue'
import AiQueue from '../ai-elements/AiQueue.vue'
import AiConfirmation from '../ai-elements/AiConfirmation.vue'
import AiConfirmationTitle from '../ai-elements/AiConfirmationTitle.vue'
import AiConfirmationRequest from '../ai-elements/AiConfirmationRequest.vue'
import AiConfirmationActions from '../ai-elements/AiConfirmationActions.vue'
import AiConfirmationAction from '../ai-elements/AiConfirmationAction.vue'
import AiConfirmationAccepted from '../ai-elements/AiConfirmationAccepted.vue'
import AiConfirmationRejected from '../ai-elements/AiConfirmationRejected.vue'
import MessageBranch from '../ai-elements/MessageBranch.vue'
import MessageBranchContent from '../ai-elements/MessageBranchContent.vue'
import MessageBranchSelector from '../ai-elements/MessageBranchSelector.vue'
import { useSessionStore } from '../../stores/session'
import { logger } from '../../utils/logger'

const props = defineProps({
  message: {
    type: Object,
    required: true,
    validator: (value) => {
      if (!value || typeof value !== 'object') return false
      const validRoles = ['user', 'assistant', 'system']
      if (!validRoles.includes(value.role)) {
        logger.warn(`ChatMessage: invalid role "${value.role}"`)
        return false
      }
      return true
    }
  },
  index: {
    type: Number,
    required: true,
    validator: (value) => value >= 0
  },
  isLast: {
    type: Boolean,
    default: false
  },
  isLoading: {
    type: Boolean,
    default: false
  },
  isStreaming: {
    type: Boolean,
    default: false
  },
  isSelected: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits({
  regenerate: (index) => typeof index === 'number' && index >= 0,
  click: (message) => message && typeof message === 'object',
  approve: (message) => message && typeof message === 'object',
  reject: (message) => message && typeof message === 'object'
})

const copied = ref(false)
const sessionStore = useSessionStore()

const hasMetadata = computed(() => {
  const m = props.message
  return (
    (m.sources && m.sources.length > 0) ||
    (m.toolCalls && m.toolCalls.length > 0) ||
    (m.reasoning && m.reasoning.content) ||
    m.plan ||
    m.chainOfThought
  )
})

const sourceCount = computed(() => props.message.sources?.length || 0)
const toolCallCount = computed(() => props.message.toolCalls?.length || 0)

const formattedTime = computed(() => {
  if (!props.message.timestamp) return ''
  const date = new Date(props.message.timestamp)
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
})

async function handleCopy() {
  try {
    await navigator.clipboard.writeText(props.message.content || '')
    copied.value = true
    ElMessage.success('已复制到剪贴板')
    setTimeout(() => { copied.value = false }, 2000)
  } catch {
    ElMessage.error('复制失败')
  }
}

function handleRegenerate() {
  emit('regenerate', props.index)
}

function handleBranchChange(versionIndex) {
  sessionStore.switchMessageVersion(sessionStore.currentSessionId, props.index, versionIndex)
}

function handleMessageClick() {
  if (props.message.role === 'assistant' && hasMetadata.value) {
    emit('click', props.message)
  }
}
</script>

<template>
  <div
    :class="[
      'message',
      message.role,
      {
        'is-selected': isSelected,
        'has-metadata': hasMetadata && message.role === 'assistant',
        'is-streaming': isStreaming && isLast && message.role === 'assistant'
      }
    ]"
    @click="handleMessageClick"
  >
    <div class="message-avatar">
      <el-icon v-if="message.role === 'user'" :size="18"><User /></el-icon>
      <el-icon v-else :size="18"><ChatDotRound /></el-icon>
    </div>
    <div class="message-content">
      <div class="message-header">
        <div class="message-role-row">
          <span class="message-role">
            {{ message.role === 'user' ? '用户' : 'AI助手' }}
          </span>
          <span v-if="formattedTime" class="message-time">{{ formattedTime }}</span>
        </div>
        <div v-if="message.role === 'assistant' && !isLoading" class="message-actions">
          <el-tooltip content="复制" placement="top" :show-after="500">
            <el-button
              link
              size="small"
              :icon="copied ? Check : CopyDocument"
              :class="{ 'copy-success': copied }"
              @click.stop="handleCopy"
            />
          </el-tooltip>
          <el-tooltip content="重新生成" placement="top" :show-after="500">
            <el-button
              link
              size="small"
              :icon="Refresh"
              @click.stop="handleRegenerate"
            />
          </el-tooltip>
          <el-tooltip v-if="hasMetadata" content="查看详情" placement="top" :show-after="500">
            <el-button
              link
              size="small"
              :icon="InfoFilled"
              :class="{ 'is-active': isSelected }"
              @click.stop="emit('click', message)"
            />
          </el-tooltip>
        </div>
      </div>

      <div v-if="message.role === 'user'" class="message-text user-text">
        <MarkdownRenderer :content="message.content" />
      </div>
      <div v-else class="message-text assistant-text">
        <MessageBranch
          v-if="message.versions && message.versions.length > 1"
          :default-branch="message.currentVersion || 0"
          :total-branches="message.versions.length"
          @branch-change="handleBranchChange"
        >
          <MessageBranchContent
            v-for="(version, vIdx) in message.versions"
            :key="vIdx"
            :index="vIdx"
          >
            <MarkdownRenderer :content="typeof version === 'string' ? version : (version?.content ?? '')" />
          </MessageBranchContent>
          <MessageBranchSelector from="assistant" />
        </MessageBranch>
        <MarkdownRenderer v-else :content="message.content" :citations="message.sources || []" />
      </div>

      <div v-if="message.images && message.images.length > 0" class="message-images">
        <AiImage
          v-for="(img, imgIdx) in message.images"
          :key="imgIdx"
          :base64="img.base64 || img.data"
          :uint8-array="img.uint8Array"
          :media-type="img.mediaType || img.mime_type || 'image/png'"
          :alt="img.alt || `AI生成图片 ${imgIdx + 1}`"
          class="message-ai-image"
        />
      </div>

      <div
        v-if="message.role === 'assistant' && sourceCount > 0"
        class="source-badges"
        @click.stop="emit('click', message)"
      >
        <div class="source-badge">
          <el-icon :size="12"><InfoFilled /></el-icon>
          <span>引用了 {{ sourceCount }} 个来源</span>
        </div>
      </div>

      <AiReasoning
        v-if="message.reasoning && message.reasoning.content"
        :content="message.reasoning.content"
        :duration="message.reasoning.duration"
        :is-streaming="isStreaming && isLast"
      />

      <Sources
        v-if="message.sources && message.sources.length > 0"
        :sources="message.sources"
        :is-streaming="isStreaming && isLast"
      />

      <Plan
        v-if="message.plan"
        :title="message.plan.title"
        :description="message.plan.description"
        :steps="message.plan.steps"
        :is-streaming="isStreaming && isLast"
      />

      <div v-if="message.chainOfThought" class="message-cot">
        <ChainOfThought :steps="message.chainOfThought" />
      </div>

      <div v-if="message.tasks && message.tasks.length > 0" class="message-tasks">
        <AiTask
          v-for="(task, idx) in message.tasks"
          :key="idx"
          :task="task"
          @click="emit('click', message)"
        />
      </div>

      <div v-if="message.toolCalls && message.toolCalls.length > 0" class="message-tool-calls">
        <div class="tool-calls-header">
          <span class="tool-calls-label">
            <el-icon :size="14"><Tools /></el-icon>
            工具调用 ({{ toolCallCount }})
          </span>
        </div>
        <TransitionGroup name="tool-call" tag="div" class="tool-calls-list">
          <AiQueue v-if="message.toolCalls && message.toolCalls.length > 1" :items="message.toolCalls" class="tool-calls-queue">
            <ToolCallCard
              v-for="(toolCall, idx) in message.toolCalls"
              :key="toolCall.id || idx"
              :tool-name="toolCall.name"
              :input="toolCall.input || toolCall.parameters"
              :output="toolCall.output || toolCall.result"
              :status="toolCall.status || 'completed'"
            />
          </AiQueue>
          <ToolCallCard
            v-else-if="message.toolCalls && message.toolCalls.length === 1"
            :tool-name="message.toolCalls[0].name"
            :input="message.toolCalls[0].input || message.toolCalls[0].parameters"
            :output="message.toolCalls[0].output || message.toolCalls[0].result"
            :status="message.toolCalls[0].status || 'completed'"
          />
        </TransitionGroup>
      </div>

      <AiConfirmation
        v-if="message.approval"
        :approval="message.approval"
        :state="message.approvalState || 'approval-requested'"
        class="message-confirmation"
      >
        <AiConfirmationRequest>
          <AiConfirmationTitle>{{ message.approval.title || '确认操作' }}</AiConfirmationTitle>
        </AiConfirmationRequest>
        <AiConfirmationActions>
          <AiConfirmationAction class="confirm-reject" @click="emit('reject', message)">
            拒绝
          </AiConfirmationAction>
          <AiConfirmationAction @click="emit('approve', message)">
            确认
          </AiConfirmationAction>
        </AiConfirmationActions>
        <AiConfirmationAccepted>
          <el-tag type="success" size="small">已确认</el-tag>
        </AiConfirmationAccepted>
        <AiConfirmationRejected>
          <el-tag type="danger" size="small">已拒绝</el-tag>
        </AiConfirmationRejected>
      </AiConfirmation>

      <AiContext
        v-if="message.context && (message.context.usedTokens || message.context.percentage)"
        class="message-context"
      >
        <div class="context-info">
          <span class="context-tokens">
            {{ message.context.usedTokens?.toLocaleString() || 0 }} / {{ message.context.maxTokens?.toLocaleString() || '128,000' }} tokens
          </span>
          <div v-if="message.context.percentage" class="context-bar">
            <div
              class="context-bar-fill"
              :style="{ width: Math.min(message.context.percentage * 100, 100) + '%' }"
              :class="{
                'bar-low': message.context.percentage < 0.5,
                'bar-medium': message.context.percentage >= 0.5 && message.context.percentage < 0.8,
                'bar-high': message.context.percentage >= 0.8
              }"
            ></div>
          </div>
        </div>
      </AiContext>

      <AiControls v-if="message.role === 'assistant' && !isLoading" class="message-bottom-controls">
        <el-button link size="small" :icon="copied ? Check : CopyDocument" @click.stop="handleCopy">
          {{ copied ? '已复制' : '复制' }}
        </el-button>
        <el-button link size="small" :icon="Refresh" @click.stop="handleRegenerate">
          重新生成
        </el-button>
      </AiControls>
    </div>
  </div>
</template>

<style scoped>
.message {
  display: flex;
  margin-bottom: 24px;
  max-width: 100%;
  transition: background-color 0.2s ease;
  border-radius: 12px;
  padding: 4px 0;
}

.message.user {
  flex-direction: row-reverse;
}

.message.assistant.has-metadata {
  cursor: pointer;
}

.message.assistant.has-metadata:hover {
  background-color: var(--el-fill-color-lighter);
}

.message.is-selected {
  background-color: var(--el-color-primary-light-9);
  outline: 1px solid var(--el-color-primary-light-7);
}

.message-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--el-color-primary) 0%, var(--el-color-primary-light-3) 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  flex-shrink: 0;
  margin: 0 12px;
}

.message.user .message-avatar {
  background: linear-gradient(135deg, #8b5cf6 0%, #a78bfa 100%);
}

.message-content {
  flex: 1;
  min-width: 0;
}

.message.user .message-content {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.message-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
  gap: 8px;
}

.message-role-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.message-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.2s;
}

.message:hover .message-actions {
  opacity: 1;
}

.message-actions .el-button.is-active {
  color: var(--el-color-primary);
}

.copy-success {
  color: var(--el-color-success) !important;
}

.message-images {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.message-ai-image {
  max-width: 300px;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  cursor: pointer;
  transition: transform 0.2s, box-shadow 0.2s;
}

.message-ai-image:hover {
  transform: scale(1.02);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12);
}

.message-role {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  padding: 0 4px;
  font-weight: 500;
}

.message-time {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}

.user-text {
  background: var(--el-fill-color-light);
  color: var(--el-text-color-primary);
  border-radius: 16px 16px 4px 16px;
  padding: 10px 16px;
  max-width: 85%;
  word-break: break-word;
}

.assistant-text {
  background: transparent;
  padding: 4px 0;
  max-width: 100%;
  word-break: break-word;
}

.source-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.source-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 12px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
  border: 1px solid var(--el-color-primary-light-8);
}

.source-badge:hover {
  background: var(--el-color-primary-light-8);
}

.message-cot,
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
  color: var(--el-text-color-secondary);
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

.message-confirmation {
  margin-top: 12px;
}

.confirm-reject {
  background-color: var(--el-color-danger) !important;
}

.tool-call-enter-active {
  transition: all 0.3s ease;
}

.tool-call-enter-from {
  opacity: 0;
  transform: translateY(-8px);
}

.message-context {
  margin-top: 12px;
}

.message-bottom-controls {
  margin-top: 8px;
  opacity: 0;
  transition: opacity 0.2s;
}

.message:hover .message-bottom-controls {
  opacity: 1;
}

.context-info {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.context-tokens {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.context-bar {
  height: 4px;
  border-radius: 2px;
  background: var(--el-fill-color);
  overflow: hidden;
}

.context-bar-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.3s ease;
}

.bar-low {
  background: var(--el-color-success);
}

.bar-medium {
  background: var(--el-color-warning);
}

.bar-high {
  background: var(--el-color-danger);
}

@media (max-width: 768px) {
  .message-avatar {
    width: 30px;
    height: 30px;
    margin: 0 8px;
  }

  .message-actions {
    opacity: 1;
  }

  .user-text {
    max-width: 92%;
    padding: 8px 12px;
  }
}
</style>
