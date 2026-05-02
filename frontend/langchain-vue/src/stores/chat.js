import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useStreamChat } from '../composables/useStreamChat'
import { useSessionStore } from './session'
import { chatAPI } from '../api'
import { ElMessage } from 'element-plus'
import { nanoid } from 'nanoid'
import { ChatRequestSchema, validateSchema } from '../utils/validation'
import { logger } from '../utils/logger'
import { getModeLabel } from '../utils/format'
import { transformFrontendMessageToBackend } from '../utils/session-transformers'

export const useChatStore = defineStore('chat', () => {
  const isLoading = ref(false)
  const currentMode = ref('basic-agent')
  const availableModes = ref({
    'basic-agent': '基础代理',
    'deep-thinking': '深度思考',
    'rag': 'RAG 检索',
    'workflow': '学习工作流',
    'deep-research': '深度研究',
    'guarded': '安全代理',
  })
  const costSummary = ref({})

  const { isStreaming, abortController, abort: stopStreaming, streamChat } = useStreamChat()

  const sendMessage = async (message, options = {}) => {
    logger.log('[ChatStore] sendMessage called')
    
    const sessionStore = useSessionStore()
    let sessionId = sessionStore.currentSessionId
    const selectedKnowledgeBaseId = sessionStore.selectedKnowledgeBase?.id || null
    
    logger.log('[ChatStore] sessionId:', sessionId)

    const validation = validateSchema(ChatRequestSchema, {
      message,
      mode: currentMode.value,
      use_tools: options.useTools !== false,
      use_advanced_tools: options.useAdvancedTools || false,
      selected_knowledge_base: selectedKnowledgeBaseId,
    })

    if (!validation.success) {
      validation.errors.forEach(err => ElMessage.error(err.message))
      return
    }

    if (!sessionId) {
      logger.log('[ChatStore] Creating new session...')
      await sessionStore.createNewSession(currentMode.value)
      sessionId = sessionStore.currentSessionId
      if (selectedKnowledgeBaseId) {
        await sessionStore.setSelectedKnowledgeBase(selectedKnowledgeBaseId)
      }
      logger.log('[ChatStore] New sessionId:', sessionId)
    }

    isLoading.value = true

    const userMessage = {
      id: nanoid(),
      role: 'user',
      content: message,
      timestamp: new Date().toISOString(),
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

    try {
      const messages = sessionStore.getSessionMessages(sessionId) || []
      
      const chatHistory = messages
        .slice(0, -2)
        .map(m => ({
          role: m.role || 'user',
          content: m.content || '',
        }))
        .filter(m => m.content && m.content.trim())

      const result = await streamChat(
        {
          message,
          chat_history: chatHistory,
          mode: currentMode.value,
          use_tools: options.useTools !== false,
          use_advanced_tools: options.useAdvancedTools || false,
          streaming: true,
          session_id: sessionId,
          selected_knowledge_base: selectedKnowledgeBaseId,
        },
        {
          appendToLastMessage: (content) => sessionStore.appendToLastMessage(sessionId, content),
          addSource: (data) => sessionStore.addSourceToLastMessage(sessionId, data),
          setSources: (data) => sessionStore.setSourcesToLastMessage(sessionId, data),
          setPlan: (data) => sessionStore.setPlanToLastMessage(sessionId, data),
          setChainOfThought: (data) => sessionStore.setChainOfThoughtToLastMessage(sessionId, data),
          addToolCall: (data) => sessionStore.addToolCallToLastMessage(sessionId, data),
          setReasoning: (data) => sessionStore.setReasoningToLastMessage(sessionId, data),
          setSuggestions: (data) => sessionStore.setSuggestionsToLastMessage(sessionId, data),
          setContext: (data) => sessionStore.setContextToLastMessage(sessionId, data),
          setCost: (data) => { costSummary.value = data },
        }
      )

      if (!result.success && !result.aborted) {
        sessionStore.updateLastMessage(sessionId, '抱歉，消息发送失败，请稍后重试。')
        ElMessage.error('发送消息失败，请稍后重试')
      }

      if (result.aborted) return

      const finalMessages = sessionStore.getSessionMessages(sessionId) || []
      if (finalMessages.length <= 2) {
        const lastMsg = finalMessages[finalMessages.length - 1]
        if (lastMsg?.content) {
          const title = lastMsg.content.slice(0, 30) + (lastMsg.content.length > 30 ? '...' : '')
          sessionStore.updateSessionTitle(sessionId, title)
        }
      }

      sessionStore.syncLastMessageToBackend(sessionId).catch(() => {})
    } finally {
      isLoading.value = false
      sessionStore.touchSessionUpdatedAt(sessionId)
    }
  }

  const regenerateMessage = async (messageIndex) => {
    const sessionStore = useSessionStore()
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

    try {
      const chatHistory = messages
        .slice(0, messageIndex)
        .map(m => ({
          role: m.role || 'user',
          content: m.content || '',
        }))
        .filter(m => m.content && m.content.trim())

      const result = await streamChat(
        {
          message: userMessage.content || '',
          chat_history: chatHistory,
          mode: currentMode.value,
          use_tools: true,
          use_advanced_tools: false,
          streaming: true,
          session_id: sid,
          selected_knowledge_base: selectedKnowledgeBaseId,
        },
        {
          appendToLastMessage: (content) => sessionStore.appendToMessage(sid, messageIndex, content),
          addSource: (data) => sessionStore.addSourceToMessage(sid, messageIndex, data),
          setSources: (data) => sessionStore.setSourcesToMessage(sid, messageIndex, data),
          setPlan: (data) => sessionStore.setPlanToMessage(sid, messageIndex, data),
          setChainOfThought: (data) => sessionStore.setChainOfThoughtToMessage(sid, messageIndex, data),
          addToolCall: (data) => sessionStore.addToolCallToMessage(sid, messageIndex, data),
          setReasoning: (data) => sessionStore.setReasoningToMessage(sid, messageIndex, data),
          setSuggestions: (data) => sessionStore.setSuggestionsToMessage(sid, messageIndex, data),
          setContext: (data) => sessionStore.setContextToMessage(sid, messageIndex, data),
          setCost: (data) => { costSummary.value = data },
        }
      )

      if (!result.success && !result.aborted) {
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
      if (data.code === 200 && data.data?.modes) availableModes.value = data.data.modes
      if (data.data?.default && !currentMode.value) currentMode.value = data.data.default
    } catch (error) {
      logger.error('Failed to fetch modes:', error)
    }
  }

  const clearCurrentSession = () => {
    const sessionStore = useSessionStore()
    sessionStore.clearCurrentSessionMessages()
  }

  return {
    isLoading,
    isStreaming,
    currentMode,
    availableModes,
    abortController,
    costSummary,
    sendMessage,
    regenerateMessage,
    fetchModes,
    clearCurrentSession,
    stopStreaming,
  }
})
