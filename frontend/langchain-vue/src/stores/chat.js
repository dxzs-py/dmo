import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { useStreamChat, CONNECTION_STATUS } from '../composables/useStreamChat'
import { useSessionStore } from './session'
import { useModelStore } from './model'
import { useUserStore } from './user'
import { useApprovalStore } from './approval'
import { useSyncStore } from './sync'
import { chatAPI } from '../api'
import { ElMessage, ElNotification } from 'element-plus'
import { nanoid } from 'nanoid'
import { ChatRequestSchema, validateSchema } from '../utils/validation'
import { logger } from '../utils/logger'
import { getModeLabel } from '../utils/format'
import { transformFrontendMessageToBackend } from '../utils/session-transformers'
import { getInterruptId } from '../utils/message-operations'
import { ToolCallStatus } from '../types'

export const useChatStore = defineStore('chat', () => {
  const isLoading = ref(false)
  const currentMode = ref('agent')
  const availableModes = ref({
    'agent': '代理',
    'deep-research': '深度研究',
  })
  const lastStreamError = ref(null)
  const messageCount = ref(0)
  const deepResearchTask = ref(null)
  const researchTaskId = ref(null)
  const researchContextInfo = ref(null)  // { taskId, query } 研究上下文标识，发送首条消息后清空
  const attachmentProcessing = ref(null)
  const approvalStore = useApprovalStore()

  /**
   * 从已加载的消息中恢复 researchTaskId 和 researchContextInfo
   * 页面刷新后运行时 ref 会丢失，需要从 assistant 消息的 researchTaskId 字段重建
   */
  const restoreResearchContextFromMessages = (sessionId) => {
    if (researchTaskId.value) return // 已有值则不覆盖
    const sessionStore = useSessionStore()
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session?.messages) return
    // 从后往前找最后一条带 researchTaskId 的 assistant 消息
    for (let i = session.messages.length - 1; i >= 0; i--) {
      const msg = session.messages[i]
      if (msg.role === 'assistant' && msg.researchTaskId) {
        researchTaskId.value = msg.researchTaskId
        researchContextInfo.value = { taskId: msg.researchTaskId, query: '' }
        logger.log('[ChatStore] 从消息历史恢复 researchTaskId:', msg.researchTaskId)
        return
      }
    }
  }

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
    
    const sessionStore = useSessionStore()
    const modelStore = useModelStore()
    let sessionId = sessionStore.currentSessionId
    const selectedKnowledgeBaseId = sessionStore.selectedKnowledgeBase?.id || null
    
    logger.log('[ChatStore] sessionId:', sessionId)

    const validation = validateSchema(ChatRequestSchema, {
      message,
      mode: currentMode.value,
      use_tools: options.useTools !== false,
      use_web_search: options.use_web_search || false,
      use_knowledge_base: options.use_knowledge_base || false,
      use_deep_thinking: options.use_deep_thinking || false,
      use_mcp: options.useMcp || false,
      selected_mcp_servers: options.selectedMcpServers || null,
      selected_tools: options.selectedTools || null,
      selected_knowledge_base: selectedKnowledgeBaseId,
      selected_knowledge_bases: sessionStore.selectedKnowledgeBases?.map(kb => kb.id) || [],
      attachment_ids: options.attachmentIds || [],
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
    // 通知 syncStore 流式开始，跳过 WebSocket 的 message_updated（避免 SSE 流式内容被快照覆盖）
    const syncStore = useSyncStore()
    syncStore.startStreaming(sessionId)
    deepResearchTask.value = null
    attachmentProcessing.value = null
    const currentResearchTaskId = researchTaskId.value
    // 不再清空 researchTaskId — 深度研究审批依赖此值路由到正确的 API
    // 仅在 clearAll / 登出 / 会话删除时清空
    const currentResearchContextInfo = researchContextInfo.value
    const continueTaskId = options.continue_task_id || null
    // 发送首条消息后清空研究上下文标签（保留 researchTaskId 供审批使用）
    if (currentResearchContextInfo) {
      researchContextInfo.value = null
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
    sessionStore.addMessageToSession(sessionId, userMessage)

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
    sessionStore.addMessageToSession(sessionId, assistantMessage, true)
    messageCount.value += 1

    let streamSyncTimer = null
    try {
      // 流式输出期间每 5 秒同步 assistant 消息到后端，防止刷新丢失
      streamSyncTimer = setInterval(() => {
        sessionStore.syncLastMessageToBackend(sessionId).catch(() => {})
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
            item.attachment_ids = m.attachmentIds
          }
          return item
        })
        .filter(m => m.content && m.content.trim())

      logger.log('[ChatStore] streamChat 请求参数, attachment_ids:', options.attachmentIds || [])
      const modelConfig = modelStore.getModelConfig()
      const specialParams = modelConfig.special_params ? { ...modelConfig.special_params } : null
      const result = await streamChat(
        {
          message,
          chat_history: chatHistory,
          mode: currentMode.value,
          use_tools: options.useTools !== false,
          use_web_search: options.use_web_search || false,
          use_knowledge_base: options.use_knowledge_base || false,
          use_deep_thinking: modelStore.thinkingEnabled,
          use_mcp: options.useMcp || false,
          selected_mcp_servers: options.selectedMcpServers || null,
          selected_tools: options.selectedTools || null,
          streaming: true,
          session_id: sessionId,
          selected_knowledge_base: selectedKnowledgeBaseId,
          selected_knowledge_bases: sessionStore.selectedKnowledgeBases?.map(kb => kb.id) || [],
          attachment_ids: options.attachmentIds || [],
          provider_id: modelConfig.provider_id || null,
          model_name: modelConfig.model_name || null,
          special_params: specialParams,
          temperature: modelConfig.temperature || null,
          max_tokens: modelConfig.max_tokens || null,
          research_task_id: currentResearchTaskId || null,
          continue_task_id: continueTaskId,
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
            deepResearchTask.value = data
            // 深度研究任务创建时立即设置 researchTaskId，确保后续审批能正确路由到研究审批 API
            if (data?.task_id) {
              researchTaskId.value = data.task_id
              sessionStore.setResearchTaskIdToLastMessage(sessionId, data.task_id)
            }
          },
          setResearchTaskId: (taskId) => {
            researchTaskId.value = taskId
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
            if (data?.actual_provider && data?.actual_model) {
              const mStore = useModelStore()
              if (mStore.currentProviderId !== data.actual_provider || mStore.currentModelName !== data.actual_model) {
                mStore.currentProviderId = data.actual_provider
                mStore.currentModelName = data.actual_model
              }
            }
          },
          setApproval: (data) => {
            // 保存审批数据时同时保存当前请求的工具配置，审批恢复时使用
            approvalStore.handleApprovalEvent(data, {
              source: 'chat',
              sessionId,
              taskId: data.task_id || null,
              baseApproval: {
                use_tools: options.useTools !== false,
                use_web_search: options.use_web_search || false,
                use_mcp: options.useMcp || false,
                selected_mcp_servers: options.selectedMcpServers || null,
                selected_tools: options.selectedTools || null,
                use_knowledge_base: options.use_knowledge_base || false,
                selected_knowledge_bases: sessionStore.selectedKnowledgeBases?.map(kb => kb.id) || [],
              },
            })
          },
          onRetry: (data) => {
            const backoffStr = data.backoff != null ? data.backoff.toFixed(1) : '?'
            const errorCode = data.errorCode || 'unknown'
            sessionStore.appendToLastMessage(
              sessionId,
              `\n\n> ⏳ 正在重试 (${data.attempt}/${data.max})，${backoffStr}秒后重试... (${errorCode})\n`
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
            // parsed 结构：{ type: "approval_history", data: {...approval_data...}, task_id: "research_xxx" }
            const taskId = parsed.task_id || parsed.data?.task_id || researchTaskId.value || null
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
        sessionStore.updateLastMessage(sessionId, '抱歉，消息发送失败，请稍后重试。')
        ElMessage.error('发送消息失败，请稍后重试')
      }

      if (result.aborted) {
        syncStore.stopStreaming(sessionId)
        return
      }

      // 审批中断时：将工具调用状态标记为 pending_approval，并保存审批数据
      // 这样刷新后前端能正确显示"等待审批"状态，而非"执行中"
      if (approvalStore.pendingApprovals.size > 0) {
        const session = sessionStore.sessions.find(s => s.id === sessionId)
        if (session && session.messages.length > 0) {
          const lastMsg = session.messages[session.messages.length - 1]
          if (lastMsg.toolCalls && Array.isArray(lastMsg.toolCalls)) {
            lastMsg.toolCalls = lastMsg.toolCalls.map(tc => ({
              ...tc,
              status: tc.status === ToolCallStatus.RUNNING ? ToolCallStatus.PENDING_APPROVAL : tc.status,
            }))
          }
          // 向后兼容：保存审批数据到消息对象
          const firstEntry = approvalStore.pendingApprovals.values().next().value
          if (firstEntry) {
            lastMsg.approval = firstEntry.approvalData || firstEntry
            lastMsg.approvalState = 'pending'
          }
          // 同步到 version
          const ver = lastMsg.versions?.[lastMsg.currentVersion]
          if (ver) {
            if (lastMsg.toolCalls) {
              ver.toolCalls = lastMsg.toolCalls.map(tc => ({ ...tc }))
            }
            if (lastMsg.approval) ver.approval = { ...lastMsg.approval }
            ver.approvalState = lastMsg.approvalState
          }
        }
      }

      const finalMessages = sessionStore.getSessionMessages(sessionId) || []
      if (finalMessages.length <= 2) {
        const lastMsg = finalMessages[finalMessages.length - 1]
        if (lastMsg?.content) {
          const title = lastMsg.content.slice(0, 30) + (lastMsg.content.length > 30 ? '...' : '')
          sessionStore.updateSessionTitle(sessionId, title)
        }
      }

      sessionStore.syncLastMessageToBackend(sessionId).catch((error) => {
        logger.error('[ChatStore] 消息同步到后端失败:', error)
        ElMessage.warning({
          message: '消息同步失败，请刷新页面重试',
          duration: 5000,
          showClose: true,
        })
      })
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
    const sessionStore = useSessionStore()
    const modelStore = useModelStore()
    const sid = sessionStore.currentSessionId
    const selectedKnowledgeBaseId = sessionStore.selectedKnowledgeBase?.id || null
    const messages = sessionStore.getSessionMessages(sid)

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
      // 流式输出期间每 5 秒同步 assistant 消息到后端，防止刷新丢失
      streamSyncTimer = setInterval(() => {
        sessionStore.syncLastMessageToBackend(sid).catch(() => {})
      }, 5000)

      const chatHistory = messages
        .slice(0, messageIndex - 1)
        .map(m => ({
          role: m.role || 'user',
          content: m.content || '',
        }))
        .filter(m => m.content && m.content.trim())

      const modelConfig = modelStore.getModelConfig()
      let regenSpecialParams = modelConfig.special_params ? { ...modelConfig.special_params } : null
      const result = await streamChat(
        {
          message: userMessage.content || '',
          chat_history: chatHistory,
          mode: currentMode.value,
          use_tools: true,
          use_web_search: false,
          use_knowledge_base: !!selectedKnowledgeBaseId,
          use_deep_thinking: modelStore.thinkingEnabled,
          use_mcp: false,
          streaming: true,
          session_id: sid,
          selected_knowledge_base: selectedKnowledgeBaseId,
          selected_knowledge_bases: sessionStore.selectedKnowledgeBases?.map(kb => kb.id) || [],
          provider_id: modelConfig.provider_id || null,
          model_name: modelConfig.model_name || null,
          special_params: regenSpecialParams,
          temperature: modelConfig.temperature || null,
          max_tokens: modelConfig.max_tokens || null,
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
            taskId: data.task_id || null,
            baseApproval: {
              use_tools: true,
              use_web_search: false,
              use_mcp: false,
              selected_mcp_servers: null,
              selected_tools: null,
              use_knowledge_base: !!selectedKnowledgeBaseId,
              selected_knowledge_bases: sessionStore.selectedKnowledgeBases?.map(kb => kb.id) || [],
            },
          }),
          onApprovalTimeout: (data) => {
            approvalStore.handleApprovalEvent(data, { source: 'chat', sessionId: sid })
          },
          onApprovalProcessed: (data) => {
            approvalStore.handleApprovalEvent(data, { source: 'chat', sessionId: sid })
          },
          onApprovalHistory: (data) => {
            const taskId = data.task_id || researchTaskId.value || null
            if (taskId) {
              approvalStore.restoreFromSSEHistory(data, taskId, sid)
            }
          },
          onRetry: (data) => {
            const backoffStr = data.backoff != null ? data.backoff.toFixed(1) : '?'
            const errorCode = data.errorCode || 'unknown'
            sessionStore.appendToMessage(sid, messageIndex, `\n\n> ⏳ 正在重试 (${data.attempt}/${data.max})，${backoffStr}秒后重试... (${errorCode})\n`)
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
            if (data?.actual_provider && data?.actual_model) {
              const mStore = useModelStore()
              if (mStore.currentProviderId !== data.actual_provider || mStore.currentModelName !== data.actual_model) {
                mStore.currentProviderId = data.actual_provider
                mStore.currentModelName = data.actual_model
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
      if (data.data?.default_mode) currentMode.value = data.data.default_mode
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

  const clearResearchContext = () => {
    researchContextInfo.value = null
  }

  const clearAll = () => {
    isLoading.value = false
    currentMode.value = 'agent'
    availableModes.value = { 'agent': '代理', 'deep-research': '深度研究' }
    lastStreamError.value = null
    messageCount.value = 0
    deepResearchTask.value = null
    researchTaskId.value = null
    researchContextInfo.value = null
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
   * @param {string|null} userInput - 用户输入（confirm_with_input 模式）
   */
  const _executeApproval = async (approval, approved, userInput = null) => {
    if (!approval) return

    const toolCallId = getInterruptId(approval)
    const entry = approvalStore.pendingApprovals.get(toolCallId)

    // 深度研究审批：如果 researchTaskId 丢失，尝试恢复
    if (approval.source === 'deep_research' && !researchTaskId.value) {
      if (deepResearchTask.value?.task_id) {
        researchTaskId.value = deepResearchTask.value.task_id
        logger.warn('[ChatStore] 深度研究审批时 researchTaskId 为空，从 deepResearchTask 恢复:', researchTaskId.value)
      } else {
        restoreResearchContextFromMessages(useSessionStore().currentSessionId)
        logger.warn('[ChatStore] 深度研究审批时 researchTaskId 为空，已尝试从消息历史恢复:', researchTaskId.value)
      }
    }

    await approvalStore.executeApproval(approval, approved, userInput, {
      taskId: entry?.taskId || researchTaskId.value || null,
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
    deepResearchTask,
    researchTaskId,
    researchContextInfo,
    attachmentProcessing,
    deleteMessage,
    deleteMessagePair,
    approveCommand,
    rejectCommand,
    restoreResearchContextFromMessages,
    clearResearchContext,
  }
})
