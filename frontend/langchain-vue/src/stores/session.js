import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { nanoid } from 'nanoid'
import { chatAPI } from '../api/client'
import { useUserStore } from './user'
import { ElMessage } from 'element-plus'

function generateId() {
  return nanoid()
}

function _migrateMessage(msg) {
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

function transformBackendSessionToFrontend(session) {
  return {
    id: session.session_id,
    sessionId: session.session_id,
    title: session.title,
    mode: session.mode,
    messageCount: session.message_count || 0,
    messages: (session.messages || []).map(msg => transformBackendMessageToFrontend(msg)),
    createdAt: new Date(session.created_at).getTime(),
    updatedAt: new Date(session.updated_at).getTime(),
  }
}

function transformBackendMessageToFrontend(msg) {
  return {
    id: msg.id?.toString() || generateId(),
    role: msg.role,
    content: msg.content,
    sources: msg.sources || [],
    plan: msg.plan,
    chainOfThought: msg.chain_of_thought,
    toolCalls: msg.tool_calls || [],
    reasoning: msg.reasoning,
    versions: msg.versions?.length > 0 ? msg.versions : [
      {
        id: msg.id?.toString() || generateId(),
        content: msg.content,
        sources: msg.sources || [],
        plan: msg.plan,
        chainOfThought: msg.chain_of_thought,
        toolCalls: msg.tool_calls || [],
        reasoning: msg.reasoning
      }
    ],
    currentVersion: msg.current_version || 0,
    timestamp: new Date(msg.created_at).getTime(),
  }
}

function transformFrontendMessageToBackend(msg) {
  const currentVersion = msg.versions?.[msg.currentVersion || 0] || msg
  return {
    role: msg.role,
    content: msg.content,
    sources: msg.sources || currentVersion.sources || [],
    plan: msg.plan || currentVersion.plan || null,
    chain_of_thought: msg.chainOfThought || currentVersion.chainOfThought || null,
    tool_calls: msg.toolCalls || currentVersion.toolCalls || [],
    reasoning: msg.reasoning || currentVersion.reasoning || null,
    versions: msg.versions || [],
    current_version: msg.currentVersion || 0,
  }
}

export const useSessionStore = defineStore('session', () => {
  const sessions = ref([])
  const currentSessionId = ref(null)
  const isLoading = ref(false)
  const lastLoadedUserId = ref(null)

  const userStore = useUserStore()

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

  const clearAllLocalData = () => {
    sessions.value = []
    currentSessionId.value = null
    lastLoadedUserId.value = null
    console.log('[Security] Cleared all local session data')
  }

  const loadSessionsFromBackend = async () => {
    if (!userStore.isLoggedIn) {
      clearAllLocalData()
      return
    }

    const currentUserId = userStore.userInfo?.id

    if (lastLoadedUserId.value && lastLoadedUserId.value !== currentUserId) {
      console.log(`[Security] User switched from ${lastLoadedUserId.value} to ${currentUserId}, clearing old data`)
      clearAllLocalData()
    }

    isLoading.value = true
    try {
      const response = await chatAPI.getSessions()
      if (response.data.code === 0) {
        const data = response.data.data
        const sessionList = Array.isArray(data) ? data : (data?.items || [])
        sessions.value = sessionList.map(transformBackendSessionToFrontend)
        lastLoadedUserId.value = currentUserId

        if (sessions.value.length > 0) {
          if (!currentSessionId.value || !sessions.value.find(s => s.id === currentSessionId.value)) {
            currentSessionId.value = sessions.value[0].id
          }
        } else {
          currentSessionId.value = null
        }

        console.log(`[Security] Loaded ${sessions.value.length} sessions for user ${currentUserId}`)
      }
    } catch (error) {
      console.error('Failed to load sessions from backend:', error)
      ElMessage.error('加载会话失败')
    } finally {
      isLoading.value = false
    }
  }

  const loadSessionDetail = async (sessionId) => {
    if (!userStore.isLoggedIn || !sessionId) return null

    try {
      const response = await chatAPI.getSession(sessionId)
      if (response.data.code === 0 && response.data.data) {
        const detailData = transformBackendSessionToFrontend(response.data.data)
        const index = sessions.value.findIndex(s => s.id === sessionId)
        if (index !== -1) {
          sessions.value[index] = detailData
        }
        console.log(`[Session] Loaded detail for session ${sessionId} with ${detailData.messages?.length || 0} messages`)
        return detailData
      }
    } catch (error) {
      console.error('Failed to load session detail:', error)
    }
    return null
  }

  const createNewSession = async (mode = 'basic-agent', title) => {
    if (!userStore.isLoggedIn) {
      ElMessage.warning('请先登录后再使用对话功能')
      return null
    }

    try {
      const response = await chatAPI.createSession({
        title: title || `新对话 - ${getModeLabel(mode)}`,
        mode: mode
      })

      if (response.data.code === 0) {
        const newSession = transformBackendSessionToFrontend(response.data.data)
        sessions.value.unshift(newSession)
        currentSessionId.value = newSession.id
        console.log(`[Security] Created new session ${newSession.id} for user ${userStore.userInfo?.id}`)
        return newSession
      }
    } catch (error) {
      console.error('Failed to create session:', error)
      ElMessage.error('创建会话失败')
    }
  }

  const switchSession = async (sessionId) => {
    if (sessions.value.find(s => s.id === sessionId)) {
      currentSessionId.value = sessionId
      const session = sessions.value.find(s => s.id === sessionId)
      if (session && (!session.messages || session.messages.length === 0)) {
        await loadSessionDetail(sessionId)
      }
    }
  }

  const deleteSession = async (sessionId) => {
    const index = sessions.value.findIndex(s => s.id === sessionId)
    if (index !== -1) {
      if (userStore.isLoggedIn) {
        try {
          await chatAPI.deleteSession(sessionId)
          console.log(`[Security] Deleted session ${sessionId} for user ${userStore.userInfo?.id}`)
        } catch (error) {
          console.error('Failed to delete session from backend:', error)
        }
      }

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

  const updateSession = (sessionId, updates) => {
    const index = sessions.value.findIndex(s => s.id === sessionId)
    if (index !== -1) {
      sessions.value[index] = {
        ...sessions.value[index],
        ...updates,
        updatedAt: Date.now(),
      }

      if (userStore.isLoggedIn && updates.title) {
        chatAPI.updateSession(sessionId, { title: updates.title }).catch(error => {
          console.error('Failed to update session on backend:', error)
        })
      }
    }
  }

  const updateSessionTitle = (sessionId, title) => {
    updateSession(sessionId, { title })
  }

  const addMessageToSession = async (sessionId, message, saveToBackend = true) => {
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

      if (saveToBackend && userStore.isLoggedIn) {
        const backendMsg = transformFrontendMessageToBackend(messageWithVersions)
        chatAPI.addMessage(sessionId, backendMsg).then(res => {
          if (res.data.code === 0 && res.data.data?.id) {
            messageWithVersions.backendId = res.data.data.id
          }
        }).catch(error => {
          console.error('Failed to save message to backend:', error)
        })
      }
    }
  }

  const syncLastMessageToBackend = async (sessionId) => {
    if (!userStore.isLoggedIn) return
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session || session.messages.length === 0) return

    const lastMessage = session.messages[session.messages.length - 1]
    if (!lastMessage.backendId) {
      const backendMsg = transformFrontendMessageToBackend(lastMessage)
      try {
        const res = await chatAPI.addMessage(sessionId, backendMsg)
        if (res.data.code === 0 && res.data.data?.id) {
          lastMessage.backendId = res.data.data.id
        }
      } catch (error) {
        console.error('Failed to sync message to backend:', error)
      }
    } else {
      const backendMsg = transformFrontendMessageToBackend(lastMessage)
      try {
        await chatAPI.updateMessage(lastMessage.backendId, backendMsg)
      } catch (error) {
        console.error('Failed to update message in backend:', error)
      }
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
        if (lastMessage.versions && lastMessage.versions[lastMessage.currentVersion]) {
          lastMessage.versions[lastMessage.currentVersion].content = content
        }
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
        if (lastMessage.versions && lastMessage.versions[lastMessage.currentVersion]) {
          lastMessage.versions[lastMessage.currentVersion].content = lastMessage.content
        }
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
        if (lastMessage.versions && lastMessage.versions[lastMessage.currentVersion]) {
          if (!lastMessage.versions[lastMessage.currentVersion].sources) {
            lastMessage.versions[lastMessage.currentVersion].sources = []
          }
          lastMessage.versions[lastMessage.currentVersion].sources.push(source)
        }
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
        if (lastMessage.versions && lastMessage.versions[lastMessage.currentVersion]) {
          lastMessage.versions[lastMessage.currentVersion].sources = sources
        }
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
        if (lastMessage.versions && lastMessage.versions[lastMessage.currentVersion]) {
          lastMessage.versions[lastMessage.currentVersion].plan = plan
        }
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
        if (lastMessage.versions && lastMessage.versions[lastMessage.currentVersion]) {
          lastMessage.versions[lastMessage.currentVersion].chainOfThought = cot
        }
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
        if (lastMessage.versions && lastMessage.versions[lastMessage.currentVersion]) {
          if (!lastMessage.versions[lastMessage.currentVersion].toolCalls) {
            lastMessage.versions[lastMessage.currentVersion].toolCalls = []
          }
          lastMessage.versions[lastMessage.currentVersion].toolCalls.push(toolCall)
        }
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

  const initialize = async () => {
    console.log('[Security] Initializing session store...')
    if (userStore.isLoggedIn) {
      await loadSessionsFromBackend()
    } else {
      clearAllLocalData()
    }
  }

  watch(() => userStore.isLoggedIn, async (newVal, oldVal) => {
    if (oldVal && !newVal) {
      console.log('[Security] User logged out, clearing all session data')
      clearAllLocalData()
    } else if (!oldVal && newVal) {
      console.log('[Security] User logged in, loading sessions from backend')
      await initialize()
    } else if (oldVal && newVal) {
      const oldUserId = lastLoadedUserId.value
      const newUserId = userStore.userInfo?.id
      if (oldUserId && newUserId && oldUserId !== newUserId) {
        console.log(`[Security] User account changed from ${oldUserId} to ${newUserId}`)
        await initialize()
      }
    }
  })

  watch(() => userStore.userInfo?.id, async (newUserId, oldUserId) => {
    if (oldUserId && newUserId && oldUserId !== newUserId && userStore.isLoggedIn) {
      console.log(`[Security] Detected user ID change: ${oldUserId} -> ${newUserId}`)
      await initialize()
    }
  })

  watch(currentSessionId, async (sessionId) => {
    if (sessionId && userStore.isLoggedIn) {
      const session = sessions.value.find(s => s.id === sessionId)
      if (session && (!session.messages || session.messages.length === 0)) {
        await loadSessionDetail(sessionId)
      }
    }
  })

  return {
    sessions,
    currentSessionId,
    currentSession,
    isLoading,
    getModeLabel,
    loadSessionsFromBackend,
    loadSessionDetail,
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
    syncLastMessageToBackend,
    initialize,
    clearAllLocalData,
  }
})
