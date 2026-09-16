import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { useStreamChat, CONNECTION_STATUS } from '../composables/useStreamChat'
import { useStreamFinalizer } from '../composables/useStreamFinalizer'
import { useSessionStore } from './session'
import { useModelStore } from './model'
import { useUserStore } from './user'
import { useApprovalStore } from './approval'
import { useSyncStore } from './sync'
import { chatAPI } from '@/api/chat'
import { ElMessage } from 'element-plus'
import { nanoid } from 'nanoid'
import { generateId } from '../utils/id'
import { ChatRequestSchema, validateSchema } from '../utils/validation'
import { logger } from '../utils/logger'
import { transformFrontendMessageToBackend } from '../utils/sessionTransformers'
import { getInterruptId } from '../utils/messageOperations'
import { resolveChatEnableSandbox } from '../utils/sandbox'
import { StreamState } from '../types'

/**
 * 聊天深度研究桥接层模块加载缓存（惰性动态加载）
 *
 * Task 9 / spec Change 5：chat.js 与 research.js 完全解耦，本文件不静态 import
 * research 与桥接层。仅在发送消息 / 审批等实际用到聊天深度研究状态时，经本 helper
 * 动态 import 桥接层 chatDeepResearch.js（模块 Promise 缓存，实例按当前 pinia 解析）。
 */
let _chatDeepResearchModulePromise = null
const _getChatDeepResearch = async () => {
  if (!_chatDeepResearchModulePromise) {
    _chatDeepResearchModulePromise = import('./chatDeepResearch')
  }
  const mod = await _chatDeepResearchModulePromise
  return mod.useChatDeepResearchStore()
}

export const useChatStore = defineStore('chat', () => {
  const isLoading = ref(false)
  // 链式删除进行中标记（Task 7.4）：删除期间置灰禁用发送/重新生成，
  // 避免删除与消息写入并发造成上下文错乱；删除完成（HTTP 返回或
  // messages_deleted 事件处理）后恢复。
  const isDeleting = ref(false)
  const currentMode = ref('agent')
  const availableModes = ref({
    'agent': '代理',
    'deep-research': '深度研究',
  })
  // 聊天深度研究模式沙箱任务级开关（默认关闭；仅 deep-research 模式随请求透传 enableSandbox）
  const deepResearchSandboxEnabled = ref(false)
  const lastStreamError = ref(null)
  const messageCount = ref(0)
  const attachmentProcessing = ref(null)
  const approvalStore = useApprovalStore()

  const {
    isStreaming,
    abortController,
    abort: abortStreamChat,
    streamChat,
    connectionStatus,
    lastError,
  } = useStreamChat()

  const isConnected = computed(() => connectionStatus.value === CONNECTION_STATUS.CONNECTED)
  const isReconnecting = computed(() => connectionStatus.value === CONNECTION_STATUS.RECONNECTING)
  const isConnecting = computed(() => connectionStatus.value === CONNECTION_STATUS.CONNECTING)

  // 当前流式类型（Task 9）：区分「普通发送」与「重新生成」，
  // 用户停止时据此标记 stopped（允许固化）/ incomplete（禁止固化）
  let _streamKind = null

  /**
   * 停止生成（Task 9）：三路径终止。
   * 1. 前端中止：abort waitForStreamEnd / AbortController，立即恢复 UI 交互；
   * 2. 后端停止：发布 SIGNAL_STOP 信令，终止 FastAPI 执行协程（优雅停止，
   *    保留 checkpoint 与已输出内容），而非仅断开前端 SSE；
   * 3. 状态标记：普通发送停止 → 消息标记 stopped（允许固化为主版本）；
   *    重生成中断 → 当前版本标记 incomplete（禁止固化为主版本）。
   */
  const stopStreaming = () => {
    abortStreamChat()
    const sessionStore = useSessionStore()
    const sessionId = sessionStore.currentSessionId

    // 后端停止信令（fire-and-forget，不阻塞 UI 恢复）
    if (sessionId) {
      chatAPI.stopStreaming(sessionId).catch(error => {
        logger.warn('[ChatStore] 后端停止请求失败（前端已中止，等待执行服务自停）:', error?.message || error)
      })
    }

    // 本地状态标记（触发浏览器即时反馈；非触发浏览器由后端 stream_completed(stopped) 广播同步）
    const messages = sessionStore.getSessionMessages(sessionId) || []
    const lastAssistant = [...messages].reverse().find(m => m.role === 'assistant')
    if (lastAssistant) {
      if (_streamKind === 'regenerate') {
        // 重生成中断：当前版本标记 incomplete，禁止固化为本轮主消息
        const currentVer = lastAssistant.versions?.[lastAssistant.currentVersion]
        if (currentVer) {
          currentVer.incomplete = true
          currentVer.streamState = StreamState.COMPLETED
          currentVer.isStreaming = false
        }
        lastAssistant.streamState = StreamState.COMPLETED
      } else {
        // 普通发送停止：保留已输出内容，标记 stopped（允许固化为主版本）
        lastAssistant.stopped = true
        lastAssistant.streamState = StreamState.COMPLETED
      }
      lastAssistant.isStreaming = false
    }
    _streamKind = null
  }

  const sendMessage = async (message, options = {}) => {
    logger.log('[ChatStore] sendMessage called')

    // Task 6.1：入口幂等守卫——置于所有 await 之前。
    // 此前 isLoading 在 createNewSession（await）之后才置 true，await 窗口内
    // 并发调用可同时进入并创建双份 user/assistant 占位消息。
    if (isLoading.value || isDeleting.value) {
      logger.warn('[ChatStore] sendMessage ignored: 已有消息正在发送中或删除进行中 (isLoading/isDeleting)')
      return
    }
    // Task 9：记录流式类型，用户停止时标记 stopped（普通发送）
    _streamKind = 'send'

    const sessionStore = useSessionStore()
    const modelStore = useModelStore()
    let sessionId = sessionStore.currentSessionId
    const selectedKnowledgeBaseId = sessionStore.selectedKnowledgeBase?.id || null
    
    logger.log('[ChatStore] sessionId:', sessionId)

    const validation = validateSchema(ChatRequestSchema, {
      message,
      mode: currentMode.value,
      useTools: options.useTools !== false,
      useWebSearch: options.useWebSearch || false,
      useKnowledgeBase: options.useKnowledgeBase || false,
      useDeepThinking: options.useDeepThinking || false,
      useMcp: options.useMcp || false,
      selectedMcpServers: options.selectedMcpServers || null,
      selectedTools: options.selectedTools || null,
      selectedKnowledgeBase: selectedKnowledgeBaseId,
      selectedKnowledgeBases: sessionStore.selectedKnowledgeBases?.map(kb => kb.id) || [],
      attachmentIds: options.attachmentIds || [],
    })

    if (!validation.success) {
      validation.errors.forEach(err => ElMessage.error(err.message))
      return
    }

    if (!sessionId) {
      logger.log('[ChatStore] Creating new session...')
      await sessionStore.createNewSession(currentMode.value)
      sessionId = sessionStore.currentSessionId
      if (!sessionId) {
        ElMessage.error('创建会话失败，请重试')
        isLoading.value = false
        return
      }
      if (selectedKnowledgeBaseId) {
        await sessionStore.setSelectedKnowledgeBase(selectedKnowledgeBaseId)
      }
      logger.log('[ChatStore] New sessionId:', sessionId)
    }

    isLoading.value = true
    lastStreamError.value = null
    // Task 6.2：发送新消息前固化末轮未固化多版本（版本固化触发条件①）。
    // 将当前选中版本固化为该轮主消息并持久化，后续 LLM 上下文以固化版本为准。
    // 新会话（无消息）或末轮无多版本时静默跳过；固化失败不阻塞发送（尽力而为，
    // LangGraph checkpoint 才是服务端上下文的权威来源）。
    try {
      await sessionStore.finalizeLatestMessage(sessionId)
    } catch (finalizeErr) {
      logger.warn('[ChatStore] 发送前版本固化失败（不阻塞发送）:', finalizeErr)
    }
    // 通知 syncStore 流式开始，跳过 WebSocket message_updated（避免 SSE 流式内容被快照覆盖）
    const syncStore = useSyncStore()
    syncStore.startStreaming(sessionId)
    // 惰性获取聊天深度研究桥接层（chat.js 不静态依赖 research / 桥接层，
    // 首次发送消息时动态加载并缓存，供研究分支回调与状态读写使用）
    const chatDeepResearch = await _getChatDeepResearch()
    chatDeepResearch.clearChatDeepResearchTask()
    attachmentProcessing.value = null
    const currentResearchTaskId = chatDeepResearch.researchTaskId
    // 不再清空 researchTaskId，深度研究审批依赖此值路由到正确的 API
    // 仅在 clearAll / 登出 / 会话删除时清空
    const currentResearchContextInfo = chatDeepResearch.researchContextInfo
    const continueTaskId = options.continueTaskId || null
    // 发送首条消息后清空研究上下文标签（保留 researchTaskId 供审批使用）
    if (currentResearchContextInfo) {
      chatDeepResearch.clearChatResearchContext()
    }

    const userMessage = {
      id: nanoid(),
      role: 'user',
      content: message,
      timestamp: new Date().toISOString(),
      attachmentIds: options.attachmentIds || [],
      attachments: options.attachments || [],
      researchContext: currentResearchContextInfo || null,
    }
    // 用户消息由后端 ChatStreamView 在流式开始时创建并广播，前端不再重复创建
    sessionStore.addMessageToSession(sessionId, userMessage, false)

    const assistantMessage = {
      id: nanoid(),
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
      sources: [],
      plan: null,
      chainOfThought: null,
      toolCalls: [],
      reasoning: null,
      suggestions: null,
      context: null,
    }
    // AI 消息由后端 SSE 流创建并广播，前端只创建本地占位
    sessionStore.addMessageToSession(sessionId, assistantMessage, false)
    messageCount.value += 1

    let streamSyncTimer = null
    try {
      // 流式输出期间每 5 秒同步 assistant 消息到后端（仅 PATCH 不 POST 创建）
      streamSyncTimer = setInterval(() => {
        sessionStore.syncLastMessageToBackend(sessionId, { allowCreate: false }).catch(() => {})
      }, 5000)

      const messages = sessionStore.getSessionMessages(sessionId) || []
      
      const chatHistory = messages
        .slice(0, -2)
        .map(m => {
          const item = {
            role: m.role || 'user',
            content: m.content || '',
          }
          if (m.role === 'user' && m.attachmentIds && m.attachmentIds.length > 0) {
            item.attachmentIds = m.attachmentIds
          }
          return item
        })
        .filter(m => m.content && m.content.trim())

      logger.log('[ChatStore] streamChat 请求参数, attachmentIds:', options.attachmentIds || [])
      const modelConfig = modelStore.getModelConfig()
      const specialParams = modelConfig.specialParams ? { ...modelConfig.specialParams } : null
      const result = await streamChat(
        {
          message,
          clientMessageId: generateId(), // Task 6.2：幂等键（可选字段，后端缺失时行为不变），fetchSSE 重试复用同一 id 保证后端只受理一次
          chatHistory: chatHistory,
          mode: currentMode.value,
          useTools: options.useTools !== false,
          useWebSearch: options.useWebSearch || false,
          useKnowledgeBase: options.useKnowledgeBase || false,
          useDeepThinking: modelStore.thinkingEnabled,
          useMcp: options.useMcp || false,
          selectedMcpServers: options.selectedMcpServers || null,
          selectedTools: options.selectedTools || null,
          streaming: true,
          sessionId: sessionId,
          selectedKnowledgeBase: selectedKnowledgeBaseId,
          selectedKnowledgeBases: sessionStore.selectedKnowledgeBases?.map(kb => kb.id) || [],
          attachmentIds: options.attachmentIds || [],
          providerId: modelConfig.providerId || null,
          modelName: modelConfig.modelName || null,
          specialParams: specialParams,
          temperature: modelConfig.temperature || null,
          maxTokens: modelConfig.maxTokens || null,
          // 仅深度研究模式或用户显式携带研究上下文时才发送 researchTaskId。
          // 根因修复：删除深度研究会话后残留的 researchTaskId 泄漏到代理模式请求，
          // 导致后端错误加载旧研究上下文注入 prompt（日志：代理请求带 research_task_id）。
          researchTaskId: (currentMode.value === 'deep-research' || currentResearchContextInfo)
            ? currentResearchTaskId
            : null,
          continueTaskId: continueTaskId,
          // 深研模式沙箱任务级开关（Spec 任务级开关；仅 deep-research 模式透传 enable_sandbox）
          enableSandbox: resolveChatEnableSandbox(currentMode.value, deepResearchSandboxEnabled.value),
        })

      if (!result.success && !result.aborted) {
        lastStreamError.value = result.error?.message || '消息发送失败'
        sessionStore.updateLastMessage(sessionId, '抱歉，消息发送失败，请稍后重试')
        ElMessage.error('发送消息失败，请稍后重试')
      }

      if (result.aborted) {
        syncStore.stopStreaming(sessionId)
        return
      }

      // 审批中断兜底已移除（Task 7 / Task 10）：
      // 审批挂起时 toolCall.status 由后端 tool_call_waiting 事件经 WebSocket 权威维护
      // （approval_service._publish_tool_call_waiting_event），审批面板显示由
      // approval.state 驱动（ToolCallCard.isWaiting），审批态与工具执行态解耦；
      // 消息级 approval 已由 approvalStore._handleNewApproval → setApprovalToLastMessage
      // 写入并 PATCH 持久化。原直接改 message.toolCalls 状态绕过了 toolCallMap
      // 唯一真相源与状态机（applyToolCallState），且 tool_calls 后端 read_only 不持久化，
      // 纯属冗余本地修复。

      const finalMessages = sessionStore.getSessionMessages(sessionId) || []
      if (finalMessages.length <= 2) {
        const lastMsg = finalMessages[finalMessages.length - 1]
        if (lastMsg?.content) {
          const title = lastMsg.content.slice(0, 30) + (lastMsg.content.length > 30 ? '...' : '')
          sessionStore.updateSessionTitle(sessionId, title)
        }
      }

      // 根因 B 修复：流式正常结束后统一最终化（chat.js 此前未接入 useStreamFinalizer，
      // STREAM_FINALIZED 从未广播 → 非触发浏览器 streamState 永久卡在 streaming）。
      // finalizeStream 内部：等待同步锁 → flush pending → 最终 PATCH →
      // 通知后端广播 stream_finalized（非触发浏览器据此触发权威全量同步并标记 COMPLETED）
      // → 本地消息标记 COMPLETED。
      // 审批中断（INTERRUPTED）/深度研究中断场景由 useStreamFinalizer 状态守卫自动跳过，
      // 不影响审批恢复流与深度研究完成流（两者均有各自最终化链路）。
      if (result.success && !result.aborted) {
        const lastMsg = (sessionStore.getSessionMessages(sessionId) || []).slice(-1)[0]
        if (lastMsg) {
          const { finalizeStream } = useStreamFinalizer()
          await finalizeStream(sessionId, lastMsg, { allowCreate: false })
        }
      }
    } finally {
      clearInterval(streamSyncTimer)
      sessionStore.clearToolSyncTimer(sessionId)
      isLoading.value = false
      syncStore.stopStreaming(sessionId)
      sessionStore.touchSessionUpdatedAt(sessionId)
      if (attachmentProcessing.value && attachmentProcessing.value.stage !== 'complete') {
        attachmentProcessing.value = null
      }
    }
  }

  const regenerateMessage = async (messageIndex) => {
    // Task 6.1：发送类方法入口幂等守卫，防止并发重复生成双流；
    // 链式删除进行中（Task 7.4）同样拒绝，避免删除与重新生成并发脏写
    if (isLoading.value || isDeleting.value) {
      logger.warn('[ChatStore] regenerateMessage ignored: 已有消息正在发送中或删除进行中 (isLoading/isDeleting)')
      return
    }
    // Task 9：记录流式类型，用户停止时标记 incomplete（重生成中断，禁止固化）
    _streamKind = 'regenerate'

    const sessionStore = useSessionStore()
    const modelStore = useModelStore()
    const sid = sessionStore.currentSessionId
    const selectedKnowledgeBaseId = sessionStore.selectedKnowledgeBase?.id || null
    const messages = sessionStore.getSessionMessages(sid)

    // Task 6.1：点击重新生成属于有效操作，重置版本固化计时器
    sessionStore.resetFinalizeTimer(sid)

    if (messageIndex < 1 || !messages?.length || messages.length < 2) return

    const userMessage = messages[messageIndex - 1]
    const assistantMessage = messages[messageIndex]
    if (userMessage?.role !== 'user' || assistantMessage?.role !== 'assistant') return

    const session = sessionStore.sessions.find(s => s.id === sid)
    if (!session || !session.messages[messageIndex]) return

    const currentMessage = session.messages[messageIndex]

    if (!currentMessage.versions) {
      currentMessage.versions = [
        {
          id: currentMessage.id,
          content: currentMessage.content,
          sources: currentMessage.sources || [],
          plan: currentMessage.plan || null,
          chainOfThought: currentMessage.chainOfThought || null,
          toolCalls: currentMessage.toolCalls || [],
          reasoning: currentMessage.reasoning || null,
          suggestions: currentMessage.suggestions || null,
          context: currentMessage.context || null,
        },
      ]
      currentMessage.currentVersion = 0
    }

    const newVersionId = nanoid()
    currentMessage.versions.push({
      id: newVersionId,
      content: '',
      sources: [],
      plan: null,
      chainOfThought: null,
      toolCalls: [],
      reasoning: null,
      suggestions: null,
      context: null,
      // 与 handleMessageRegenerated 的新版本结构对齐：标记 STREAMING + isStreaming，
      // 使触发浏览器收到自身 message_regenerated 事件时幂等检查（空 STREAMING 版本）命中，
      // 避免重复归档产生多余空版本。
      streamState: StreamState.STREAMING,
      isStreaming: true,
    })
    currentMessage.currentVersion = currentMessage.versions.length - 1

    currentMessage.content = ''
    currentMessage.sources = []
    currentMessage.plan = null
    currentMessage.chainOfThought = null
    currentMessage.toolCalls = []
    currentMessage.reasoning = null
    currentMessage.suggestions = null
    currentMessage.context = null
    currentMessage.streamState = StreamState.STREAMING
    currentMessage.isStreaming = true

    isLoading.value = true
    lastStreamError.value = null

    let streamSyncTimer = null
    try {
      // 流式输出期间每 5 秒同步消息到后端（仅 PATCH）
      streamSyncTimer = setInterval(() => {
        sessionStore.syncLastMessageToBackend(sid, { allowCreate: false }).catch(() => {})
      }, 5000)

      const chatHistory = messages
        .slice(0, messageIndex - 1)
        .map(m => ({
          role: m.role || 'user',
          content: m.content || '',
        }))
        .filter(m => m.content && m.content.trim())

      const modelConfig = modelStore.getModelConfig()
      let regenSpecialParams = modelConfig.specialParams ? { ...modelConfig.specialParams } : null
      const result = await streamChat(
        {
          message: userMessage.content || '',
          clientMessageId: generateId(), // Task 6.2：幂等键，防止重新生成时重复请求造成双流
          chatHistory: chatHistory,
          mode: currentMode.value,
          useTools: true,
          useWebSearch: false,
          useKnowledgeBase: !!selectedKnowledgeBaseId,
          useDeepThinking: modelStore.thinkingEnabled,
          useMcp: false,
          streaming: true,
          sessionId: sid,
          selectedKnowledgeBase: selectedKnowledgeBaseId,
          selectedKnowledgeBases: sessionStore.selectedKnowledgeBases?.map(kb => kb.id) || [],
          providerId: modelConfig.providerId || null,
          modelName: modelConfig.modelName || null,
          specialParams: regenSpecialParams,
          temperature: modelConfig.temperature || null,
          maxTokens: modelConfig.maxTokens || null,
          // 重新生成语义：复用已有消息对（user/assistant 消息 ID），后端不新建消息对
          regenerate: true,
          userMessageId: userMessage.backendId || userMessage.id,
          assistantMessageId: currentMessage.backendId || currentMessage.id,
        })

      if (!result.success && !result.aborted) {
        lastStreamError.value = result.error?.message || '重新生成失败'
        ElMessage.error('重新生成失败，请稍后重试')
      }

      if (result.success && currentMessage.backendId) {
        const backendMsg = transformFrontendMessageToBackend(currentMessage)
        chatAPI.updateMessage(currentMessage.backendId, backendMsg).catch(error => {
          logger.error('Failed to sync regenerated message to backend:', error)
        })
      }

      // 根因 B 修复：重新生成流结束后统一最终化（与 sendMessage 一致），
      // 广播 stream_finalized 让非触发浏览器同步完整结果并最终化。
      // 审批中断（INTERRUPTED）场景由 useStreamFinalizer 状态守卫自动跳过。
      if (result.success && !result.aborted) {
        const { finalizeStream } = useStreamFinalizer()
        await finalizeStream(sid, currentMessage, { messageIndex, allowCreate: false })
        // Task 6.3：流式结束后末轮存在未固化多版本，重新启动 5 分钟超时固化
        // 计时器（重新生成完成且用户无有效操作时超时固化当前选中版本）
        sessionStore.resetFinalizeTimer(sid)
      }
    } finally {
      clearInterval(streamSyncTimer)
      sessionStore.clearToolSyncTimer(sid)
      isLoading.value = false
      sessionStore.touchSessionUpdatedAt(sid)
    }
  }

  const fetchModes = async () => {
    try {
      const response = await chatAPI.getModes()
      const data = response.data
      if (data.code === 200 && data.data?.modes) {
        const backendModes = data.data.modes
        // 后端返回数组格式 [{id, label, ...}]，转换为 {id: label} 对象
        if (Array.isArray(backendModes)) {
          const modesMap = {}
          backendModes.forEach(m => { modesMap[m.id] = m.label })
          availableModes.value = modesMap
        } else {
          availableModes.value = backendModes
        }
      }
      if (data.data?.defaultMode) currentMode.value = data.data.defaultMode
    } catch (error) {
      logger.error('Failed to fetch modes:', error)
    }
  }

  const clearCurrentSession = () => {
    const sessionStore = useSessionStore()
    sessionStore.clearCurrentSessionMessages()
  }

  const clearError = () => {
    lastStreamError.value = null
  }

  const clearAll = () => {
    isLoading.value = false
    currentMode.value = 'agent'
    availableModes.value = { 'agent': '代理', 'deep-research': '深度研究' }
    lastStreamError.value = null
    messageCount.value = 0
    // 重置聊天深度研究桥接状态（惰性加载；clearAll / 登出场景）
    _getChatDeepResearch().then(bridge => bridge.resetChatDeepResearch()).catch(() => {})
    attachmentProcessing.value = null
    approvalStore.clearAll()
  }

  // 登出时清除聊天状态，防止跨用户数据泄露
  const userStore = useUserStore()
  watch(() => userStore.isLoggedIn, (newVal) => {
    if (!newVal) {
      clearAll()
    }
  })

  const deleteMessage = async (backendId, researchTaskId, frontendId) => {
    const sessionStore = useSessionStore()
    const sessionId = sessionStore.currentSessionId
    await chatAPI.deleteMessage(backendId)
    sessionStore.removeMessageFromSession(sessionId, frontendId || backendId)
    ElMessage.success('消息已删除')
  }

  const deleteMessagePair = async (sessionId, backendId, frontendId) => {
    const sessionStore = useSessionStore()
    // 删除期间置灰禁用发送/重新生成（Task 7.4），删除完成恢复
    isDeleting.value = true
    try {
      await chatAPI.deleteMessagePair(sessionId, backendId)
      sessionStore.removeMessagePairFromSession(sessionId, frontendId || backendId)
      ElMessage.success('消息已删除')
    } finally {
      isDeleting.value = false
    }
  }

  /**
   * 统一的审批执行函数（approveCommand / rejectCommand 的合并实现）
   * @param {Object} approval - 审批数据
   * @param {boolean} approved - true=确认, false=拒绝
   * @param {string|null} userInput - 用户输入（confirm_with_input 模式下）
   */
  const _executeApproval = async (approval, approved, userInput = null) => {
    if (!approval) return

    const toolCallId = getInterruptId(approval)
    const entry = approvalStore.pendingApprovals.get(toolCallId)

    // 深度研究审批：researchTaskId 恢复与取值统一委托桥接层
    // （内部实现原"从 deepResearchTask / 消息历史恢复"逻辑）
    const chatDeepResearch = await _getChatDeepResearch()
    const researchTaskId = chatDeepResearch.getResearchTaskIdForApproval(approval)

    await approvalStore.executeApproval(approval, approved, userInput, {
      taskId: entry?.taskId || researchTaskId || null,
    })
  }

  const approveCommand = (approval, userInput) => _executeApproval(approval, true, userInput)
  const rejectCommand = (approval) => _executeApproval(approval, false)

  /** 设置聊天深研模式沙箱任务级开关（仅 deep-research 模式随请求透传 enableSandbox） */
  const setDeepResearchSandboxEnabled = (val) => {
    deepResearchSandboxEnabled.value = val === true
  }

  return {
    isLoading,
    isDeleting,
    isStreaming,
    currentMode,
    availableModes,
    deepResearchSandboxEnabled,
    setDeepResearchSandboxEnabled,
    abortController,
    lastStreamError,
    messageCount,
    connectionStatus,
    lastError,
    isConnected,
    isReconnecting,
    isConnecting,
    sendMessage,
    regenerateMessage,
    fetchModes,
    clearCurrentSession,
    stopStreaming,
    clearError,
    attachmentProcessing,
    deleteMessage,
    deleteMessagePair,
    approveCommand,
    rejectCommand,
  }
})
