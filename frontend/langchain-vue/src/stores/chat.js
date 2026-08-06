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
import { ElMessage, ElNotification } from 'element-plus'
import { nanoid } from 'nanoid'
import { generateId } from '../utils/id'
import { ChatRequestSchema, validateSchema } from '../utils/validation'
import { logger } from '../utils/logger'
import { transformFrontendMessageToBackend } from '../utils/sessionTransformers'
import { getInterruptId } from '../utils/messageOperations'

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
  const currentMode = ref('agent')
  const availableModes = ref({
    'agent': '代理',
    'deep-research': '深度研究',
  })
  const lastStreamError = ref(null)
  const messageCount = ref(0)
  const attachmentProcessing = ref(null)
  const approvalStore = useApprovalStore()

  const {
    isStreaming,
    abortController,
    abort: stopStreaming,
    streamChat,
    connectionStatus,
    lastError,
    retryCount,
    bytesReceived,
  } = useStreamChat()

  const isConnected = computed(() => connectionStatus.value === CONNECTION_STATUS.CONNECTED)
  const isReconnecting = computed(() => connectionStatus.value === CONNECTION_STATUS.RECONNECTING)
  const isConnecting = computed(() => connectionStatus.value === CONNECTION_STATUS.CONNECTING)

  const sendMessage = async (message, options = {}) => {
    logger.log('[ChatStore] sendMessage called')

    // Task 6.1：入口幂等守卫——置于所有 await 之前。
    // 此前 isLoading 在 createNewSession（await）之后才置 true，await 窗口内
    // 并发调用可同时进入并创建双份 user/assistant 占位消息。
    if (isLoading.value) {
      logger.warn('[ChatStore] sendMessage ignored: 已有消息正在发送中 (isLoading)')
      return
    }

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
          researchTaskId: currentResearchTaskId || null,
          continueTaskId: continueTaskId,
        },
        {
          appendToLastMessage: (content) => sessionStore.appendToLastMessage(sessionId, content),
          addSource: (data) => sessionStore.addSourceToLastMessage(sessionId, data),
          setSources: (data) => sessionStore.setSourcesToLastMessage(sessionId, data),
          setPlan: (data) => sessionStore.setPlanToLastMessage(sessionId, data),
          setChainOfThought: (data) => sessionStore.setChainOfThoughtToLastMessage(sessionId, data),
          addToolCall: (data) => sessionStore.addToolCallToLastMessage(sessionId, data),
          addOrUpdateToolCall: (data) => sessionStore.addOrUpdateToolCallToLastMessage(sessionId, data),
          updateOrAddToolResult: (data) => sessionStore.updateOrAddToolResultToLastMessage(sessionId, data),
          setReasoning: (data) => sessionStore.setReasoningToLastMessage(sessionId, data),
          setSuggestions: (data) => sessionStore.setSuggestionsToLastMessage(sessionId, data),
          setDeepResearchTask: (data) => {
            // 状态迁移至 chatDeepResearch 桥接层（Task 9），
            // 内部会同步写入 deepResearchTask 与 researchTaskId
            chatDeepResearch.setChatDeepResearchTask(data)
            // 深度研究任务创建时立即设置 researchTaskId，确保后续审批能正确路由到研究审批 API
            if (data?.taskId) {
              sessionStore.setResearchTaskIdToLastMessage(sessionId, data.taskId)
            }
          },
          setResearchTaskId: (taskId) => {
            chatDeepResearch.setChatResearchTaskId(taskId)
            sessionStore.setResearchTaskIdToLastMessage(sessionId, taskId)
          },
          setContext: (data) => sessionStore.setContextToLastMessage(sessionId, data),
          setUsage: (data) => sessionStore.setUsageToLastMessage(sessionId, data),
          setAttachmentIds: (ids) => sessionStore.setAttachmentIdsToLastUserMessage(sessionId, ids),
          setAttachmentProcessing: (data) => { attachmentProcessing.value = data },
          setError: (errorMsg) => {
            lastStreamError.value = errorMsg
            logger.error('[ChatStore] 流式错误:', errorMsg)
          },
          setModelFallback: (data) => {
            if (data?.message) {
              ElNotification({
                title: '模型降级提示',
                message: data.message,
                type: 'warning',
                duration: 8000,
              })
            }
            // 同步更新 modelStore 为实际使用的模型
            if (data?.actualProvider && data?.actualModel) {
              const mStore = useModelStore()
              if (mStore.currentProviderId !== data.actualProvider || mStore.currentModelName !== data.actualModel) {
                mStore.currentProviderId = data.actualProvider
                mStore.currentModelName = data.actualModel
              }
            }
          },
          setApproval: (data) => {
            // 保存审批数据时同时保存当前请求的工具配置，审批恢复时使用
            approvalStore.handleApprovalEvent(data, {
              source: 'chat',
              sessionId,
              taskId: data.taskId || null,
              baseApproval: {
                useTools: options.useTools !== false,
                useWebSearch: options.useWebSearch || false,
                useMcp: options.useMcp || false,
                selectedMcpServers: options.selectedMcpServers || null,
                selectedTools: options.selectedTools || null,
                useKnowledgeBase: options.useKnowledgeBase || false,
                selectedKnowledgeBases: sessionStore.selectedKnowledgeBases?.map(kb => kb.id) || [],
              },
            })
          },
          onRetry: (data) => {
            const backoffStr = data.backoff != null ? data.backoff.toFixed(1) : '?'
            const errorCode = data.errorCode || 'unknown'
            sessionStore.appendToLastMessage(
              sessionId,
              `\n\n> 正在重试 (${data.attempt}/${data.max})，${backoffStr}秒后重试... (${errorCode})\n`
            )
          },
          onTimeoutWarning: (data) => {
            sessionStore.appendToLastMessage(
              sessionId,
              `\n\n> ⚠️ 执行时间较长（已 ${data.elapsed}s / 阈值 ${data.limit}s），正在继续执行...\n`
            )
          },
          onApprovalTimeout: (data) => {
            // 深度研究审批超时：统一由 approval store 处理
            approvalStore.handleApprovalEvent(data, { source: 'chat', sessionId })
          },
          onApprovalProcessed: (data) => {
            // 审批已在另一端（深度研究模块）处理，统一由 approval store 处理
            approvalStore.handleApprovalEvent(data, { source: 'chat', sessionId })
          },
          onApprovalHistory: (parsed) => {
            // 历史审批补偿：SSE 重连时后端推送 Redis List 中的历史审批
            // parsed 结构：{ type: "approval_history", data: {...approval_data...}, taskId: "research_xxx" }
            const taskId = parsed.taskId || parsed.data?.taskId || chatDeepResearch.researchTaskId || null
            if (taskId && parsed.data) {
              approvalStore.restoreFromSSEHistory(parsed.data, taskId, sessionId)
            }
          },
          onToolUsageDedup: (data) => {
            if (data?.message) {
              ElMessage.info({ message: data.message, duration: 3000 })
            }
          },
          onToolUsageBlocked: (data) => {
            if (data?.message) {
              ElMessage.warning({ message: data.message, duration: 5000 })
            }
          },
          onToolUsageWarning: (data) => {
            if (data?.severity === 'warn' && data?.message) {
              ElMessage.warning({ message: data.message, duration: 4000 })
            }
          },
        }
      )

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
    // Task 6.1：发送类方法入口幂等守卫，防止并发重复生成双流
    if (isLoading.value) {
      logger.warn('[ChatStore] regenerateMessage ignored: 已有消息正在发送中 (isLoading)')
      return
    }

    const sessionStore = useSessionStore()
    const modelStore = useModelStore()
    const sid = sessionStore.currentSessionId
    const selectedKnowledgeBaseId = sessionStore.selectedKnowledgeBase?.id || null
    const messages = sessionStore.getSessionMessages(sid)
    // 惰性获取聊天深度研究桥接层（与 sendMessage 一致）：onApprovalHistory 等
    // SSE 回调需要读取 researchTaskId。Task 9 解耦后 regenerateMessage 不再直接
    // 访问 research store，统一经 chatDeepResearch 桥接层读取（修复原作用域未定义的 ReferenceError）
    const chatDeepResearch = await _getChatDeepResearch()

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
        },
        {
          appendToLastMessage: (content) => sessionStore.appendToMessage(sid, messageIndex, content),
          addSource: (data) => sessionStore.addSourceToMessage(sid, messageIndex, data),
          setSources: (data) => sessionStore.setSourcesToMessage(sid, messageIndex, data),
          setPlan: (data) => sessionStore.setPlanToMessage(sid, messageIndex, data),
          setChainOfThought: (data) => sessionStore.setChainOfThoughtToMessage(sid, messageIndex, data),
          addToolCall: (data) => sessionStore.addToolCallToMessage(sid, messageIndex, data),
          addOrUpdateToolCall: (data) => sessionStore.addOrUpdateToolCallToMessage(sid, messageIndex, data),
          updateOrAddToolResult: (data) => sessionStore.updateOrAddToolResultToMessage(sid, messageIndex, data),
          setReasoning: (data) => sessionStore.setReasoningToMessage(sid, messageIndex, data),
          setSuggestions: (data) => sessionStore.setSuggestionsToMessage(sid, messageIndex, data),
          setContext: (data) => sessionStore.setContextToMessage(sid, messageIndex, data),
          setUsage: (data) => {
            if (data.model !== undefined) currentMessage.model = data.model
            if (data.tokenCount !== undefined) currentMessage.tokenCount = data.tokenCount
            if (data.responseTime !== undefined) currentMessage.responseTime = data.responseTime
          },
          setError: (errorMsg) => {
            lastStreamError.value = errorMsg
            logger.error('[ChatStore] 重新生成流式错误:', errorMsg)
          },
          setApproval: (data) => approvalStore.handleApprovalEvent(data, {
            source: 'chat',
            sessionId: sid,
            taskId: data.taskId || null,
            baseApproval: {
              useTools: true,
              useWebSearch: false,
              useMcp: false,
              selectedMcpServers: null,
              selectedTools: null,
              useKnowledgeBase: !!selectedKnowledgeBaseId,
              selectedKnowledgeBases: sessionStore.selectedKnowledgeBases?.map(kb => kb.id) || [],
            },
          }),
          onApprovalTimeout: (data) => {
            approvalStore.handleApprovalEvent(data, { source: 'chat', sessionId: sid })
          },
          onApprovalProcessed: (data) => {
            approvalStore.handleApprovalEvent(data, { source: 'chat', sessionId: sid })
          },
          onApprovalHistory: (data) => {
            const taskId = data.taskId || chatDeepResearch.researchTaskId || null
            if (taskId) {
              approvalStore.restoreFromSSEHistory(data, taskId, sid)
            }
          },
          onRetry: (data) => {
            const backoffStr = data.backoff != null ? data.backoff.toFixed(1) : '?'
            const errorCode = data.errorCode || 'unknown'
            sessionStore.appendToMessage(sid, messageIndex, `\n\n> 正在重试 (${data.attempt}/${data.max})，${backoffStr}秒后重试... (${errorCode})\n`)
          },
          onTimeoutWarning: (data) => {
            sessionStore.appendToMessage(sid, messageIndex, `\n\n> ⚠️ 执行时间较长（已 ${data.elapsed}s / 阈值 ${data.limit}s），正在继续执行...\n`)
          },
          setModelFallback: (data) => {
            if (data?.message) {
              ElNotification({
                title: '模型降级提示',
                message: data.message,
                type: 'warning',
                duration: 8000,
              })
            }
            if (data?.actualProvider && data?.actualModel) {
              const mStore = useModelStore()
              if (mStore.currentProviderId !== data.actualProvider || mStore.currentModelName !== data.actualModel) {
                mStore.currentProviderId = data.actualProvider
                mStore.currentModelName = data.actualModel
              }
            }
          },
          onToolUsageDedup: (data) => {
            if (data?.message) {
              ElMessage.info({ message: data.message, duration: 3000 })
            }
          },
          onToolUsageBlocked: (data) => {
            if (data?.message) {
              ElMessage.warning({ message: data.message, duration: 5000 })
            }
          },
          onToolUsageWarning: (data) => {
            if (data?.severity === 'warn' && data?.message) {
              ElMessage.warning({ message: data.message, duration: 4000 })
            }
          },
        }
      )

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
    await chatAPI.deleteMessagePair(sessionId, backendId)
    sessionStore.removeMessagePairFromSession(sessionId, frontendId || backendId)
    ElMessage.success('消息已删除')
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

  return {
    isLoading,
    isStreaming,
    currentMode,
    availableModes,
    abortController,
    lastStreamError,
    messageCount,
    connectionStatus,
    lastError,
    retryCount,
    bytesReceived,
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
