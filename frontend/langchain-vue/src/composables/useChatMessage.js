/**
 * ChatMessage 组件的核心逻辑组合式函数。
 *
 * 将消息渲染所需的计算属性与事件处理函数从 ChatMessage.vue 中抽离，
 * 使组件模板聚焦于结构编排，逻辑可独立测试与复用。
 *
 * @param {Object} props - ChatMessage 组件 props（reactive）
 * @param {Object} props.message - 消息对象
 * @param {number} props.index - 消息在列表中的索引
 * @param {boolean} props.isLast - 是否为最后一条消息
 * @param {boolean} props.isLoading - 是否处于加载态
 * @param {boolean} props.isStreaming - 是否处于流式输出态
 * @param {boolean} props.isSelected - 是否被选中（查看详情）
 * @param {boolean} props.showDebug - 是否显示调试面板
 * @param {(event: string, ...args: any[]) => void} emit - 组件 emit 函数
 * @returns {{
 *   copied: import('vue').Ref<boolean>,
 *   hasPendingApprovals: import('vue').ComputedRef<boolean>,
 *   toolCallApprovals: import('vue').ComputedRef<Array>,
 *   attachmentProcessing: import('vue').ComputedRef<Object|null>,
 *   showAttachmentProcessing: import('vue').ComputedRef<boolean>,
 *   showDeepResearchCard: import('vue').ComputedRef<boolean>,
 *   hasMetadata: import('vue').ComputedRef<boolean>,
 *   sourceCount: import('vue').ComputedRef<number>,
 *   toolCallCount: import('vue').ComputedRef<number>,
 *   formattedTime: import('vue').ComputedRef<string>,
 *   attachments: import('vue').ComputedRef<Array>,
 *   researchContext: import('vue').ComputedRef<Object|null>,
 *   hasResearchTask: import('vue').ComputedRef<boolean>,
 *   showContinueResearch: import('vue').ComputedRef<boolean>,
 *   handleToolCallApprove: (toolCall: Object) => void,
 *   handleToolCallReject: (toolCall: Object) => void,
 *   navigateToDeepResearch: () => void,
 *   getFileIcon: (type: string) => string,
 *   handleCopy: () => Promise<void>,
 *   handleRegenerate: () => void,
 *   handleContinueResearch: () => void,
 *   handleDelete: () => Promise<void>,
 *   handleBranchChange: (versionIndex: number) => void,
 *   handleMessageClick: () => void,
 * }}
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useSessionStore } from '../stores/session'
import { useChatStore } from '../stores/chat'
import { useApprovalStore } from '../stores/approval'

export function useChatMessage(props, emit) {
  const copied = ref(false)
  const sessionStore = useSessionStore()
  const chatStore = useChatStore()
  const approvalStore = useApprovalStore()
  const router = useRouter()

  // ── 审批相关 ──
  const hasPendingApprovals = computed(() => approvalStore.pendingApprovals.size > 0)

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

  /** ToolCall 级审批确认：从 ToolCallCard 冒泡上来 */
  function handleToolCallApprove(toolCall) {
    const approval = toolCall?.approval
    if (!approval) return
    // ToolCallCard 在 confirm_with_input 模式下会附加 _user_input 字段
    const userInput = toolCall._user_input
    if (userInput !== undefined) {
      emit('approve', { message: props.message, approval, user_input: userInput })
    } else {
      emit('approve', { message: props.message, approval })
    }
  }

  /** ToolCall 级审批拒绝：从 ToolCallCard 冒泡上来 */
  function handleToolCallReject(toolCall) {
    const approval = toolCall?.approval
    if (!approval) return
    emit('reject', { message: props.message, approval })
  }

  // ── 附件处理进度 ──
  const attachmentProcessing = computed(() => chatStore.attachmentProcessing)
  const showAttachmentProcessing = computed(() => {
    if (props.message.role !== 'assistant' || !props.isLast) return false
    if (!attachmentProcessing.value) return false
    if (attachmentProcessing.value.stage === 'complete') return false
    if (!props.message.content || props.message.content.trim() === '') return true
    return false
  })

  // ── 深度研究卡片 ──
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

  const hasResearchTask = computed(() => !!props.message.research_task_id && !props.message.research_task_deleted)

  const showContinueResearch = computed(() => {
    return props.message.role === 'assistant' && hasResearchTask.value && !props.isStreaming
  })

  function handleContinueResearch() {
    if (props.message.research_task_id) {
      emit('continue-research', props.message.research_task_id)
    }
  }

  // ── 元数据与统计 ──
  const hasMetadata = computed(() => {
    const m = props.message
    return (
      (m.sources && m.sources.length > 0) ||
      (m.toolCalls && m.toolCalls.length > 0) ||
      (m.reasoning && m.reasoning.content)
    )
  })

  const sourceCount = computed(() => props.message.sources?.length || 0)
  const toolCallCount = computed(() => props.message.toolCalls?.length || 0)

  const formattedTime = computed(() => {
    if (!props.message.timestamp) return ''
    const date = new Date(props.message.timestamp)
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  })

  // ── 附件 ──
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

  // ── 操作 ──
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
    } catch {
      // 用户取消删除操作
    }
  }

  function handleBranchChange(versionIndex) {
    sessionStore.switchMessageVersion(sessionStore.currentSessionId, props.index, versionIndex)
  }

  function handleMessageClick() {
    if (props.message.role === 'assistant' && hasMetadata.value) {
      emit('click', props.message)
    }
  }

  return {
    copied,
    hasPendingApprovals,
    toolCallApprovals,
    attachmentProcessing,
    showAttachmentProcessing,
    showDeepResearchCard,
    hasMetadata,
    sourceCount,
    toolCallCount,
    formattedTime,
    attachments,
    researchContext,
    hasResearchTask,
    showContinueResearch,
    handleToolCallApprove,
    handleToolCallReject,
    navigateToDeepResearch,
    getFileIcon,
    handleCopy,
    handleRegenerate,
    handleContinueResearch,
    handleDelete,
    handleBranchChange,
    handleMessageClick,
  }
}
