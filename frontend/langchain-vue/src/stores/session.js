import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useSessionStore = defineStore('session', () => {
  const sessions = ref([])
  const currentSessionId = ref(null)

  const currentSession = computed(() => {
    return sessions.value.find(s => s.id === currentSessionId.value)
  })

  const getModeLabel = (mode) => {
    const modeLabels = {
      'basic-agent': '基础代理',
      'rag': 'RAG 检索',
      'workflow': '学习工作流',
      'deep-research': '深度研究',
      'guarded': '安全代理',
    }
    return modeLabels[mode] || mode
  }

  const createNewSession = (mode = 'basic-agent') => {
    const newSession = {
      id: Date.now().toString(),
      title: '新对话',
      mode: mode,
      messageCount: 0,
      messages: [],
      createdAt: new Date().toISOString(),
    }
    sessions.value.unshift(newSession)
    currentSessionId.value = newSession.id
    return newSession
  }

  const switchSession = (sessionId) => {
    if (sessions.value.find(s => s.id === sessionId)) {
      currentSessionId.value = sessionId
    }
  }

  const deleteSession = (sessionId) => {
    const index = sessions.value.findIndex(s => s.id === sessionId)
    if (index !== -1) {
      sessions.value.splice(index, 1)
      if (currentSessionId.value === sessionId) {
        if (sessions.value.length > 0) {
          currentSessionId.value = sessions.value[0].id
        } else {
          currentSessionId.value = null
        }
      }
    }
  }

  const updateSessionTitle = (sessionId, title) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session) {
      session.title = title
    }
  }

  const addMessageToSession = (sessionId, message) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session) {
      session.messages.push(message)
      session.messageCount = session.messages.length
    }
  }

  const updateLastMessage = (sessionId, content) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages.length > 0) {
      const lastMessage = session.messages[session.messages.length - 1]
      if (lastMessage.role === 'assistant') {
        lastMessage.content = content
      }
    }
  }

  const appendToLastMessage = (sessionId, content) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages.length > 0) {
      const lastMessage = session.messages[session.messages.length - 1]
      if (lastMessage.role === 'assistant') {
        lastMessage.content = (lastMessage.content || '') + content
      }
    }
  }

  const addSourceToLastMessage = (sessionId, source) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages.length > 0) {
      const lastMessage = session.messages[session.messages.length - 1]
      if (lastMessage.role === 'assistant' && lastMessage.sources) {
        lastMessage.sources.push(source)
      }
    }
  }

  const setSourcesToLastMessage = (sessionId, sources) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages.length > 0) {
      const lastMessage = session.messages[session.messages.length - 1]
      if (lastMessage.role === 'assistant') {
        lastMessage.sources = sources
      }
    }
  }

  const setPlanToLastMessage = (sessionId, plan) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages.length > 0) {
      const lastMessage = session.messages[session.messages.length - 1]
      if (lastMessage.role === 'assistant') {
        lastMessage.plan = plan
      }
    }
  }

  const setChainOfThoughtToLastMessage = (sessionId, cot) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages.length > 0) {
      const lastMessage = session.messages[session.messages.length - 1]
      if (lastMessage.role === 'assistant') {
        lastMessage.chainOfThought = cot
      }
    }
  }

  const addToolCallToLastMessage = (sessionId, toolCall) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages.length > 0) {
      const lastMessage = session.messages[session.messages.length - 1]
      if (lastMessage.role === 'assistant' && lastMessage.toolCalls) {
        lastMessage.toolCalls.push(toolCall)
      }
    }
  }

  const getSessionMessages = (sessionId) => {
    const session = sessions.value.find(s => s.id === sessionId)
    return session ? session.messages : []
  }

  const removeMessagesFromIndex = (sessionId, startIndex) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages.length > startIndex) {
      session.messages.splice(startIndex)
      session.messageCount = session.messages.length
    }
  }

  const clearCurrentSessionMessages = () => {
    const session = sessions.value.find(s => s.id === currentSessionId.value)
    if (session) {
      session.messages = []
      session.messageCount = 0
    }
  }

  if (sessions.value.length === 0) {
    createNewSession('basic-agent')
  }

  return {
    sessions,
    currentSessionId,
    currentSession,
    getModeLabel,
    createNewSession,
    switchSession,
    deleteSession,
    updateSessionTitle,
    addMessageToSession,
    updateLastMessage,
    appendToLastMessage,
    addSourceToLastMessage,
    setSourcesToLastMessage,
    setPlanToLastMessage,
    setChainOfThoughtToLastMessage,
    addToolCallToLastMessage,
    getSessionMessages,
    removeMessagesFromIndex,
    clearCurrentSessionMessages,
  }
})
