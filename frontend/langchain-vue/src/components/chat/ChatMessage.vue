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
  },
  showDebug: {
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
        <AiReasoning
          v-if="message.reasoning && message.reasoning.content"
          :content="message.reasoning.content"
          :duration="message.reasoning.duration"
          :is-streaming="isStreaming && isLast"
        />
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
              :status="toolCall.status || (toolCall.state === 'output-error' ? 'failed' : toolCall.state === 'output-available' ? 'completed' : 'running')"
            />
          </AiQueue>
          <ToolCallCard
            v-else-if="message.toolCalls && message.toolCalls.length === 1"
            :tool-name="message.toolCalls[0].name"
            :input="message.toolCalls[0].input || message.toolCalls[0].parameters"
            :output="message.toolCalls[0].output || message.toolCalls[0].result"
            :status="message.toolCalls[0].status || (message.toolCalls[0].state === 'output-error' ? 'failed' : message.toolCalls[0].state === 'output-available' ? 'completed' : 'running')"
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

      <div v-if="showDebug && message.role === 'assistant'" class="debug-panel">
        <div class="debug-panel-header">
          <span class="debug-panel-title">🐛 调试信息</span>
        </div>
        <div class="debug-panel-body">
          <div class="debug-field">
            <span class="debug-label">消息 ID:</span>
            <span class="debug-value">{{ message.id || 'N/A' }}</span>
          </div>
          <div class="debug-field">
            <span class="debug-label">角色:</span>
            <span class="debug-value">{{ message.role }}</span>
          </div>
          <div class="debug-field">
            <span class="debug-label">时间:</span>
            <span class="debug-value">{{ message.timestamp || 'N/A' }}</span>
          </div>
          <div v-if="message.model" class="debug-field">
            <span class="debug-label">模型:</span>
            <span class="debug-value">{{ message.model }}</span>
          </div>
          <div v-if="message.tokenUsage" class="debug-field">
            <span class="debug-label">Token 用量:</span>
            <span class="debug-value">
              输入 {{ message.tokenUsage.promptTokens || 0 }} / 输出 {{ message.tokenUsage.completionTokens || 0 }} / 总计 {{ message.tokenUsage.totalTokens || 0 }}
            </span>
          </div>
          <div v-if="message.context" class="debug-field">
            <span class="debug-label">上下文:</span>
            <span class="debug-value">{{ message.context.usedTokens || 0 }} / {{ message.context.maxTokens || '128K' }} tokens ({{ ((message.context.percentage || 0) * 100).toFixed(1) }}%)</span>
          </div>
          <div v-if="message.toolCalls && message.toolCalls.length > 0" class="debug-field">
            <span class="debug-label">工具调用:</span>
            <span class="debug-value">{{ message.toolCalls.length }} 次调用</span>
          </div>
          <div v-if="message.sources && message.sources.length > 0" class="debug-field">
            <span class="debug-label">来源数:</span>
            <span class="debug-value">{{ message.sources.length }}</span>
          </div>
          <div v-if="message.latency" class="debug-field">
            <span class="debug-label">延迟:</span>
            <span class="debug-value">{{ message.latency }}ms</span>
          </div>
          <details v-if="message.rawResponse" class="debug-details">
            <summary class="debug-summary">原始响应</summary>
            <pre class="debug-json">{{ typeof message.rawResponse === 'string' ? message.rawResponse : JSON.stringify(message.rawResponse, null, 2) }}</pre>
          </details>
          <details class="debug-details">
            <summary class="debug-summary">完整消息数据</summary>
            <pre class="debug-json">{{ JSON.stringify(message, null, 2) }}</pre>
          </details>
        </div>
      </div>

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
  border-radius: var(--radius-lg);
  padding: 4px 0;
}

.message.user {
  flex-direction: row-reverse;
}

.message.assistant.has-metadata {
  cursor: pointer;
}

.message.assistant:hover {
  background-color: color-mix(in srgb, var(--foreground) 3%, transparent);
}

.message.is-selected {
  background-color: color-mix(in srgb, var(--sidebar-primary) 5%, transparent);
}

.message.is-streaming {
  animation: none;
}

.message-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: var(--gradient-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  flex-shrink: 0;
  margin: 0 12px;
  box-shadow: var(--shadow-sm);
}

.message.user .message-avatar {
  background: var(--gradient-secondary);
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
  transition: opacity var(--transition-fast);
}

.message:hover .message-actions {
  opacity: 1;
}

.message-actions .el-button.is-active {
  color: var(--sidebar-primary);
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
  border-radius: var(--radius);
  overflow: hidden;
  box-shadow: var(--shadow-sm);
  cursor: pointer;
  transition: transform var(--transition-normal), box-shadow var(--transition-normal);
}

.message-ai-image:hover {
  transform: scale(1.02);
  box-shadow: var(--shadow-md);
}

.message-role {
  font-size: 12px;
  color: var(--muted-foreground);
  padding: 0 4px;
  font-weight: 500;
}

.message-time {
  font-size: 11px;
  color: var(--muted-foreground);
  opacity: 0.7;
}

.user-text {
  background: color-mix(in srgb, var(--sidebar-primary) 12%, transparent);
  color: var(--foreground);
  border-radius: 18px 18px 4px 18px;
  padding: 10px 16px;
  max-width: 85%;
  word-break: break-word;
  border: 1px solid color-mix(in srgb, var(--sidebar-primary) 15%, transparent);
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
  background: color-mix(in srgb, var(--sidebar-primary) 8%, transparent);
  color: var(--sidebar-primary);
  font-size: 12px;
  cursor: pointer;
  transition: all var(--transition-fast);
  border: 1px solid color-mix(in srgb, var(--sidebar-primary) 15%, transparent);
}

.source-badge:hover {
  background: color-mix(in srgb, var(--sidebar-primary) 15%, transparent);
  box-shadow: var(--shadow-sm);
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
  transition: opacity var(--transition-fast);
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
  color: var(--muted-foreground);
}

.context-bar {
  height: 4px;
  border-radius: 2px;
  background: var(--accent);
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

  .message-bottom-controls {
    opacity: 1;
  }
}

.debug-panel {
  margin-top: 12px;
  border: 1px solid color-mix(in srgb, #f59e0b 30%, transparent);
  border-radius: 8px;
  background: color-mix(in srgb, #f59e0b 4%, var(--card));
  overflow: hidden;
}

.debug-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: color-mix(in srgb, #f59e0b 8%, transparent);
  border-bottom: 1px solid color-mix(in srgb, #f59e0b 15%, transparent);
}

.debug-panel-title {
  font-size: 12px;
  font-weight: 600;
  color: #f59e0b;
}

.debug-panel-body {
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.debug-field {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
}

.debug-label {
  color: var(--muted-foreground);
  white-space: nowrap;
  min-width: 70px;
}

.debug-value {
  color: var(--foreground);
  word-break: break-all;
}

.debug-details {
  margin-top: 4px;
}

.debug-summary {
  font-size: 12px;
  color: #f59e0b;
  cursor: pointer;
  padding: 4px 0;
  user-select: none;
}

.debug-summary:hover {
  text-decoration: underline;
}

.debug-json {
  margin: 4px 0 0;
  padding: 8px;
  background: var(--background);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 11px;
  font-family: var(--font-mono);
  max-height: 200px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--foreground);
}
</style>
