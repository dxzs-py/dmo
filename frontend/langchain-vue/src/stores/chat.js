import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useStreamChat } from '../composables/useStreamChat'
import { useSessionStore } from './session'
import { chatAPI } from '../api/client'
import { ElMessage } from 'element-plus'
import { nanoid } from 'nanoid'
import { ChatRequestSchema, validateSchema } from '../lib/validation'

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
    const sessionStore = useSessionStore()
    const sessionId = sessionStore.currentSessionId

    const validation = validateSchema(ChatRequestSchema, {
      message,
      mode: currentMode.value,
      use_tools: options.useTools !== false,
      use_advanced_tools: options.useAdvancedTools || false,
    })

    if (!validation.success) {
      validation.errors.forEach(err => ElMessage.error(err.message))
      return
    }

    if (!sessionId) sessionStore.createNewSession(currentMode.value)

    isLoading.value = true

    const userMessage = {
      id: nanoid(),
      role: 'user',
      content: message,
      timestamp: new Date().toISOString(),
    }
    sessionStore.addMessageToSession(sessionStore.currentSessionId, userMessage)

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
    sessionStore.addMessageToSession(sessionStore.currentSessionId, assistantMessage, false)

    try {
      const messages = sessionStore.getSessionMessages(sessionStore.currentSessionId) || []
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
          session_id: sessionStore.currentSessionId,
        },
        {
          appendToLastMessage: (content) =>
            sessionStore.appendToLastMessage(sessionStore.currentSessionId, content),
          addSource: (data) =>
            sessionStore.addSourceToLastMessage(sessionStore.currentSessionId, data),
          setSources: (data) =>
            sessionStore.setSourcesToLastMessage(sessionStore.currentSessionId, data),
          setPlan: (data) =>
            sessionStore.setPlanToLastMessage(sessionStore.currentSessionId, data),
          setChainOfThought: (data) =>
            sessionStore.setChainOfThoughtToLastMessage(sessionStore.currentSessionId, data),
          addToolCall: (data) =>
            sessionStore.addToolCallToLastMessage(sessionStore.currentSessionId, data),
          setReasoning: (data) =>
            sessionStore.setReasoningToLastMessage(sessionStore.currentSessionId, data),
          setSuggestions: (data) =>
            sessionStore.setSuggestionsToLastMessage(sessionStore.currentSessionId, data),
          setContext: (data) =>
            sessionStore.setContextToLastMessage(sessionStore.currentSessionId, data),
          setCost: (data) => {
            costSummary.value = data
          },
        }
      )

      if (!result.success && !result.aborted) {
        const mockResponses = [
          '您好！我是您的智能学习助手。很高兴为您服务！',
          '感谢您的提问！让我为您解答。',
          '这是一个很好的问题！我会尽力为您提供帮助。',
        ]
        const randomResponse = mockResponses[Math.floor(Math.random() * mockResponses.length)]
        let currentContent = ''
        for (let i = 0; i < randomResponse.length; i++) {
          if (!isStreaming.value) break
          await new Promise(resolve => setTimeout(resolve, 30))
          currentContent += randomResponse[i]
          sessionStore.updateLastMessage(sessionStore.currentSessionId, currentContent)
        }
        ElMessage.error('发送消息失败，请稍后重试')
      }

      if (result.aborted) return

      const finalMessages = sessionStore.getSessionMessages(sessionStore.currentSessionId) || []
      if (finalMessages.length <= 2) {
        const lastMsg = finalMessages[finalMessages.length - 1]
        if (lastMsg?.content) {
          const title = lastMsg.content.slice(0, 30) + (lastMsg.content.length > 30 ? '...' : '')
          sessionStore.updateSessionTitle(sessionStore.currentSessionId, title)
        }
      }

      sessionStore.syncLastMessageToBackend(sessionStore.currentSessionId).catch(() => {})
    } finally {
      isLoading.value = false
    }
  }

  const regenerateMessage = async (messageIndex) => {
    const sessionStore = useSessionStore()
    const messages = sessionStore.getSessionMessages(sessionStore.currentSessionId)

    if (messageIndex < 1 || !messages?.length || messages.length < 2) return

    const userMessage = messages[messageIndex - 1]
    const assistantMessage = messages[messageIndex]
    if (userMessage?.role !== 'user' || assistantMessage?.role !== 'assistant') return

    const newVersionId = nanoid()
    sessionStore.addVersionToMessage(sessionStore.currentSessionId, messageIndex, {
      id: newVersionId,
      content: '',
      sources: [],
      plan: null,
      chainOfThought: null,
      toolCalls: [],
      reasoning: null,
    })

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
          session_id: sessionStore.currentSessionId,
        },
        {
          appendToLastMessage: (content) => {
            const sessionMessages = sessionStore.getSessionMessages(sessionStore.currentSessionId)
            const msg = sessionMessages[messageIndex]
            if (msg?.versions?.[msg.currentVersion] !== undefined) {
              msg.versions[msg.currentVersion].content += content
              msg.content = msg.versions[msg.currentVersion].content
            }
          },
          addSource: (data) => {
            const sessionMessages = sessionStore.getSessionMessages(sessionStore.currentSessionId)
            const msg = sessionMessages[messageIndex]
            if (msg?.versions?.[msg.currentVersion]) {
              if (!msg.versions[msg.currentVersion].sources) msg.versions[msg.currentVersion].sources = []
              msg.versions[msg.currentVersion].sources.push(data)
              msg.sources = msg.versions[msg.currentVersion].sources
            }
          },
          setSources: (data) => {
            const sessionMessages = sessionStore.getSessionMessages(sessionStore.currentSessionId)
            const msg = sessionMessages[messageIndex]
            if (msg?.versions?.[msg.currentVersion]) {
              msg.versions[msg.currentVersion].sources = data
              msg.sources = data
            }
          },
          setPlan: (data) => {
            const sessionMessages = sessionStore.getSessionMessages(sessionStore.currentSessionId)
            const msg = sessionMessages[messageIndex]
            if (msg?.versions?.[msg.currentVersion]) {
              msg.versions[msg.currentVersion].plan = data
              msg.plan = data
            }
          },
          setChainOfThought: (data) => {
            const sessionMessages = sessionStore.getSessionMessages(sessionStore.currentSessionId)
            const msg = sessionMessages[messageIndex]
            if (msg?.versions?.[msg.currentVersion]) {
              msg.versions[msg.currentVersion].chainOfThought = data
              msg.chainOfThought = data
            }
          },
          addToolCall: (data) => {
            const sessionMessages = sessionStore.getSessionMessages(sessionStore.currentSessionId)
            const msg = sessionMessages[messageIndex]
            if (msg?.versions?.[msg.currentVersion]) {
              if (!msg.versions[msg.currentVersion].toolCalls) msg.versions[msg.currentVersion].toolCalls = []
              msg.versions[msg.currentVersion].toolCalls.push(data)
              msg.toolCalls = msg.versions[msg.currentVersion].toolCalls
            }
          },
        }
      )

      if (!result.success && !result.aborted) {
        ElMessage.error('重新生成失败，请稍后重试')
      }
    } finally {
      isLoading.value = false
    }
  }

  const fetchModes = async () => {
    try {
      const response = await chatAPI.getModes()
      const data = response.data
      if (data.code === 200 && data.data?.modes) availableModes.value = data.data.modes
      if (data.data?.default && !currentMode.value) currentMode.value = data.data.default
    } catch (error) {
      console.error('Failed to fetch modes:', error)
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
