import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { chatAPI } from '../api'
import { knowledgeAPI } from '../api'
import { useUserStore } from './user'
import { ElMessage, ElMessageBox } from 'element-plus'
import { logger } from '../utils/logger'
import {
  isApiSuccess,
  transformBackendSessionToFrontend,
  transformFrontendMessageToBackend,
  getModeLabel
} from '../utils/session-transformers'
import {
  getLastAssistantMessage,
  setLastMessageField,
  addLastMessageFieldItem,
  getMessageByIndex,
  setMessageField,
  addMessageFieldItem,
  appendToMessage,
  createMessageVersion,
  addOrUpdateToolCallInLastMessage,
  updateOrAddToolResultInLastMessage,
  addOrUpdateToolCallInMessageByIdx,
  updateOrAddToolResultInMessageByIdx,
} from '../utils/message-operations'

export const useSessionStore = defineStore('session', () => {
  const sessions = ref([])
  const currentSessionId = ref(null)
  const selectedKnowledgeBase = ref(null)
  const selectedKnowledgeBases = ref([])
  const knowledgeBases = ref([])
  const isLoading = ref(false)
  const lastLoadedUserId = ref(null)
  const paginationMeta = ref({
    total: 0,
    page: 1,
    pageSize: 20,
    totalPages: 1,
    hasMore: false,
  })

  const userStore = useUserStore()

  const _getLast = (sessionId) => getLastAssistantMessage(sessions.value, sessionId)
  const _setLastField = (sessionId, field, value) => setLastMessageField(sessions.value, sessionId, field, value)
  const _addLastFieldItem = (sessionId, field, item) => addLastMessageFieldItem(sessions.value, sessionId, field, item)
  const _getByIndex = (sessionId, idx) => getMessageByIndex(sessions.value, sessionId, idx)
  const _setField = (sessionId, idx, field, value) => setMessageField(sessions.value, sessionId, idx, field, value)
  const _addFieldItem = (sessionId, idx, field, item) => addMessageFieldItem(sessions.value, sessionId, idx, field, item)
  const _append = (sessionId, idx, content) => appendToMessage(sessions.value, sessionId, idx, content)

  const addSourceToMessage = (sessionId, messageIndex, source) => _addFieldItem(sessionId, messageIndex, 'sources', source)
  const setSourcesToMessage = (sessionId, messageIndex, sources) => _setField(sessionId, messageIndex, 'sources', sources)
  const setPlanToMessage = (sessionId, messageIndex, plan) => _setField(sessionId, messageIndex, 'plan', plan)
  const setChainOfThoughtToMessage = (sessionId, messageIndex, cot) => _setField(sessionId, messageIndex, 'chainOfThought', cot)
  const setReasoningToMessage = (sessionId, messageIndex, reasoning) => _setField(sessionId, messageIndex, 'reasoning', reasoning)
  const setSuggestionsToMessage = (sessionId, messageIndex, suggestions) => _setField(sessionId, messageIndex, 'suggestions', suggestions)
  const setContextToMessage = (sessionId, messageIndex, context) => _setField(sessionId, messageIndex, 'context', context)
  const addToolCallToMessage = (sessionId, messageIndex, toolCall) => _addFieldItem(sessionId, messageIndex, 'toolCalls', toolCall)
  const addOrUpdateToolCallToMessage = (sessionId, messageIndex, data) => addOrUpdateToolCallInMessageByIdx(sessions.value, sessionId, messageIndex, data)
  const updateOrAddToolResultToMessage = (sessionId, messageIndex, data) => updateOrAddToolResultInMessageByIdx(sessions.value, sessionId, messageIndex, data)

  const currentSession = computed(() => {
    return sessions.value.find(s => s.id === currentSessionId.value)
  })

  const clearAllLocalData = () => {
    sessions.value = []
    currentSessionId.value = null
    selectedKnowledgeBase.value = null
    selectedKnowledgeBases.value = []
    lastLoadedUserId.value = null
    logger.log('[Security] Cleared all local session data')
  }

  const _loadingPromise = { sessions: null, knowledgeBases: null }

  const loadSessionsFromBackend = async (page = 1) => {
    if (!userStore.isLoggedIn) {
      clearAllLocalData()
      return
    }
    // 防止重复并发调用
    if (page === 1 && _loadingPromise.sessions) {
      return _loadingPromise.sessions
    }

    const currentUserId = userStore.userInfo?.id

    if (lastLoadedUserId.value && lastLoadedUserId.value !== currentUserId) {
      logger.log(`[Security] User switched from ${lastLoadedUserId.value} to ${currentUserId}, clearing old data`)
      clearAllLocalData()
    }

    isLoading.value = true
    const promise = (async () => {
    try {
      const response = await chatAPI.getSessions({ page, page_size: paginationMeta.value.pageSize })

      if (isApiSuccess(response)) {
        const data = response.data.data

        let sessionList = []

        if (Array.isArray(data)) {
          sessionList = data
        } else if (data && typeof data === 'object') {
          sessionList = data.items || []
        }

        if (page <= 1) {
          sessions.value = sessionList
            .map(transformBackendSessionToFrontend)
            .filter(s => s !== null)
        } else {
          const newSessions = sessionList
            .map(transformBackendSessionToFrontend)
            .filter(s => s !== null)
          const existingIds = new Set(sessions.value.map(s => s.id))
          newSessions.forEach(s => { if (!existingIds.has(s.id)) sessions.value.push(s) })
        }

        if (data && typeof data === 'object' && !Array.isArray(data)) {
          paginationMeta.value = {
            total: data.total || 0,
            page: data.page || page,
            pageSize: data.page_size || paginationMeta.value.pageSize,
            totalPages: data.total_pages || 1,
            hasMore: (data.page || page) < (data.total_pages || 1),
          }
        } else {
          paginationMeta.value = {
            total: sessions.value.length,
            page: 1,
            pageSize: paginationMeta.value.pageSize,
            totalPages: 1,
            hasMore: false,
          }
        }

        lastLoadedUserId.value = currentUserId

        if (sessions.value.length > 0) {
          if (!currentSessionId.value || !sessions.value.find(s => s.id === currentSessionId.value)) {
            currentSessionId.value = sessions.value[0].id
          }
        } else {
          currentSessionId.value = null
        }

        logger.log(`[Security] Loaded ${sessions.value.length} sessions (page ${paginationMeta.value.page}/${paginationMeta.value.totalPages}) for user ${currentUserId}`)
      }
    } catch (error) {
      logger.error('Failed to load sessions from backend:', error)
      ElMessage.error('加载会话失败')
    } finally {
      isLoading.value = false
      if (page === 1) _loadingPromise.sessions = null
    }
    })()
    if (page === 1) _loadingPromise.sessions = promise
    return promise
  }

  const loadMoreSessions = async () => {
    if (isLoading.value || !paginationMeta.value.hasMore) return
    await loadSessionsFromBackend(paginationMeta.value.page + 1)
  }

  const loadSessionDetail = async (sessionId, { forceRefresh = false } = {}) => {
    if (!userStore.isLoggedIn || !sessionId) return null

    if (!forceRefresh) {
      const existing = sessions.value.find(s => s.id === sessionId)
      if (existing && existing.messages && existing.messages.length > 0) {
        return existing
      }
    }

    try {
      const response = await chatAPI.getSession(sessionId)
      
      if (isApiSuccess(response) && response.data.data) {
        const detailData = transformBackendSessionToFrontend(response.data.data)
        
        const index = sessions.value.findIndex(s => s.id === sessionId)
        if (index !== -1) {
          sessions.value[index] = detailData
        }
        logger.log(`[Session] Loaded detail for session ${sessionId} with ${detailData.messages?.length || 0} messages`)
        return detailData
      }
    } catch (error) {
      logger.error('Failed to load session detail:', error)
    }
    return null
  }

  const createNewSession = async (mode = 'agent', title) => {
    if (!userStore.isLoggedIn) {
      ElMessage.warning('请先登录后再使用对话功能')
      return null
    }

    try {
      const response = await chatAPI.createSession({
        title: title || `新对话 - ${getModeLabel(mode)}`,
        mode: mode
      })

      if (isApiSuccess(response)) {
        const newSession = transformBackendSessionToFrontend(response.data.data)
        sessions.value.unshift(newSession)
        currentSessionId.value = newSession.id
        logger.log(`[Security] Created new session ${newSession.id} for user ${userStore.userInfo?.id}`)
        return newSession
      }
    } catch (error) {
      logger.error('Failed to create session:', error)
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
      // 恢复知识库选择状态
      if (session?.selectedKnowledgeBase) {
        selectedKnowledgeBase.value = session.selectedKnowledgeBase
      } else {
        selectedKnowledgeBase.value = null
      }
      if (session?.selectedKnowledgeBases) {
        setSelectedKnowledgeBases(session.selectedKnowledgeBases)
      } else {
        selectedKnowledgeBases.value = []
      }
    }
  }

  const deleteSession = async (sessionId) => {
    const index = sessions.value.findIndex(s => s.id === sessionId)
    if (index !== -1) {
      if (userStore.isLoggedIn) {
        try {
          const response = await chatAPI.deleteSession(sessionId)
          const resData = response.data?.data || response.data
          if (resData?.has_linked_data) {
            const linkedTasks = resData.linked_research_tasks || resData.linked_tasks || []
            const taskList = linkedTasks.map(t => t.query || t.user_question || t.task_id).join('、')
            const count = linkedTasks.length
            await ElMessageBox.confirm(
              `该会话关联了 ${count} 个深度研究任务及文件，删除后将无法恢复。关联任务：[${taskList}]`,
              '',
              { confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning' }
            )
            await chatAPI.deleteSession(sessionId, { confirm_delete_linked: 'true' })
          }
          logger.log(`[Security] Deleted session ${sessionId} for user ${userStore.userInfo?.id}`)
        } catch (error) {
          if (error === 'cancel' || error?.toString?.().includes('cancel')) return
          logger.error('Failed to delete session from backend:', error)
          throw error
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
          logger.error('Failed to update session on backend:', error)
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
        versions: [createMessageVersion(message)],
        currentVersion: 0
      }
      session.messages.push(messageWithVersions)
      session.messageCount = session.messages.length

      if (saveToBackend && userStore.isLoggedIn) {
        const backendMsg = transformFrontendMessageToBackend(messageWithVersions)
        chatAPI.addMessage(sessionId, backendMsg).then(res => {
          if (isApiSuccess(res) && res.data.data?.id) {
            messageWithVersions.backendId = res.data.data.id
          }
        }).catch(error => {
          logger.error('Failed to save message to backend:', error)
        })
      }
    }
  }

  // 防止 syncLastMessageToBackend 并发调用导致创建重复消息
  const _syncLocks = new Map()

  const syncLastMessageToBackend = async (sessionId) => {
    if (!userStore.isLoggedIn) return

    // 防止并发：如果已有同步操作在进行，等待它完成
    if (_syncLocks.get(sessionId)) {
      await _syncLocks.get(sessionId)
      return
    }

    const promise = (async () => {
      const session = sessions.value.find(s => s.id === sessionId)
      if (!session || session.messages.length === 0) return

      const lastMessage = session.messages[session.messages.length - 1]
      if (!lastMessage.backendId) {
        const backendMsg = transformFrontendMessageToBackend(lastMessage)
        try {
          const res = await chatAPI.addMessage(sessionId, backendMsg)
          if (isApiSuccess(res) && res.data.data?.id) {
            lastMessage.backendId = res.data.data.id
          }
        } catch (error) {
          logger.error('Failed to sync message to backend:', error)
        }
      } else {
        const backendMsg = transformFrontendMessageToBackend(lastMessage)
        try {
          await chatAPI.updateMessage(lastMessage.backendId, backendMsg)
        } catch (error) {
          logger.error('Failed to update message in backend:', error)
        }
      }
    })()

    _syncLocks.set(sessionId, promise)
    try {
      await promise
    } finally {
      _syncLocks.delete(sessionId)
    }
  }

  const addVersionToMessage = (sessionId, messageIndex, version) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages[messageIndex]) {
      const message = session.messages[messageIndex]
      if (!message.versions) {
        message.versions = [createMessageVersion(message)]
        message.currentVersion = 0
      }
      message.versions.push(version)
      message.currentVersion = message.versions.length - 1
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
        message.suggestions = version.suggestions
        message.context = version.context
        message.attachmentIds = version.attachmentIds || message.attachmentIds
        touchSessionUpdatedAt(sessionId)

        if (userStore.isLoggedIn && message.backendId) {
          const backendMsg = transformFrontendMessageToBackend(message)
          chatAPI.updateMessage(message.backendId, backendMsg).catch(error => {
            logger.error('Failed to sync version switch to backend:', error)
          })
        }
      }
    }
  }

  const updateLastMessage = (sessionId, content) => {
    const result = _getLast(sessionId)
    if (!result) return
    result.message.content = content
    const ver = result.message.versions?.[result.message.currentVersion]
    if (ver) ver.content = content
  }

  const appendToLastMessage = (sessionId, content) => {
    const result = _getLast(sessionId)
    if (!result) return
    result.message.content = (result.message.content || '') + content
    const ver = result.message.versions?.[result.message.currentVersion]
    if (ver) ver.content = result.message.content
  }

  const addSourceToLastMessage = (sessionId, source) => _addLastFieldItem(sessionId, 'sources', source)
  const setSourcesToLastMessage = (sessionId, sources) => _setLastField(sessionId, 'sources', sources)
  const setPlanToLastMessage = (sessionId, plan) => _setLastField(sessionId, 'plan', plan)
  const setChainOfThoughtToLastMessage = (sessionId, cot) => _setLastField(sessionId, 'chainOfThought', cot)
  const setReasoningToLastMessage = (sessionId, reasoning) => _setLastField(sessionId, 'reasoning', reasoning)
  const setSuggestionsToLastMessage = (sessionId, suggestions) => _setLastField(sessionId, 'suggestions', suggestions)
  const setContextToLastMessage = (sessionId, context) => _setLastField(sessionId, 'context', context)
  const setResearchTaskIdToLastMessage = (sessionId, researchTaskId) => _setLastField(sessionId, 'researchTaskId', researchTaskId)
  const setApprovalToLastMessage = (sessionId, approval) => {
    _setLastField(sessionId, 'approval', approval)
    _setLastField(sessionId, 'approvalState', approval?.state || 'pending')
  }

  const appendToLastAssistantMessage = (sessionId, content) => {
    const result = getLastAssistantMessage(sessions.value, sessionId)
    if (!result) return
    result.message.content = (result.message.content || '') + content
    const ver = result.message.versions?.[result.message.currentVersion]
    if (ver) ver.content = result.message.content
  }

  const addToolCallToLastMessage = (sessionId, toolCall) => _addLastFieldItem(sessionId, 'toolCalls', toolCall)
  const addOrUpdateToolCallToLastMessage = (sessionId, data) => addOrUpdateToolCallInLastMessage(sessions.value, sessionId, data)
  const updateOrAddToolResultToLastMessage = (sessionId, data) => updateOrAddToolResultInLastMessage(sessions.value, sessionId, data)

  const setUsageToLastMessage = (sessionId, usageData) => {
    const result = getLastAssistantMessage(sessions.value, sessionId)
    if (!result) return
    if (usageData.model !== undefined) result.message.model = usageData.model
    if (usageData.tokenCount !== undefined) result.message.tokenCount = usageData.tokenCount
    if (usageData.tokens !== undefined) result.message.tokens = usageData.tokens
    if (usageData.tokenDetail !== undefined) result.message.tokenDetail = usageData.tokenDetail
    if (usageData.responseTime !== undefined) result.message.responseTime = usageData.responseTime
  }

  const setAttachmentIdsToLastUserMessage = (sessionId, attachmentIds) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session) return
    for (let i = session.messages.length - 1; i >= 0; i--) {
      if (session.messages[i].role === 'user') {
        session.messages[i].attachmentIds = attachmentIds
        const ver = session.messages[i].versions?.[session.messages[i].currentVersion]
        if (ver) ver.attachmentIds = attachmentIds
        break
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

  const removeMessageFromSession = (sessionId, messageId) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session) return
    const idx = session.messages.findIndex(m => m.id === messageId)
    if (idx !== -1) {
      session.messages.splice(idx, 1)
      session.messageCount = session.messages.length
      session.updatedAt = Date.now()
    }
  }

  const removeMessagePairFromSession = (sessionId, userMessageId) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session) return
    const userIdx = session.messages.findIndex(m => m.id === userMessageId)
    if (userIdx === -1) return
    session.messages.splice(userIdx, 2)
    session.messageCount = session.messages.length
    session.updatedAt = Date.now()
  }

  const setSelectedKnowledgeBase = (kbIdOrObj) => {
    if (!kbIdOrObj) {
      selectedKnowledgeBase.value = null
      return
    }
    if (typeof kbIdOrObj === 'object') {
      selectedKnowledgeBase.value = kbIdOrObj
    } else {
      const found = knowledgeBases.value.find(kb => kb.id === kbIdOrObj)
      selectedKnowledgeBase.value = found || { id: kbIdOrObj }
    }
  }

  const setSelectedKnowledgeBases = (kbIds) => {
    if (!kbIds || !Array.isArray(kbIds)) {
      selectedKnowledgeBases.value = []
      return
    }
    selectedKnowledgeBases.value = kbIds.map(id => {
      const existing = selectedKnowledgeBases.value.find(kb => kb.id === id)
      if (existing) return existing
      const kb = knowledgeBases.value.find(k => k.id === id)
      return kb ? { id: kb.id, name: kb.name } : { id, name: id }
    })
  }

  const loadKnowledgeBases = async () => {
    // 防止重复并发调用
    if (_loadingPromise.knowledgeBases) {
      return _loadingPromise.knowledgeBases
    }
    const promise = (async () => {
    try {
      const response = await knowledgeAPI.getKnowledgeBases()
      if (response.data?.code === 200 && Array.isArray(response.data.data)) {
        knowledgeBases.value = response.data.data
      } else if (response.data?.data?.items) {
        knowledgeBases.value = response.data.data.items
      }
      if (selectedKnowledgeBase.value?.id) {
        const stillExists = knowledgeBases.value.some(kb => kb.id === selectedKnowledgeBase.value.id)
        if (!stillExists) {
          selectedKnowledgeBase.value = null
        }
      }
      if (selectedKnowledgeBases.value.length > 0) {
        selectedKnowledgeBases.value = selectedKnowledgeBases.value.filter(
          kb => knowledgeBases.value.some(k => k.id === (kb.id || kb))
        )
      }
    } catch (error) {
      logger.error('Failed to load knowledge bases:', error)
    } finally {
      _loadingPromise.knowledgeBases = null
    }
    })()
    _loadingPromise.knowledgeBases = promise
    return promise
  }

  const initialize = async () => {
    logger.log('[Security] Initializing session store...')
    if (userStore.isLoggedIn) {
      await Promise.all([loadSessionsFromBackend(), loadKnowledgeBases()])
    } else {
      clearAllLocalData()
    }
  }

  watch(() => userStore.isLoggedIn, async (newVal, oldVal) => {
    if (oldVal && !newVal) {
      logger.log('[Security] User logged out, clearing all session data')
      clearAllLocalData()
    } else if (!oldVal && newVal) {
      logger.log('[Security] User logged in, loading sessions from backend')
      await initialize()
    } else if (oldVal && newVal) {
      const oldUserId = lastLoadedUserId.value
      const newUserId = userStore.userInfo?.id
      if (oldUserId && newUserId && oldUserId !== newUserId) {
        logger.log(`[Security] User account changed from ${oldUserId} to ${newUserId}`)
        await initialize()
      }
    }
  })

  watch(() => userStore.userInfo?.id, async (newUserId, oldUserId) => {
    if (oldUserId && newUserId && oldUserId !== newUserId && userStore.isLoggedIn) {
      logger.log(`[Security] Detected user ID change: ${oldUserId} -> ${newUserId}`)
      await initialize()
    }
  })

  const touchSessionUpdatedAt = (sessionId) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session) session.updatedAt = Date.now()
  }

  return {
    sessions,
    currentSessionId,
    selectedKnowledgeBase,
    selectedKnowledgeBases,
    knowledgeBases,
    isLoading,
    paginationMeta,
    currentSession,
    loadSessionsFromBackend,
    loadMoreSessions,
    loadSessionDetail,
    createNewSession,
    switchSession,
    deleteSession,
    updateSession,
    updateSessionTitle,
    addMessageToSession,
    syncLastMessageToBackend,
    addVersionToMessage,
    switchMessageVersion,
    updateLastMessage,
    appendToLastMessage,
    appendToMessage: _append,
    addSourceToLastMessage,
    setSourcesToLastMessage,
    setPlanToLastMessage,
    setChainOfThoughtToLastMessage,
    setReasoningToLastMessage,
    setSuggestionsToLastMessage,
    setContextToLastMessage,
    setResearchTaskIdToLastMessage,
    setApprovalToLastMessage,
    appendToLastAssistantMessage,
    addToolCallToLastMessage,
    addOrUpdateToolCallToLastMessage,
    updateOrAddToolResultToLastMessage,
    setUsageToLastMessage,
    setAttachmentIdsToLastUserMessage,
    addSourceToMessage,
    setSourcesToMessage,
    setPlanToMessage,
    setChainOfThoughtToMessage,
    setReasoningToMessage,
    setSuggestionsToMessage,
    setContextToMessage,
    addToolCallToMessage,
    addOrUpdateToolCallToMessage,
    updateOrAddToolResultToMessage,
    getSessionMessages,
    removeMessagesFromIndex,
    clearCurrentSessionMessages,
    removeMessageFromSession,
    removeMessagePairFromSession,
    setSelectedKnowledgeBase,
    setSelectedKnowledgeBases,
    loadKnowledgeBases,
    initialize,
    clearAllLocalData,
    touchSessionUpdatedAt,
  }
})
