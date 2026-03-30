import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { nanoid } from 'nanoid'

const SESSIONS_KEY = 'lc-studylab-sessions'
const CURRENT_SESSION_KEY = 'lc-studylab-current-session'

function generateId() {
  return nanoid()
}

function migrateMessage(msg) {
  if (!msg.versions) {
    return {
      ...msg,
      versions: [
        {
          id: msg.id,
          content: msg.content,
          sources: msg.sources || [],
          plan: msg.plan || null,
          chainOfThought: msg.chainOfThought || null,
          toolCalls: msg.toolCalls || [],
          reasoning: msg.reasoning || null
        }
      ],
      currentVersion: 0
    }
  }
  return msg
}

function getSessionsFromStorage() {
  if (typeof window === 'undefined') return []
  try {
    const data = localStorage.getItem(SESSIONS_KEY)
    if (data) {
      const sessions = JSON.parse(data)
      return sessions.map(session => ({
        ...session,
        messages: session.messages ? session.messages.map(migrateMessage) : []
      }))
    }
    return []
  } catch (error) {
    console.error('Failed to load sessions from storage:', error)
    return []
  }
}

function saveSessionsToStorage(sessions) {
  if (typeof window === 'undefined') return
  try {
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions))
  } catch (error) {
    console.error('Failed to save sessions to storage:', error)
  }
}

function getCurrentSessionIdFromStorage() {
  if (typeof window === 'undefined') return null
  return localStorage.getItem(CURRENT_SESSION_KEY)
}

function saveCurrentSessionIdToStorage(sessionId) {
  if (typeof window === 'undefined') return
  localStorage.setItem(CURRENT_SESSION_KEY, sessionId)
}

function clearCurrentSessionIdFromStorage() {
  if (typeof window === 'undefined') return
  localStorage.removeItem(CURRENT_SESSION_KEY)
}

export const useSessionStore = defineStore('session', () => {
  const sessions = ref(getSessionsFromStorage())
  const currentSessionId = ref(getCurrentSessionIdFromStorage())

  const currentSession = computed(() => {
    return sessions.value.find(s => s.id === currentSessionId.value)
  })

  const getModeLabel = (mode) => {
    const modeLabels = {
      'basic-agent': '基础对话',
      'rag': 'RAG 问答',
      'workflow': '学习工作流',
      'deep-research': '深度研究',
      'guarded': '安全模式',
    }
    return modeLabels[mode] || mode
  }

  const createNewSession = (mode = 'basic-agent', title) => {
    const newSession = {
      id: generateId(),
      title: title || `新对话 - ${getModeLabel(mode)}`,
      mode: mode,
      messageCount: 0,
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
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
          clearCurrentSessionIdFromStorage()
        }
      }
    }
  }

  const updateSession = (sessionId, updates) => {
    const index = sessions.value.findIndex(s => s.id === sessionId)
    if (index !== -1) {
      sessions.value[index] = {
        ...sessions.value[index],
        ...updates,
        updatedAt: Date.now(),
      }
    }
  }

  const updateSessionTitle = (sessionId, title) => {
    updateSession(sessionId, { title })
  }

  const addMessageToSession = (sessionId, message) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session) {
      const messageWithVersions = {
        ...message,
        versions: [
          {
            id: message.id,
            content: message.content,
            sources: message.sources || [],
            plan: message.plan || null,
            chainOfThought: message.chainOfThought || null,
            toolCalls: message.toolCalls || [],
            reasoning: message.reasoning || null
          }
        ],
        currentVersion: 0
      }
      session.messages.push(messageWithVersions)
      session.messageCount = session.messages.length
      session.updatedAt = Date.now()
    }
  }

  const addVersionToMessage = (sessionId, messageIndex, version) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages[messageIndex]) {
      const message = session.messages[messageIndex]
      if (!message.versions) {
        message.versions = [
          {
            id: message.id,
            content: message.content,
            sources: message.sources || [],
            plan: message.plan || null,
            chainOfThought: message.chainOfThought || null,
            toolCalls: message.toolCalls || [],
            reasoning: message.reasoning || null
          }
        ]
        message.currentVersion = 0
      }
      message.versions.push(version)
      message.currentVersion = message.versions.length - 1
      session.updatedAt = Date.now()
    }
  }

  const switchMessageVersion = (sessionId, messageIndex, versionIndex) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages[messageIndex]) {
      const message = session.messages[messageIndex]
      if (message.versions && versionIndex >= 0 && versionIndex < message.versions.length) {
        message.currentVersion = versionIndex
        const version = message.versions[versionIndex]
        message.content = version.content
        message.sources = version.sources
        message.plan = version.plan
        message.chainOfThought = version.chainOfThought
        message.toolCalls = version.toolCalls
        message.reasoning = version.reasoning
        session.updatedAt = Date.now()
      }
    }
  }

  const updateLastMessage = (sessionId, content) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages.length > 0) {
      const lastMessage = session.messages[session.messages.length - 1]
      if (lastMessage.role === 'assistant') {
        lastMessage.content = content
        session.updatedAt = Date.now()
      }
    }
  }

  const appendToLastMessage = (sessionId, content) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages.length > 0) {
      const lastMessage = session.messages[session.messages.length - 1]
      if (lastMessage.role === 'assistant') {
        lastMessage.content = (lastMessage.content || '') + content
        session.updatedAt = Date.now()
      }
    }
  }

  const addSourceToLastMessage = (sessionId, source) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages.length > 0) {
      const lastMessage = session.messages[session.messages.length - 1]
      if (lastMessage.role === 'assistant') {
        if (!lastMessage.sources) {
          lastMessage.sources = []
        }
        lastMessage.sources.push(source)
        session.updatedAt = Date.now()
      }
    }
  }

  const setSourcesToLastMessage = (sessionId, sources) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages.length > 0) {
      const lastMessage = session.messages[session.messages.length - 1]
      if (lastMessage.role === 'assistant') {
        lastMessage.sources = sources
        session.updatedAt = Date.now()
      }
    }
  }

  const setPlanToLastMessage = (sessionId, plan) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages.length > 0) {
      const lastMessage = session.messages[session.messages.length - 1]
      if (lastMessage.role === 'assistant') {
        lastMessage.plan = plan
        session.updatedAt = Date.now()
      }
    }
  }

  const setChainOfThoughtToLastMessage = (sessionId, cot) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages.length > 0) {
      const lastMessage = session.messages[session.messages.length - 1]
      if (lastMessage.role === 'assistant') {
        lastMessage.chainOfThought = cot
        session.updatedAt = Date.now()
      }
    }
  }

  const addToolCallToLastMessage = (sessionId, toolCall) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages.length > 0) {
      const lastMessage = session.messages[session.messages.length - 1]
      if (lastMessage.role === 'assistant') {
        if (!lastMessage.toolCalls) {
          lastMessage.toolCalls = []
        }
        lastMessage.toolCalls.push(toolCall)
        session.updatedAt = Date.now()
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
      session.updatedAt = Date.now()
    }
  }

  const clearCurrentSessionMessages = () => {
    const session = sessions.value.find(s => s.id === currentSessionId.value)
    if (session) {
      session.messages = []
      session.messageCount = 0
      session.updatedAt = Date.now()
    }
  }

  watch(sessions, (newSessions) => {
    saveSessionsToStorage(newSessions)
  }, { deep: true })

  watch(currentSessionId, (newId) => {
    if (newId) {
      saveCurrentSessionIdToStorage(newId)
    } else {
      clearCurrentSessionIdFromStorage()
    }
  })

  if (sessions.value.length === 0) {
    createNewSession('basic-agent')
  } else if (!currentSessionId.value || !sessions.value.find(s => s.id === currentSessionId.value)) {
    currentSessionId.value = sessions.value[0].id
  }

  return {
    sessions,
    currentSessionId,
    currentSession,
    getModeLabel,
    createNewSession,
    switchSession,
    deleteSession,
    updateSession,
    updateSessionTitle,
    addMessageToSession,
    addVersionToMessage,
    switchMessageVersion,
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
