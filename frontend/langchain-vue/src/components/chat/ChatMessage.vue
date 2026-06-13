<script setup>
import { computed, ref } from 'vue'
import { User, ChatDotRound, Refresh, CopyDocument, Check, InfoFilled, Tools, Search, ArrowRight, Loading, Delete, Document } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import MarkdownRenderer from '../common/MarkdownRenderer.vue'
import ChainOfThought from './ChainOfThought.vue'
import ToolCallCard from './ToolCallCard.vue'
import Sources from './Sources.vue'
import Plan from './Plan.vue'
import { AiReasoning } from '../ai-elements'
import AiTask from '../ai-elements/AiTask.vue'
import AiImage from '../ai-elements/AiImage.vue'
import AiControls from '../ai-elements/AiControls.vue'
import AiQueue from '../ai-elements/AiQueue.vue'
import AiConfirmation from '../ai-elements/AiConfirmation.vue'
import { useSessionStore } from '../../stores/session'
import { useChatStore } from '../../stores/chat'
import { useApprovalStore } from '../../stores/approval'
import { ToolCallStatus, ApprovalState } from '../../types'
import { logger } from '../../utils/logger'
import { formatFileSize } from '../../utils/format'

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
  approve: (payload) => payload && typeof payload === 'object',
  reject: (payload) => payload && typeof payload === 'object',
  delete: (payload) => payload && typeof payload.messageId !== 'undefined',
  'continue-research': (taskId) => typeof taskId === 'string' && taskId.length > 0,
})

const copied = ref(false)
const approvalInputValues = ref({})  // Map<toolCallId, inputValue> 每个工具调用独立的输入值
const sessionStore = useSessionStore()
const chatStore = useChatStore()
const approvalStore = useApprovalStore()
const router = useRouter()

// 是否有待审批的操作（审批等待中不应显示上下文统计）
const hasPendingApprovals = computed(() => approvalStore.pendingApprovals.size > 0)

// 收集所有工具调用级的审批数据
const toolCallApprovals = computed(() => {
  if (!props.message.toolCalls || !Array.isArray(props.message.toolCalls)) return []
  return props.message.toolCalls
    .filter(tc => tc.approval)
    .map(tc => ({
      toolCallId: tc.id,
      approval: tc.approval,
      status: tc.status,
    }))
})

// 是否使用消息级审批（向后兼容：当没有 toolCall 级审批时回退到 message.approval）
const useMessageLevelApproval = computed(() => {
  return toolCallApprovals.value.length === 0 && props.message.approval
})

// ToolCall 级审批确认：从 ToolCallCard 冒泡上来
function handleToolCallApprove(toolCall) {
  const approval = toolCall?.approval
  if (!approval) return
  // ToolCallCard 在 confirm_with_input 模式下会附加 _user_input 字段
  const userInput = toolCall._user_input
  if (userInput !== undefined) {
    emit('approve', { message: props.message, approval, user_input: userInput })
  } else if (approval.action === 'confirm_with_input') {
    emit('approve', { message: props.message, approval, user_input: approvalInputValues.value[toolCall.id] || '' })
  } else {
    emit('approve', { message: props.message, approval })
  }
}

// ToolCall 级审批拒绝：从 ToolCallCard 冒泡上来
function handleToolCallReject(toolCall) {
  const approval = toolCall?.approval
  if (!approval) return
  emit('reject', { message: props.message, approval })
}

// 通用审批确认处理：支持 CONFIRM 和 CONFIRM_WITH_INPUT 两种模式（消息级审批）
function handleApprove(toolCallId) {
  if (toolCallId) {
    // 工具调用级审批（旧逻辑，已被 handleToolCallApprove 替代，保留以防遗漏）
    const tc = props.message.toolCalls?.find(t => t.id === toolCallId)
    const approval = tc?.approval
    if (!approval) return
    if (approval.action === 'confirm_with_input') {
      emit('approve', { message: props.message, approval, user_input: approvalInputValues.value[toolCallId] || '' })
    } else {
      emit('approve', { message: props.message, approval })
    }
  } else if (useMessageLevelApproval.value) {
    // 向后兼容：消息级审批
    if (props.message.approval?.action === 'confirm_with_input') {
      emit('approve', { message: props.message, approval: props.message.approval, user_input: approvalInputValues.value['message'] || '' })
    } else {
      emit('approve', { message: props.message, approval: props.message.approval })
    }
  }
}

const attachmentProcessing = computed(() => chatStore.attachmentProcessing)
const showAttachmentProcessing = computed(() => {
  if (props.message.role !== 'assistant' || !props.isLast) return false
  if (!attachmentProcessing.value) return false
  if (attachmentProcessing.value.stage === 'complete') return false
  if (!props.message.content || props.message.content.trim() === '') return true
  return false
})
const showDeepResearchCard = computed(() => {
  return props.message.role === 'assistant' && !!props.message.research_task_id && !props.message.research_task_deleted
})

function navigateToDeepResearch() {
  const taskId = props.message.research_task_id
  if (taskId) {
    router.push({ path: '/deep-research', query: { task_id: taskId } })
  } else {
    router.push({ path: '/deep-research' })
  }
}

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

function getFileIcon(type) {
  const iconMap = {
    pdf: '📄', doc: '📝', docx: '📝', txt: '📃',
    png: '🖼️', jpg: '🖼️', jpeg: '🖼️', gif: '🖼️', webp: '🖼️', svg: '🖼️',
    xls: '📊', xlsx: '📊', csv: '📊',
    py: '🐍', js: '📜', ts: '📜', html: '🌐', css: '🎨',
    json: '📋', xml: '📋', md: '📝',
  }
  return iconMap[type] || '📎'
}

const attachments = computed(() => {
  if (props.message.attachments && props.message.attachments.length > 0) {
    return props.message.attachments.map(att => ({
      ...att,
      name: att.name || att.original_name,
      size: att.size || att.file_size,
      fileType: att.fileType || att.file_type,
    }))
  }
  return []
})

const researchContext = computed(() => {
  return props.message.researchContext || null
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

const hasResearchTask = computed(() => !!props.message.research_task_id && !props.message.research_task_deleted)

const showContinueResearch = computed(() => {
  return props.message.role === 'assistant' && hasResearchTask.value && !props.isStreaming
})

function handleContinueResearch() {
  if (props.message.research_task_id) {
    emit('continue-research', props.message.research_task_id)
  }
}

async function handleDelete() {
  let confirmMsg = '确定删除这条消息吗？'
  if (hasResearchTask.value) {
    confirmMsg += '该消息关联了深度研究任务，删除后研究任务仍可在深度研究模块查看。'
  }
  try {
    await ElMessageBox.confirm(confirmMsg, '删除确认', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    emit('delete', { messageId: props.message.id, researchTaskId: props.message.research_task_id })
  } catch {}
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
          <el-tooltip content="删除" placement="top" :show-after="500">
            <el-button
              link
              size="small"
              :icon="Delete"
              class="delete-btn"
              @click.stop="handleDelete"
            />
          </el-tooltip>
        </div>
        <div v-if="message.role === 'user'" class="message-actions">
          <el-tooltip content="删除" placement="top" :show-after="500">
            <el-button
              link
              size="small"
              :icon="Delete"
              class="delete-btn"
              @click.stop="handleDelete"
            />
          </el-tooltip>
        </div>
      </div>

      <div v-if="researchContext" class="attachment-list">
        <div class="attachment-item research-attachment-item">
          <span class="attachment-icon">🔬</span>
          <div class="attachment-info">
            <div class="attachment-name-row">
              <span class="attachment-name">深度研究</span>
              <el-icon class="attachment-status status-success" :size="14"><Check /></el-icon>
            </div>
            <span v-if="researchContext.query" class="attachment-size research-query">{{ researchContext.query }}</span>
          </div>
        </div>
      </div>

      <div v-if="attachments.length > 0" class="attachment-list">
        <div v-for="(att, idx) in attachments" :key="idx" class="attachment-item">
          <span class="attachment-icon">{{ getFileIcon(att.fileType) }}</span>
          <div class="attachment-info">
            <div class="attachment-name-row">
              <span class="attachment-name">{{ att.name }}</span>
            </div>
            <span v-if="att.size" class="attachment-size">{{ formatFileSize(att.size) }}</span>
          </div>
        </div>
      </div>

      <div v-if="message.role === 'user'" class="message-text user-text">
        <MarkdownRenderer :content="message.content" />
      </div>
      <div v-else class="message-text assistant-text">
        <div v-if="showAttachmentProcessing" class="attachment-processing-bar">
          <el-icon class="is-loading" :size="14"><Loading /></el-icon>
          <span class="attachment-processing-text">{{ attachmentProcessing.message || '正在处理文档...' }}</span>
        </div>
        <AiReasoning
          v-if="message.reasoning && message.reasoning.content"
          :content="message.reasoning.content"
          :duration="message.reasoning.duration"
          :is-streaming="isStreaming && isLast"
          :source="message.reasoning.source || 'deep_thinking'"
        />
        <div
          v-if="message.versions && message.versions.length > 1"
          class="message-branch"
        >
          <div
            v-for="(version, vIdx) in message.versions"
            :key="vIdx"
            class="message-branch-content"
            :class="{ 'is-active': vIdx === (message.currentVersion || 0) }"
          >
            <MarkdownRenderer :content="typeof version === 'string' ? version : (version?.content ?? '')" />
          </div>
          <div class="message-branch-selector">
            <span class="branch-label">版本</span>
            <button
              v-for="vIdx in message.versions.length"
              :key="vIdx"
              :class="['branch-btn', { active: (vIdx - 1) === (message.currentVersion || 0) }]"
              @click="handleBranchChange(vIdx - 1)"
            >
              {{ vIdx }}
            </button>
          </div>
        </div>
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

      <!-- 深度研究进度卡片 -->
      <div v-if="showDeepResearchCard" class="deep-research-card" @click.stop="navigateToDeepResearch">
        <div class="deep-research-card-icon">
          <el-icon :size="20"><Search /></el-icon>
        </div>
        <div class="deep-research-card-content">
          <div class="deep-research-card-title">深度研究任务已创建</div>
          <div class="deep-research-card-desc">
            <template v-if="isStreaming && isLast">
              <el-icon class="is-loading" :size="12"><Loading /></el-icon>
              <span>研究进行中，点击查看实时进度</span>
            </template>
            <template v-else>
              <span>研究已完成，点击查看详细报告和文件</span>
            </template>
          </div>
        </div>
        <div class="deep-research-card-arrow">
          <el-icon :size="14"><ArrowRight /></el-icon>
        </div>
      </div>

      <!-- 继续研究按钮 -->
      <div v-if="showContinueResearch" class="continue-research-btn" @click.stop="handleContinueResearch">
        <el-icon :size="14"><Search /></el-icon>
        <span>继续研究</span>
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
              :status="toolCall.status || (toolCall.state === 'output-error' ? ToolCallStatus.FAILED : toolCall.state === 'output-available' ? ToolCallStatus.COMPLETED : ToolCallStatus.RUNNING)"
              :tool-call="toolCall"
              @approve="(tc) => handleToolCallApprove(tc)"
              @reject="(tc) => handleToolCallReject(tc)"
            />
          </AiQueue>
          <ToolCallCard
            v-else-if="message.toolCalls && message.toolCalls.length === 1"
            :tool-name="message.toolCalls[0].name"
            :input="message.toolCalls[0].input || message.toolCalls[0].parameters"
            :output="message.toolCalls[0].output || message.toolCalls[0].result"
            :status="message.toolCalls[0].status || (message.toolCalls[0].state === 'output-error' ? ToolCallStatus.FAILED : message.toolCalls[0].state === 'output-available' ? ToolCallStatus.COMPLETED : ToolCallStatus.RUNNING)"
            :tool-call="message.toolCalls[0]"
            @approve="(tc) => handleToolCallApprove(tc)"
            @reject="(tc) => handleToolCallReject(tc)"
          />
        </TransitionGroup>
      </div>

      <!-- 向后兼容：消息级审批（当没有 toolCall 级审批时回退） -->
      <AiConfirmation
        v-if="useMessageLevelApproval"
        :approval="message.approval"
        :state="message.approvalState || 'pending'"
        class="message-confirmation"
        :class-name="'danger-' + (message.approval.danger_level || 'medium')"
        :extra-class="{ 'confirmation-collapsed': [ApprovalState.APPROVED, ApprovalState.REJECTED, ApprovalState.TIMEOUT].includes(message.approvalState) }"
      >
        <!-- 已确认/已拒绝/已超时：折叠显示，只保留一行状态标签 -->
        <template v-if="[ApprovalState.APPROVED, ApprovalState.REJECTED, ApprovalState.TIMEOUT].includes(message.approvalState)">
          <div class="confirmation-result-inline">
            <span class="confirmation-tool-badge" :class="'badge-' + (message.approval.danger_level || 'medium')">
              {{ message.approval.tool_name || '工具' }}
            </span>
            <el-tag v-if="message.approvalState === ApprovalState.APPROVED" type="success" size="small">已确认</el-tag>
            <el-tag v-else-if="message.approvalState === ApprovalState.REJECTED" type="danger" size="small">已拒绝</el-tag>
            <el-tag v-else-if="message.approvalState === ApprovalState.TIMEOUT" type="warning" size="small">已超时</el-tag>
          </div>
        </template>
        <!-- 待审批：完整展示 -->
        <template v-else>
          <div class="confirmation-request">
            <div class="confirmation-header">
              <span class="confirmation-tool-badge" :class="'badge-' + (message.approval.danger_level || 'medium')">
                {{ message.approval.tool_name || '工具' }}
              </span>
              <span class="confirmation-title">{{ message.approval.title || '确认操作' }}</span>
            </div>
            <div v-if="message.approval.command" class="confirmation-command">
              <code>{{ message.approval.command }}</code>
            </div>
            <div v-if="message.approval.description" class="confirmation-desc">
              {{ message.approval.description }}
            </div>
          </div>
          <!-- CONFIRM_WITH_INPUT 模式：显示输入框 -->
          <div v-if="message.approval.action === 'confirm_with_input'" class="confirmation-input-area">
            <el-input
              v-model="approvalInputValues['message']"
              :placeholder="message.approval.input_placeholder || '请输入值...'"
              size="small"
              clearable
              @keyup.enter="handleApprove()"
            />
          </div>
          <div class="confirmation-actions">
            <button class="confirmation-action confirm-reject" @click="emit('reject', { message, approval: message.approval })">
              拒绝
            </button>
            <button
              class="confirmation-action confirm-approve"
              :class="'approve-' + (message.approval.danger_level || 'medium')"
              @click="handleApprove()"
            >
              {{ message.approval.action === 'confirm_with_input' ? '确认并提交' : '确认执行' }}
            </button>
          </div>
        </template>
      </AiConfirmation>



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
        <el-button link size="small" :icon="Delete" class="delete-btn" @click.stop="handleDelete">
          删除
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

.message.assistant .message-content {
  padding: 0 20px;
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

/* 触摸设备（无悬停能力）始终显示操作按钮 */
@media (hover: none) {
  .message-actions {
    opacity: 1;
  }
}

.message-actions .el-button.is-active {
  color: var(--sidebar-primary);
}

.copy-success {
  color: var(--el-color-success) !important;
}

.delete-btn {
  color: var(--muted-foreground) !important;
}

.delete-btn:hover {
  color: var(--el-color-danger) !important;
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
  background: color-mix(in srgb, var(--sidebar-primary) 10%, transparent);
  color: var(--foreground);
  border-radius: 18px 18px 4px 18px;
  padding: 10px 16px;
  max-width: 85%;
  word-break: break-word;
}

.attachment-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}

.attachment-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 6px 10px;
  font-size: 13px;
  color: var(--foreground);
  transition: all var(--transition-fast);
  max-width: 200px;
}

.attachment-item:hover {
  border-color: var(--sidebar-primary);
  box-shadow: var(--shadow-sm);
}

.attachment-icon {
  font-size: 16px;
  flex-shrink: 0;
}

.attachment-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.attachment-name-row {
  display: flex;
  align-items: center;
  gap: 4px;
}

.attachment-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--foreground);
  font-size: 12px;
  line-height: 1.3;
  max-width: 120px;
}

.attachment-size {
  color: var(--muted-foreground);
  font-size: 11px;
  flex-shrink: 0;
}

.research-attachment-item {
  max-width: 280px;
}

.research-query {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 200px;
}

.attachment-status {
  flex-shrink: 0;
  line-height: 1;
}

.status-success {
  color: var(--el-color-success, #67c23a);
}

.assistant-text {
  background: transparent;
  padding: 4px 0;
  max-width: 100%;
  word-break: break-word;
}

.attachment-processing-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  margin-bottom: 8px;
  border-radius: 8px;
  background: linear-gradient(135deg, color-mix(in srgb, var(--sidebar-primary) 12%, transparent), color-mix(in srgb, var(--sidebar-primary) 6%, transparent));
  border: 1px solid color-mix(in srgb, var(--sidebar-primary) 25%, transparent);
  animation: processing-pulse 2s ease-in-out infinite;
}

@keyframes processing-pulse {
  0%, 100% { border-color: color-mix(in srgb, var(--sidebar-primary) 25%, transparent); }
  50% { border-color: color-mix(in srgb, var(--sidebar-primary) 50%, transparent); }
}

.attachment-processing-bar .is-loading {
  color: var(--sidebar-primary);
  animation: rotating 1.5s linear infinite;
}

@keyframes rotating {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.attachment-processing-text {
  font-size: 13px;
  color: var(--sidebar-primary);
  font-weight: 500;
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

.deep-research-card {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  padding: 12px 0;
  border-radius: 10px;
  background: linear-gradient(135deg, color-mix(in srgb, var(--sidebar-primary) 8%, transparent), color-mix(in srgb, var(--sidebar-primary) 3%, transparent));
  border: 1px solid color-mix(in srgb, var(--sidebar-primary) 20%, transparent);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.deep-research-card:hover {
  background: linear-gradient(135deg, color-mix(in srgb, var(--sidebar-primary) 14%, transparent), color-mix(in srgb, var(--sidebar-primary) 6%, transparent));
  border-color: color-mix(in srgb, var(--sidebar-primary) 35%, transparent);
  box-shadow: var(--shadow-sm);
  transform: translateY(-1px);
}

.deep-research-card-icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--sidebar-primary) 15%, transparent);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--sidebar-primary);
  flex-shrink: 0;
}

.deep-research-card-content {
  flex: 1;
  min-width: 0;
}

.deep-research-card-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--foreground);
}

.deep-research-card-desc {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted-foreground);
  margin-top: 2px;
}

.deep-research-card-desc .is-loading {
  color: var(--sidebar-primary);
}

.deep-research-card-arrow {
  color: var(--muted-foreground);
  flex-shrink: 0;
  transition: transform var(--transition-fast);
}

.deep-research-card:hover .deep-research-card-arrow {
  color: var(--sidebar-primary);
  transform: translateX(2px);
}

.continue-research-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  padding: 6px 14px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--sidebar-primary) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--sidebar-primary) 20%, transparent);
  color: var(--sidebar-primary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--transition-fast);
  user-select: none;
}

.continue-research-btn:hover {
  background: color-mix(in srgb, var(--sidebar-primary) 15%, transparent);
  border-color: color-mix(in srgb, var(--sidebar-primary) 35%, transparent);
  box-shadow: var(--shadow-sm);
  transform: translateY(-1px);
}

.continue-research-btn:active {
  transform: translateY(0);
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

/* 已确认/已拒绝的审批面板折叠为紧凑一行 */
.message-confirmation.confirmation-collapsed {
  padding: 4px 10px;
  gap: 0;
  border-color: var(--border);
  background-color: transparent;
}

.confirmation-result-inline {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.confirm-reject {
  background-color: var(--el-color-danger) !important;
}

.message-branch {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.message-branch-content {
  display: none;
}

.message-branch-content.is-active {
  display: block;
}

.message-branch-selector {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 4px;
}

.branch-label {
  font-size: 12px;
  color: var(--muted-foreground);
  margin-right: 4px;
}

.branch-btn {
  width: 24px;
  height: 24px;
  border-radius: 4px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--muted-foreground);
  font-size: 12px;
  cursor: pointer;
  transition: all var(--transition-fast);
  display: flex;
  align-items: center;
  justify-content: center;
}

.branch-btn:hover {
  border-color: var(--sidebar-primary);
  color: var(--sidebar-primary);
}

.branch-btn.active {
  background: var(--sidebar-primary);
  border-color: var(--sidebar-primary);
  color: white;
}

.confirmation-request {
  padding: 8px 0;
}

.confirmation-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.confirmation-tool-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.confirmation-tool-badge.badge-low {
  background: var(--el-color-success-light-8);
  color: var(--el-color-success-dark-2);
}

.confirmation-tool-badge.badge-medium {
  background: var(--el-color-warning-light-8);
  color: var(--el-color-warning-dark-2);
}

.confirmation-tool-badge.badge-high {
  background: var(--el-color-danger-light-8);
  color: var(--el-color-danger-dark-2);
}

/* danger_level 边框颜色区分 */
.danger-low .ai-confirmation {
  border-color: var(--el-color-success-light-5);
}

.danger-medium .ai-confirmation {
  border-color: var(--el-color-warning-light-5);
}

.danger-high .ai-confirmation {
  border-color: var(--el-color-danger-light-5);
}

.confirmation-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--foreground);
}

.confirmation-command {
  margin-top: 8px;
  padding: 8px 12px;
  background: var(--background);
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow-x: auto;
}

.confirmation-command code {
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--foreground);
  white-space: pre-wrap;
  word-break: break-all;
}

.confirmation-desc {
  margin-top: 6px;
  font-size: 13px;
  color: var(--muted-foreground);
  line-height: 1.5;
}

.confirmation-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.confirmation-action {
  padding: 6px 16px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--foreground);
  font-size: 13px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.confirmation-action:hover {
  opacity: 0.8;
}

.confirmation-action.confirm-approve {
  background: var(--el-color-primary);
  border-color: var(--el-color-primary);
  color: white;
}

.confirmation-action.confirm-approve.approve-low {
  background: var(--el-color-success);
  border-color: var(--el-color-success);
}

.confirmation-action.confirm-approve.approve-medium {
  background: var(--el-color-warning);
  border-color: var(--el-color-warning);
  color: var(--el-color-warning-dark-2);
}

.confirmation-action.confirm-approve.approve-high {
  background: var(--el-color-danger);
  border-color: var(--el-color-danger);
}

.confirmation-input-area {
  margin-top: 8px;
}

.confirmation-action.confirm-reject {
  background: var(--el-color-danger);
  border-color: var(--el-color-danger);
  color: white;
}

.confirmation-result {
  margin-top: 8px;
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
