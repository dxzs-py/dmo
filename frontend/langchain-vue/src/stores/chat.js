import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { useStreamChat, CONNECTION_STATUS } from '../composables/useStreamChat'
import { useSessionStore } from './session'
import { useModelStore } from './model'
import { useUserStore } from './user'
import { chatAPI } from '../api'
import { ElMessage, ElNotification } from 'element-plus'
import { nanoid } from 'nanoid'
import { ChatRequestSchema, validateSchema } from '../utils/validation'
import { logger } from '../utils/logger'
import { getModeLabel } from '../utils/format'
import { transformFrontendMessageToBackend } from '../utils/session-transformers'

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
  const researchContextInfo = ref(null)  // { taskId, query } 持久化研究上下文标识，不随消息发送清空
  const attachmentProcessing = ref(null)
  const pendingApprovals = ref(new Map())  // Map<toolCallId, approvalData> 跟踪所有待处理审批

  /**
   * 共用函数：处理 SSE 流中的 approval 事件
   * 在 sendMessage、approveCommand、rejectCommand 三处复用
   *
   * @param {Object} parsedData - SSE 解析后的 approval.data
   * @param {string} sessionId - 当前会话 ID
   * @param {Object} baseApproval - 已有审批数据（用于继承工具/模型配置）
   */
  const handleApprovalSSEEvent = (parsedData, sessionId, baseApproval = {}) => {
    const sessionStore = useSessionStore()
    const toolCallId = parsedData.tool_call_id || parsedData.interrupt_id || ''
    const approvalData = {
      ...parsedData,
      state: 'pending',
      use_tools: baseApproval.use_tools ?? true,
      use_web_search: baseApproval.use_web_search ?? false,
      use_mcp: baseApproval.use_mcp ?? false,
      selected_mcp_servers: baseApproval.selected_mcp_servers ?? null,
      selected_tools: baseApproval.selected_tools ?? null,
      use_knowledge_base: baseApproval.use_knowledge_base ?? false,
      selected_knowledge_bases: baseApproval.selected_knowledge_bases ?? [],
    }
    pendingApprovals.value.set(toolCallId, approvalData)
    sessionStore.setApprovalToToolCall(sessionId, toolCallId, approvalData)
    sessionStore.updateToolCallStatus(sessionId, toolCallId, 'pending_approval', approvalData)
    sessionStore.setApprovalToLastMessage(sessionId, { ...parsedData, state: 'pending' })
  }

  /**
   * 从已加载的消息中恢复 pendingApprovals Map
   * 页面刷新后 toolCalls 已从后端恢复，但 pendingApprovals 是运行时 Map，需要重建
   */
  const restorePendingApprovals = (sessionId) => {
    const sessionStore = useSessionStore()
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session?.messages) return
    for (const msg of session.messages) {
      if (!msg.toolCalls || !Array.isArray(msg.toolCalls)) continue
      for (const tc of msg.toolCalls) {
        const approval = tc.approval
        if (approval && approval.state === 'pending') {
          const toolCallId = approval.tool_call_id || approval.interrupt_id || tc.id || ''
          if (toolCallId && !pendingApprovals.value.has(toolCallId)) {
            pendingApprovals.value.set(toolCallId, approval)
          }
        }
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
    deepResearchTask.value = null
    attachmentProcessing.value = null
    const currentResearchTaskId = researchTaskId.value
    researchTaskId.value = null
    const currentResearchContextInfo = researchContextInfo.value
    researchContextInfo.value = null
    const continueTaskId = options.continue_task_id || null

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
    sessionStore.addMessageToSession(sessionId, assistantMessage, false)
    messageCount.value += 1

    try {
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
          setDeepResearchTask: (data) => { deepResearchTask.value = data },
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
            handleApprovalSSEEvent(data, sessionId, {
              use_tools: options.useTools !== false,
              use_web_search: options.use_web_search || false,
              use_mcp: options.useMcp || false,
              selected_mcp_servers: options.selectedMcpServers || null,
              selected_tools: options.selectedTools || null,
              use_knowledge_base: options.use_knowledge_base || false,
              selected_knowledge_bases: sessionStore.selectedKnowledgeBases?.map(kb => kb.id) || [],
            })
          },
        }
      )

      if (!result.success && !result.aborted) {
        lastStreamError.value = result.error?.message || '消息发送失败'
        sessionStore.updateLastMessage(sessionId, '抱歉，消息发送失败，请稍后重试。')
        ElMessage.error('发送消息失败，请稍后重试')
      }

      if (result.aborted) return

      // 审批中断时：将工具调用状态标记为 pending_approval，并保存审批数据
      // 这样刷新后前端能正确显示"等待审批"状态，而非"执行中"
      if (pendingApprovals.value.size > 0) {
        const session = sessionStore.sessions.find(s => s.id === sessionId)
        if (session && session.messages.length > 0) {
          const lastMsg = session.messages[session.messages.length - 1]
          if (lastMsg.toolCalls && Array.isArray(lastMsg.toolCalls)) {
            lastMsg.toolCalls = lastMsg.toolCalls.map(tc => ({
              ...tc,
              status: tc.status === 'running' ? 'pending_approval' : tc.status,
            }))
          }
          // 向后兼容：保存审批数据到消息对象
          const firstApproval = pendingApprovals.value.values().next().value
          if (firstApproval) {
            lastMsg.approval = firstApproval
            lastMsg.approvalState = 'pending'
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
      isLoading.value = false
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

    try {
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
    pendingApprovals.value.clear()
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
    if (researchTaskId) {
      ElMessage.info({ message: '关联的深度研究任务已清理', duration: 3000 })
    }
  }

  const deleteMessagePair = async (sessionId, backendId, frontendId) => {
    const sessionStore = useSessionStore()
    await chatAPI.deleteMessagePair(sessionId, backendId)
    sessionStore.removeMessagePairFromSession(sessionId, frontendId || backendId)
    ElMessage.success('消息已删除')
  }

  const approveCommand = async (approval, userInput = null) => {
    if (!approval) return
    const toolCallId = approval.tool_call_id || approval.interrupt_id || ''
    const approvalData = pendingApprovals.value.get(toolCallId) || approval
    if (!approvalData) return

    const sessionStore = useSessionStore()
    const sessionId = sessionStore.currentSessionId

    // 更新对应 toolCall 状态为 approved
    sessionStore.updateToolCallStatus(sessionId, toolCallId, 'approved', approval)
    // 从 pendingApprovals Map 中移除该审批（已处理完成）
    pendingApprovals.value.delete(toolCallId)
    // 向后兼容：更新消息级审批状态
    sessionStore.setApprovalToLastMessage(sessionId, { ...approvalData, state: 'approved' })

    try {
      // 调用审批 API，通过 Command(resume=...) 恢复 Agent 执行
      const interruptId = approvalData.interrupt_id || approvalData.tool_call_id || ''
      const modelStore = useModelStore()
      const modelConfig = modelStore.getModelConfig()
      const requestBody = {
        session_id: sessionId,
        interrupt_id: interruptId,
        approved: true,
        // 传递模型配置，确保审批恢复时使用正确的模型
        provider_id: modelConfig.provider_id || null,
        model_name: modelConfig.model_name || null,
        use_deep_thinking: modelStore.thinkingEnabled,
        special_params: modelConfig.special_params ? { ...modelConfig.special_params } : null,
        temperature: modelConfig.temperature || null,
        max_tokens: modelConfig.max_tokens || null,
        // 传递工具配置，确保审批恢复时使用与原始请求一致的工具集
        use_tools: approvalData.use_tools ?? true,
        use_web_search: approvalData.use_web_search ?? false,
        use_mcp: approvalData.use_mcp ?? false,
        selected_mcp_servers: approvalData.selected_mcp_servers ?? null,
        selected_tools: approvalData.selected_tools ?? null,
        use_knowledge_base: approvalData.use_knowledge_base ?? false,
        selected_knowledge_bases: approvalData.selected_knowledge_bases ?? [],
      }
      // CONFIRM_WITH_INPUT 模式：传递用户输入值
      if (approvalData.action === 'confirm_with_input' && userInput !== null) {
        requestBody.user_input = userInput
      }

      const response = await fetch('/api/v1/chat/approval/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${localStorage.getItem('user_token')}`,
        },
        body: JSON.stringify(requestBody),
      })

      if (!response.ok) {
        throw new Error(`审批请求失败: ${response.status}`)
      }

      // 处理 SSE 流式响应
      isStreaming.value = true
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ') || line === 'data: [DONE]') continue
          try {
            const parsed = JSON.parse(line.slice(6))
            if (parsed.type === 'chunk' && parsed.content) {
              sessionStore.appendToLastAssistantMessage(sessionId, parsed.content)
            } else if (parsed.type === 'tool') {
              sessionStore.addOrUpdateToolCallToLastMessage(sessionId, parsed.data)
            } else if (parsed.type === 'tool_result') {
              sessionStore.updateOrAddToolResultToLastMessage(sessionId, parsed.data)
            } else if (parsed.type === 'reasoning' && parsed.data?.content) {
              sessionStore.setReasoningToLastMessage(sessionId, parsed.data)
            } else if (parsed.type === 'approval' && parsed.data) {
              // 审批恢复后 agent 又触发新的审批请求，复用共用函数
              handleApprovalSSEEvent(parsed.data, sessionId, approvalData)
            }
          } catch (e) {
            // 忽略解析错误
          }
        }
      }

      isStreaming.value = false

      // 审批完成后更新消息的审批状态
      const session = sessionStore.sessions.find(s => s.id === sessionId)
      if (session && session.messages.length > 0) {
        const lastMsg = session.messages[session.messages.length - 1]
        lastMsg.approvalState = 'approved'
        if (lastMsg.approval) {
          lastMsg.approval = { ...lastMsg.approval, state: 'approved' }
        }
        // 仅将本次审批的 toolCall 状态更新为 completed，不影响其他 pending_approval 的 toolCall
        if (lastMsg.toolCalls && Array.isArray(lastMsg.toolCalls)) {
          lastMsg.toolCalls = lastMsg.toolCalls.map(tc => ({
            ...tc,
            status: tc.id === toolCallId && tc.status === 'approved' ? 'completed' : tc.status,
          }))
        }
      }

      // 审批完成后同步消息到后端，确保工具调用结果被持久化
      try {
        await sessionStore.syncLastMessageToBackend(sessionId)
      } catch (syncErr) {
        console.error('审批后同步消息失败:', syncErr)
      }
    } catch (err) {
      console.error('审批确认失败:', err)
      isStreaming.value = false
      // 恢复审批状态：重新添加到 pendingApprovals，以便用户重试
      pendingApprovals.value.set(toolCallId, { ...approvalData, state: 'pending' })
      sessionStore.updateToolCallStatus(sessionId, toolCallId, 'pending_approval', approval)
      sessionStore.setApprovalToLastMessage(sessionId, { ...approvalData, state: 'pending' })
    }
  }

  const rejectCommand = async (approval) => {
    if (!approval) return
    const toolCallId = approval.tool_call_id || approval.interrupt_id || ''
    const approvalData = pendingApprovals.value.get(toolCallId) || approval
    if (!approvalData) return

    const sessionStore = useSessionStore()
    const sessionId = sessionStore.currentSessionId

    // 更新对应 toolCall 状态为 rejected
    sessionStore.updateToolCallStatus(sessionId, toolCallId, 'rejected', approval)
    // 从 pendingApprovals Map 中移除该审批（不再待处理）
    pendingApprovals.value.delete(toolCallId)
    // 向后兼容：更新消息级审批状态
    sessionStore.setApprovalToLastMessage(sessionId, { ...approvalData, state: 'rejected' })

    try {
      // 调用审批 API，通过 Command(resume=False) 告知 Agent 用户拒绝
      const interruptId = approvalData.interrupt_id || approvalData.tool_call_id || ''
      const modelStore = useModelStore()
      const modelConfig = modelStore.getModelConfig()
      const response = await fetch('/api/v1/chat/approval/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${localStorage.getItem('user_token')}`,
        },
        body: JSON.stringify({
          session_id: sessionId,
          interrupt_id: interruptId,
          approved: false,
          provider_id: modelConfig.provider_id || null,
          model_name: modelConfig.model_name || null,
          use_deep_thinking: modelStore.thinkingEnabled,
          special_params: modelConfig.special_params ? { ...modelConfig.special_params } : null,
          temperature: modelConfig.temperature || null,
          max_tokens: modelConfig.max_tokens || null,
          use_tools: approvalData.use_tools ?? true,
          use_web_search: approvalData.use_web_search ?? false,
          use_mcp: approvalData.use_mcp ?? false,
          selected_mcp_servers: approvalData.selected_mcp_servers ?? null,
          selected_tools: approvalData.selected_tools ?? null,
          use_knowledge_base: approvalData.use_knowledge_base ?? false,
          selected_knowledge_bases: approvalData.selected_knowledge_bases ?? [],
        }),
      })

      if (!response.ok) {
        throw new Error(`审批拒绝请求失败: ${response.status}`)
      }

      // 处理 SSE 流式响应（拒绝后 Agent 可能会输出"用户已拒绝"的文本）
      // 继续读取直到流自然结束
      isStreaming.value = true
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ') || line === 'data: [DONE]') continue
          try {
            const parsed = JSON.parse(line.slice(6))
            if (parsed.type === 'chunk' && parsed.content) {
              sessionStore.appendToLastAssistantMessage(sessionId, parsed.content)
            } else if (parsed.type === 'tool') {
              sessionStore.addOrUpdateToolCallToLastMessage(sessionId, parsed.data)
            } else if (parsed.type === 'tool_result') {
              sessionStore.updateOrAddToolResultToLastMessage(sessionId, parsed.data)
            } else if (parsed.type === 'reasoning' && parsed.data?.content) {
              sessionStore.setReasoningToLastMessage(sessionId, parsed.data)
            } else if (parsed.type === 'approval' && parsed.data) {
              // 拒绝后 agent 又触发新的审批请求，复用共用函数
              handleApprovalSSEEvent(parsed.data, sessionId, approvalData)
            }
          } catch (e) {
            // 忽略解析错误
          }
        }
      }

      isStreaming.value = false
    } catch (err) {
      console.error('审批拒绝失败:', err)
      isStreaming.value = false
    }
  }

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
    pendingApprovals,
    approveCommand,
    rejectCommand,
    restorePendingApprovals,
    clearResearchContext,
  }
})
