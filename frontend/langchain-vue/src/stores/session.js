import { defineStore } from 'pinia'
import { ref, computed, watch, triggerRef } from 'vue'
import router from '../router'
import { chatAPI } from '@/api/chat'
import { knowledgeAPI } from '@/api/knowledge'
import { useUserStore } from './user'
import { ElMessage, ElMessageBox } from 'element-plus'
import { logger } from '../utils/logger'
import { getModeLabel, getQueryParam } from '../utils/format'
import {
  isApiSuccess,
  transformBackendSessionToFrontend,
  transformFrontendMessageToBackend,
} from '../utils/sessionTransformers'
import {
  getLastAssistantMessage,
  setLastMessageField,
  addLastMessageFieldItem,
  getMessageByIndex,
  setMessageField,
  addMessageFieldItem,
  appendToMessage,
  createMessageVersion,
  addOrUpdateToolCallInMessageByIdx,
  updateOrAddToolResultInMessageByIdx,
  findToolCallById,
  getInterruptId,
  // Map 版本工具函数（与 researchStore 共用，toolCallMap 作为唯一真相源）
  addOrUpdateToolCallInMap,
  updateOrAddToolResultInMap,
  setApprovalToToolCallInMap,
  updateApprovalStateInMap,
  updateToolCallStatusInMap,
  finalizeToolCallsInMap,
  findToolCallInMap,
  flushPendingApprovalsInMap,
  isTerminalStatus,
  mergeMessageFromBackend,
} from '../utils/messageOperations'
import { StreamState, ToolCallStatus } from '../types'

/** localStorage key：持久化 currentSessionId，防止刷新后丢失（Task 15 P0 修复） */
const CURRENT_SESSION_ID_KEY = 'lc_current_session_id'

/** 安全读取 localStorage（Node 测试环境可能不存在） */
const _safeGetLocalStorage = (key) => {
  try { return typeof localStorage !== 'undefined' ? localStorage.getItem(key) : null }
  catch { return null }
}
/** 安全写入 localStorage */
const _safeSetLocalStorage = (key, value) => {
  try { if (typeof localStorage !== 'undefined') localStorage.setItem(key, value) }
  catch { /* 静默忽略 */ }
}
/** 安全移除 localStorage */
const _safeRemoveLocalStorage = (key) => {
  try { if (typeof localStorage !== 'undefined') localStorage.removeItem(key) }
  catch { /* 静默忽略 */ }
}

export const useSessionStore = defineStore('session', () => {
  const sessions = ref([])
  // 刷新后从 localStorage 恢复 currentSessionId，避免 loadSessionsFromBackend 因
  // currentSessionId 为空 fallback 到 sessions[0]（按加载顺序，非真正最新的会话）
  const _savedSessionId = _safeGetLocalStorage(CURRENT_SESSION_ID_KEY)
  const currentSessionId = ref(_savedSessionId || null)
  const selectedKnowledgeBase = ref(null)
  const selectedKnowledgeBases = ref([])
  const knowledgeBases = ref([])
  const isLoading = ref(false)
  const lastLoadedUserId = ref(null)
  /** 乐观删除 ID 集合：防止 session_deleted WS 事件与 HTTP 删除重复操作（Task 5 修复） */
  const deletedSessionIds = ref(new Set())
  const paginationMeta = ref({
    total: 0,
    page: 1,
    pageSize: 20,
    totalPages: 1,
    hasMore: false,
  })

  // 持久化 currentSessionId（Task 15 P0 修复）：
  // 刷新后从 localStorage 恢复，避免 loadSessionsFromBackend fallback 到 sessions[0]
  // 选中错误会话。newId 为空时移除条目，防止跨用户残留。
  watch(currentSessionId, (newId) => {
    if (newId) {
      _safeSetLocalStorage(CURRENT_SESSION_ID_KEY, newId)
    } else {
      _safeRemoveLocalStorage(CURRENT_SESSION_ID_KEY)
    }
  })

  // ==================== 工具调用状态（Map 为唯一真相源） ====================
  //
  // 设计与 researchStore 对齐：toolCallMap 作为唯一真相源，message.toolCalls
  // 数组通过 _syncMessageToolCalls 派生（单向数据流：Map → message.toolCalls）。
  //
  // 数据结构：
  //   toolCallsMap: Map<sessionId, Map<toolCallId, toolCall>>
  //   pendingApprovals: Map<sessionId, Map<toolCallId, {approvalData, toolCallId}>>
  //
  // 时序保护：审批事件先于 tool 事件到达时，setApprovalToToolCall 创建 isSynthetic
  // 占位条目并加入 pendingApprovals 队列；后续 tool 事件到达时通过
  // flushPendingApprovals 绑定审批数据到真实 toolCall。

  /** @type {import('vue').Ref<Set<string>>} */
  const optimisticSessionIds = ref(new Set())

  /** @type {import('vue').Ref<Map<string, Map<string, Object>>>} */
  const toolCallsMap = ref(new Map())
  /** @type {import('vue').Ref<Map<string, Map<string, {approvalData: Object, toolCallId: string}>>>} */
  const pendingApprovals = ref(new Map())

  const userStore = useUserStore()

  /** 乐观会话去重：非触发浏览器收到 session_created 后调用，命中则跳过 subscribeSession + currentSessionId 切换 */
  const consumeOptimisticSession = (sessionId) => {
    if (!sessionId || !optimisticSessionIds.value.has(sessionId)) return false
    optimisticSessionIds.value.delete(sessionId)
    optimisticSessionIds.value = new Set(optimisticSessionIds.value)
    return true
  }

  /** 按 ID 查找会话 */
  const _findSession = (sessionId) => sessions.value.find(s => s.id === sessionId)

  const _getLast = (sessionId) => getLastAssistantMessage(sessions.value, sessionId)
  const _setLastField = (sessionId, field, value) => setLastMessageField(sessions.value, sessionId, field, value)
  const _addLastFieldItem = (sessionId, field, item) => addLastMessageFieldItem(sessions.value, sessionId, field, item)
  const _getByIndex = (sessionId, idx) => getMessageByIndex(sessions.value, sessionId, idx)
  const _setField = (sessionId, idx, field, value) => setMessageField(sessions.value, sessionId, idx, field, value)
  const _addFieldItem = (sessionId, idx, field, item) => addMessageFieldItem(sessions.value, sessionId, idx, field, item)
  const _append = (sessionId, idx, content) => appendToMessage(sessions.value, sessionId, idx, content)

  /**
   * 获取或创建指定 session 的 toolCallMap
   * @param {string} sessionId - 会话 ID
   * @returns {Map<string, Object>} toolCallMap
   */
  const _ensureToolCallsMap = (sessionId) => {
    if (!sessionId) return null
    if (!toolCallsMap.value.has(sessionId)) {
      toolCallsMap.value.set(sessionId, new Map())
      triggerRef(toolCallsMap)
    }
    return toolCallsMap.value.get(sessionId)
  }

  /**
   * 获取或创建指定 session 的 pendingApprovalsMap
   * @param {string} sessionId - 会话 ID
   * @returns {Map<string, {approvalData: Object, toolCallId: string}>} pendingApprovalsMap
   */
  const _ensurePendingApprovalsMap = (sessionId) => {
    if (!sessionId) return null
    if (!pendingApprovals.value.has(sessionId)) {
      pendingApprovals.value.set(sessionId, new Map())
      triggerRef(pendingApprovals)
    }
    return pendingApprovals.value.get(sessionId)
  }

  /**
   * 将 API 加载的消息中的 toolCalls 初始化到 toolCallsMap
   *
   * loadSessionDetail 从后端加载完整消息后，message.toolCalls 已有完整数据，
   * 但 toolCallsMap 为空。若后续 WebSocket 事件先于全量 tool_call_* 事件
   * 触发 _syncMessageToolCalls，会把 message.toolCalls 替换为 toolCallsMap
   * 的不完整子集（P27）。
   *
   * 此函数建立双向一致性：将 message.toolCalls 回填到 toolCallsMap。
   * 仅填充尚不存在的条目，不覆盖 WebSocket 已写入的动态字段。
   *
   * @param {string} sessionId - 会话 ID
   * @param {Array} messages - 消息列表（来自 API）
   */
  const _syncToolCallsMapFromMessages = (sessionId, messages) => {
    if (!sessionId || !Array.isArray(messages)) return
    let targetMap = toolCallsMap.value.get(sessionId)
    if (!targetMap) {
      targetMap = new Map()
      toolCallsMap.value.set(sessionId, targetMap)
    }
    for (const msg of messages) {
      if (msg.role !== 'assistant' || !Array.isArray(msg.toolCalls)) continue
      for (const tc of msg.toolCalls) {
        const key = tc.toolCallId || tc.id
        if (!key || targetMap.has(key)) continue
        // 以浅拷贝创建条目，同时标注所属消息 backendId
        targetMap.set(key, { ...tc, messageBackendId: msg.backendId?.toString() })
      }
    }
  }

  /**
   * 从 toolCallMap 收集应同步到目标消息的 toolCall 数组（唯一归属规则，两处同步共用）
   *
   * 归属规则（Task 5.4，_syncMessageToolCalls 与 _syncAllMessageToolCallsFromMap 统一）：
   * - 有 messageBackendId 的条目仅归属到匹配消息
   * - 无 messageBackendId 的条目仅归属到最后一条 assistant 消息
   *   （禁止复制到每条 assistant 消息，避免工具卡片迁移）
   *
   * @param {Map} toolCallMap - 该 session 的 toolCall Map
   * @param {Object} targetMsg - 目标消息
   * @param {boolean} isLast - targetMsg 是否为 session 中最后一条 assistant 消息
   * @returns {Array} 归属到该消息的 toolCall 数组
   */
  const _collectToolCallsForMessage = (toolCallMap, targetMsg, isLast) => {
    const backendId = targetMsg.backendId?.toString()
    const arr = []
    for (const tc of toolCallMap.values()) {
      if (tc.messageBackendId) {
        if (tc.messageBackendId === backendId) arr.push(tc)
      } else if (isLast) {
        arr.push(tc)  // 无归属的兜底到最后一个 assistant
      }
    }
    return arr
  }

  /**
   * 将 toolCallMap 同步到 messages 数组（最后一条 assistant 消息的 toolCalls）
   *
   * 单向数据流：Map → message.toolCalls（派生）。
   * 修改 toolCallMap 内对象属性后必须调用，确保 UI 刷新。
   *
   * 同步范围：
   * - 最后一条 assistant 消息的 toolCalls
   * - 当前 version（versions[currentVersion]）的 toolCalls
   *
   * @param {string} sessionId - 会话 ID
   * @param {Object} [message] - 可选，指定目标消息；不传则回退到最后一条 assistant
   */
  const _syncMessageToolCalls = (sessionId, message) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session || session.messages.length === 0) return
    const targetMsg = message || session.messages[session.messages.length - 1]
    if (!targetMsg || targetMsg.role !== 'assistant') return
    const toolCallMap = toolCallsMap.value.get(sessionId)
    if (!toolCallMap) {
      // 兜底保护：toolCallMap 未初始化时，不清空已有 toolCalls 数据
      // （刷新后 API 加载的 toolCalls 已在 message.toolCalls 中，等待
      //  loadSessionDetail → _syncToolCallsMapFromMessages 回填 Map）
      return
    }
    const isLast = _isLastAssistantMessage(sessionId, targetMsg)
    // 归属规则统一走 _collectToolCallsForMessage（Task 5.4）
    const arr = _collectToolCallsForMessage(toolCallMap, targetMsg, isLast)
    // 按 LLM 原始生成序号稳定排序（跨浏览器工具顺序一致性保障）
    // _index 由后端 stream_chunk_processors 在解析 AIMessage.tool_calls 时标注
    if (arr.length > 1) {
      arr.sort((a, b) => {
        const ai = a._index ?? 999
        const bi = b._index ?? 999
        return ai - bi
      })
    }
    targetMsg.toolCalls = arr
    const ver = targetMsg.versions?.[targetMsg.currentVersion]
    if (ver) ver.toolCalls = arr
    // 同步完成后清理其他 assistant 消息上残留的 toolCalls（避免旧消息保留审批 UI）
    for (const msg of session.messages) {
      if (msg !== targetMsg && msg.role === 'assistant' && msg.toolCalls?.length > 0) {
        const stillValid = msg.toolCalls.some(tc => {
          const mapTc = toolCallMap.get(tc.id)
          return mapTc && (!mapTc.messageBackendId || mapTc.messageBackendId === msg.backendId?.toString())
        })
        if (!stillValid) msg.toolCalls = []
      }
    }
  }

  /** 判断消息是否为 session 中最后一条 assistant */
  const _isLastAssistantMessage = (sessionId, msg) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session) return false
    for (let i = session.messages.length - 1; i >= 0; i--) {
      if (session.messages[i].role === 'assistant') {
        return session.messages[i] === msg
      }
    }
    return false
  }

  const addSourceToMessage = (sessionId, messageIndex, source) => _addFieldItem(sessionId, messageIndex, 'sources', source)
  const setSourcesToMessage = (sessionId, messageIndex, sources) => _setField(sessionId, messageIndex, 'sources', sources)
  const setPlanToMessage = (sessionId, messageIndex, plan) => _setField(sessionId, messageIndex, 'plan', plan)
  const setChainOfThoughtToMessage = (sessionId, messageIndex, cot) => _setField(sessionId, messageIndex, 'chainOfThought', cot)
  const setReasoningToMessage = (sessionId, messageIndex, reasoning) => _setField(sessionId, messageIndex, 'reasoning', reasoning)
  const setSuggestionsToMessage = (sessionId, messageIndex, suggestions) => _setField(sessionId, messageIndex, 'suggestions', suggestions)
  const setContextToMessage = (sessionId, messageIndex, context) => _setField(sessionId, messageIndex, 'context', context)
  const addToolCallToMessage = (sessionId, messageIndex, toolCall) => _addFieldItem(sessionId, messageIndex, 'toolCalls', toolCall)
  // 数组版入口（按消息索引写入）：仅保留给重新生成 SSE 流等"目标消息非最后一条 assistant"
  // 的场景（chat.js regenerateMessage 调用）。合并/保护逻辑已与 Map 版共用
  // _mergeExistingToolCall / _findMatchingToolCall，不再存在双实现漂移（Task 5.5）。
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
    // 同步清理工具调用与待绑定审批数据（Map 为唯一真相源）
    toolCallsMap.value = new Map()
    pendingApprovals.value = new Map()
    deletedSessionIds.value = new Set()
    // 清理 currentSessionId 持久化（Task 15 P0 修复）：登出/切换用户时清除，
    // 避免残留的会话 ID 被下一个用户恢复，导致跨用户会话串扰
    _safeRemoveLocalStorage(CURRENT_SESSION_ID_KEY)
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
      const response = await chatAPI.getSessions({ page, pageSize: paginationMeta.value.pageSize })

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
            pageSize: data.pageSize || paginationMeta.value.pageSize,
            totalPages: data.totalPages || 1,
            hasMore: (data.page || page) < (data.totalPages || 1),
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
          // 会话定位策略（页面加载时执行一次，SPA 导航不触发 initialize，不受影响）：
          // - URL 带 session_id（深度研究跳转/分享链接）：ChatView onMounted 会按 query 设置
          //   currentSessionId，此处必须保留任何切换，避免竞态覆盖 URL 指定会话（URL 权威）。
          // - 刷新（navigation type=reload，Task 15 P0）：恢复 localStorage 记住的上次会话；
          //   若恢复失败（会话已删除/不存在），fallback 到 updatedAt 最新会话。
          // - 新打开 /chat（navigate/back_forward，URL 无 session_id）：定位 updatedAt 最新会话，
          //   而非 localStorage 里的旧会话（用户期望「打开即最新对话」）。
          const urlSessionId = getQueryParam(router.currentRoute.value, 'session_id')
          const isPageReload = typeof performance !== 'undefined'
            && performance.getEntriesByType?.('navigation')[0]?.type === 'reload'
          const restoredId = currentSessionId.value
          const restoredExists = restoredId && sessions.value.find(s => s.id === restoredId)
          if (!urlSessionId && !isPageReload && restoredExists) {
            // 新打开 /chat：忽略 localStorage 旧会话，定位 updatedAt 最新会话
            const latest = sessions.value.reduce((a, b) =>
              (b.updatedAt || 0) > (a.updatedAt || 0) ? b : a
            )
            currentSessionId.value = latest.id
            logger.log(`[Session] 新打开页面，定位最新会话: ${latest.id}（忽略 localStorage: ${restoredId}）`)
          } else if (!urlSessionId && !isPageReload && !restoredExists) {
            // 新打开 /chat 且无有效恢复值：同样定位最新
            const latest = sessions.value.reduce((a, b) =>
              (b.updatedAt || 0) > (a.updatedAt || 0) ? b : a
            )
            currentSessionId.value = latest.id
            logger.log(`[Session] 新打开页面（无恢复值），定位最新会话: ${latest.id}`)
          } else if (!urlSessionId && isPageReload && !restoredExists) {
            // 刷新但恢复失败（会话已删除），fallback 到最新会话
            const latest = sessions.value.reduce((a, b) =>
              (b.updatedAt || 0) > (a.updatedAt || 0) ? b : a
            )
            currentSessionId.value = latest.id
            logger.log(`[Session] 刷新恢复失败，fallback 到最新会话: ${latest.id}`)
          }
          // 刷新且恢复成功 / URL 指定会话：保留，不做切换
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
      // Task 15 P0 修复：当本地仅用户消息无 AI 消息时，不短路返回，
      // 强制从后端加载（刷新后恢复了 currentSessionId 但后端可能有完整 AI 回复）
      const hasAssistantMessages = existing?.messages?.some(m => m.role === 'assistant')
      // F3-D：检查是否存在疑似深度研究消息但缺少 researchTaskId，
      // 若有则仍需触发详情请求以恢复丢失的 researchTaskId
      const hasResearchContext = existing?.messages?.some(m => m.researchContext != null)
      const hasAnyResearchTaskId = existing?.messages?.some(m => m.researchTaskId != null)
      const needsResearchReload = hasResearchContext && !hasAnyResearchTaskId
      if (existing && existing.messages && existing.messages.length > 0 && hasAssistantMessages && !needsResearchReload) {
        return existing
      }
    }

    try {
      const response = await chatAPI.getSession(sessionId)
      
      if (isApiSuccess(response) && response.data.data) {
        const detailData = transformBackendSessionToFrontend(response.data.data)
        
        const index = sessions.value.findIndex(s => s.id === sessionId)
        if (index !== -1) {
          const existingSession = sessions.value[index]
          if (existingSession && existingSession.messages?.length > 0) {
            // 逐消息合并：API 数据是权威快照，但保留本地保护态数据（流式中的 content/streamState 等）
            const apiMessages = detailData.messages
            for (const apiMsg of apiMessages) {
              const existingMsg = existingSession.messages.find(m =>
                String(m.backendId) === String(apiMsg.backendId)
              )
              if (existingMsg) {
                mergeMessageFromBackend(existingMsg, apiMsg)
              } else {
                existingSession.messages.push(apiMsg)
              }
            }
            existingSession.title = detailData.title
            existingSession.mode = detailData.mode
            existingSession.messageCount = detailData.messageCount
            existingSession.updatedAt = detailData.updatedAt
          } else {
            sessions.value[index] = detailData
          }
          // 同步 API 加载的 toolCalls 到 toolCallsMap
          _syncToolCallsMapFromMessages(sessionId, existingSession?.messages || detailData.messages || [])
          // 反向同步：将 Map 中的完整状态回写到 messages 数组
          _syncAllMessageToolCallsFromMap(sessionId)
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
        // 使用 upsertSession 而非 unshift：避免与 WebSocket session_created 事件重复添加
        // 竞态：WebSocket 事件可能比 HTTP 响应更早到达前端，导致 store 中已存在该 session
        // upsertSession 的 findIndex 会命中已有项，仅更新元数据，不会重复添加
        const newSession = upsertSession(response.data.data)
        if (!newSession) {
          logger.error('createNewSession: upsertSession returned null', response.data)
          ElMessage.error('创建会话失败：数据格式异常')
          return null
        }
        currentSessionId.value = newSession.id
        // 乐观标记：触发浏览器标记已创建，WS session_created 到达时 consume 跳过重复处理
        optimisticSessionIds.value.add(newSession.id)
        triggerRef(optimisticSessionIds)  // 确保 Vue 响应式感知 Set 变更
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
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session) return

    // 标记乐观删除 ID（Task 5 修复）：防止 WebSocket session_deleted 事件
    // 与 HTTP 删除重复操作导致的误删/重复侧边栏移除
    deletedSessionIds.value.add(sessionId)

    if (userStore.isLoggedIn) {
      try {
        const response = await chatAPI.deleteSession(sessionId)
        const resData = response.data?.data || response.data
        if (resData?.linkedResearchPreserved) {
          ElMessage.info(`关联的深度研究将保留在独立模块中（${resData.linkedResearchCount || ''}个任务）`)
        }
        logger.log(`[Security] Deleted session ${sessionId} for user ${userStore.userInfo?.id}`)
      } catch (error) {
        // HTTP 失败时回滚乐观删除标记
        deletedSessionIds.value.delete(sessionId)
        if (error === 'cancel' || error?.toString?.().includes('cancel')) return
        logger.error('Failed to delete session from backend:', error)
        throw error
      }
    }

    // 用 filter 按 session_id 过滤移除（非按 index splice，避免 await 后 index 过期误删相邻会话）
    sessions.value = sessions.value.filter(s => s.id !== sessionId)
    // 清理该会话关联的工具调用与待绑定审批数据（Map 为唯一真相源）
    if (toolCallsMap.value.has(sessionId)) {
      toolCallsMap.value.delete(sessionId)
      triggerRef(toolCallsMap)
    }
    if (pendingApprovals.value.has(sessionId)) {
      pendingApprovals.value.delete(sessionId)
      triggerRef(pendingApprovals)
    }
    // 清理该会话关联的审批条目（延迟导入避免循环依赖）
    try {
      const { useApprovalStore } = await import('./approval')
      const approvalStore = useApprovalStore()
      for (const [key, entry] of approvalStore.pendingApprovals) {
        if (entry.sessionId === sessionId) {
          approvalStore.pendingApprovals.delete(key)
        }
      }
    } catch { /* 忽略 */ }
    if (currentSessionId.value === sessionId) {
      if (sessions.value.length > 0) {
        currentSessionId.value = sessions.value[0].id
      } else {
        currentSessionId.value = null
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
  // 记录上次同步的消息签名，避免重复 PATCH 相同内容
  const _lastSyncSignatures = new Map()

  /**
   * 等待当前同步锁释放（供 useStreamFinalizer 使用）
   * @param {string} sessionId
   */
  const waitForSyncLock = async (sessionId) => {
    const lock = _syncLocks.get(sessionId)
    if (lock) await lock
  }

  /**
   * 刷新待同步队列（供 useStreamFinalizer 流结束后调用）
   * 确保所有 pending 的审批同步、消息同步等操作完成后再最终 PATCH
   * @param {string} sessionId
   * @param {{ messageIndex?: number }} [options]
   */
  const flushPendingSync = async (sessionId, { messageIndex } = {}) => {
    await syncLastMessageToBackend(sessionId, { allowCreate: false })
  }

  /**
   * 同步最后一条消息到后端（PATCH 已有，或 POST 创建）
   * @param {string} sessionId
   * @param {{ allowCreate?: boolean }} [options]
   *   - allowCreate: 为 false 时禁止 POST 创建新消息（后端 SSE 负责创建）
   */
  const syncLastMessageToBackend = async (sessionId, { allowCreate = true } = {}) => {
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
        if (!allowCreate) return  // 后端 SSE 流负责创建，前端禁止 POST
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
        // 签名检测：内容未变化则跳过 PATCH，避免流结束后重复请求
        const signature = `${lastMessage.backendId}:${lastMessage.content?.length}:${lastMessage.toolCalls?.length}:${lastMessage.sources?.length}`
        if (_lastSyncSignatures.get(sessionId) === signature) return
        _lastSyncSignatures.set(sessionId, signature)
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

  /**
   * 按消息索引精确同步指定消息到后端（useStreamFinalizer 依赖）
   * @param {string} sessionId
   * @param {number} messageIndex
   * @param {{ allowCreate?: boolean }} [options]
   */
  const syncMessageToBackend = async (sessionId, messageIndex, { allowCreate = true } = {}) => {
    if (!userStore.isLoggedIn) return
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session?.messages || !session.messages[messageIndex]) return
    const message = session.messages[messageIndex]
    if (!message.backendId) {
      if (!allowCreate) return
      const backendMsg = transformFrontendMessageToBackend(message)
      try {
        const res = await chatAPI.addMessage(sessionId, backendMsg)
        if (isApiSuccess(res) && res.data.data?.id) {
          message.backendId = res.data.data.id
        }
      } catch (error) {
        logger.error('Failed to sync message to backend:', error)
      }
    } else {
      const backendMsg = transformFrontendMessageToBackend(message)
      try {
        await chatAPI.updateMessage(message.backendId, backendMsg)
      } catch (error) {
        logger.error('Failed to update message in backend:', error)
      }
    }
  }

  /** debounced 工具调用同步 — 工具调用/结果到达后立即同步到后端 */
  const _toolSyncTimers = {}
  const _debouncedToolSync = (sessionId) => {
    if (_toolSyncTimers[sessionId]) clearTimeout(_toolSyncTimers[sessionId])
    _toolSyncTimers[sessionId] = setTimeout(() => {
      syncLastMessageToBackend(sessionId, { allowCreate: false }).catch(() => {})
      delete _toolSyncTimers[sessionId]
    }, 300)
  }

  /**
   * 按 messageBackendId 精确同步指定消息到后端
   * @param {string} sessionId
   * @param {string} messageBackendId - 后端消息 ID
   * @param {{ allowCreate?: boolean }} [options]
   */
  const syncMessageByBackendIdToBackend = async (sessionId, messageBackendId, { allowCreate = true } = {}) => {
    if (!userStore.isLoggedIn || !messageBackendId) return

    const session = sessions.value.find(s => s.id === sessionId)
    if (!session?.messages) return

    const message = session.messages.find(m =>
      m.backendId?.toString() === messageBackendId?.toString()
    )
    if (!message) {
      logger.warn(`[Session] syncMessageByBackendIdToBackend 未找到消息: sessionId=${sessionId}, backendId=${messageBackendId}`)
      return
    }

    if (!message.backendId) {
      if (!allowCreate) return
      const backendMsg = transformFrontendMessageToBackend(message)
      try {
        const res = await chatAPI.addMessage(sessionId, backendMsg)
        if (isApiSuccess(res) && res.data.data?.id) {
          message.backendId = res.data.data.id
        }
      } catch (error) {
        logger.error('Failed to sync message by backendId to backend:', error)
      }
    } else {
      const backendMsg = transformFrontendMessageToBackend(message)
      try {
        await chatAPI.updateMessage(message.backendId, backendMsg)
      } catch (error) {
        logger.error('Failed to update message by backendId in backend:', error)
      }
    }
  }

  /** 清理指定会话的工具同步定时器，流结束时调用 */
  const clearToolSyncTimer = (sessionId) => {
    if (_toolSyncTimers[sessionId]) {
      clearTimeout(_toolSyncTimers[sessionId])
      delete _toolSyncTimers[sessionId]
    }
  }

  /** 清除同步签名缓存（用于 interrupted 状态清理） */
  const clearSyncSignature = (sessionId) => {
    _lastSyncSignatures.delete(sessionId)
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
  const setResearchTaskIdToLastMessage = (sessionId, researchTaskId) => {
    const applied = _setLastField(sessionId, 'researchTaskId', researchTaskId)
    if (!applied && researchTaskId && _pendingResearchTaskIds) {
      // 兜底（P3 根因修复）：SSE deep_research 事件可能早于 assistant 消息创建到达，
      // 此时 last assistant 消息不存在，暂存 taskId，待消息创建后由 addMessageToSession 补充
      _pendingResearchTaskIds.set(sessionId, researchTaskId)
    }
    return applied
  }
  const setApprovalToLastMessage = (sessionId, approval) => {
    _setLastField(sessionId, 'approval', approval)
    _setLastField(sessionId, 'approvalState', approval?.state || 'pending')
  }

  /**
   * 将审批数据设置到指定 toolCall（工具调用级审批）
   *
   * 统一通过 toolCallMap 作为唯一真相源操作（与 researchStore 对齐）。
   * isSynthetic 占位机制：审批先于真实 tool 事件到达时，创建占位条目使 ToolCallCard
   * 立即渲染审批面板，并加入 pendingApprovals 队列（兜底机制）。
   *
   * @param {string} sessionId - 会话 ID
   * @param {string} toolCallId - 工具调用 ID（interrupt_id 或 tool_call_id）
   * @param {Object} approvalData - 审批事件数据
   */
  const setApprovalToToolCall = (sessionId, toolCallId, approvalData) => {
    if (!sessionId || !toolCallId || !approvalData) return
    const toolCallMap = _ensureToolCallsMap(sessionId)
    if (!toolCallMap) return

    const isSynthetic = setApprovalToToolCallInMap(toolCallMap, toolCallId, approvalData)
    triggerRef(toolCallsMap)
    _syncMessageToolCalls(sessionId)

    // 如果创建了占位条目，同时存入 pendingApprovals 队列（兜底机制，确保后续 tool 事件能正确合并）
    if (isSynthetic) {
      const pendingMap = _ensurePendingApprovalsMap(sessionId)
      if (pendingMap) {
        const pendingIds = [toolCallId, approvalData.toolCallId].filter(Boolean)
        for (const pid of pendingIds) {
          pendingMap.set(pid, { approvalData, toolCallId })
        }
        triggerRef(pendingApprovals)
        logger.info(
          `[Session] 占位 toolCall 已加入 pendingApprovals 队列: sessionId=${sessionId}, toolCallId=${toolCallId}, pendingIds=${pendingIds}`
        )
      }
    }
  }

  /**
   * 刷新待绑定的审批数据，将其附加到对应 toolCall
   *
   * 在 addOrUpdateToolCall / updateOrAddToolResult 创建/更新 toolCall 后调用，
   * 处理审批事件先于 tool 事件到达的时序场景：
   * 1. 从 pendingApprovals Map 中查找匹配的审批
   * 2. 从队列移除后调用 setApprovalToToolCall 绑定（此时 toolCallMap 中已有目标条目）
   *
   * @param {string} sessionId - 会话 ID
   * @param {string} toolCallId - 工具调用 ID
   */
  const flushPendingApprovals = (sessionId, toolCallId) => {
    if (!sessionId || !toolCallId) return
    const pendingMap = pendingApprovals.value.get(sessionId)
    if (!pendingMap) return
    const toolCallMap = toolCallsMap.value.get(sessionId)
    if (!toolCallMap) return
    const didBind = flushPendingApprovalsInMap(pendingMap, toolCallMap, toolCallId)
    if (didBind) {
      triggerRef(pendingApprovals)
      triggerRef(toolCallsMap)
      _syncMessageToolCalls(sessionId)
      logger.info(`[Session] pending 审批已绑定: sessionId=${sessionId}, toolCallId=${toolCallId}`)
    }
  }

  /**
   * 更新 toolCall 的审批状态（approval.state）
   *
   * 用于审批成功/失败/超时后同步消息中的审批数据。
   * 仅更新 approval.state，不再同步修改 toolCall.status（审批事件不应改变工具状态）。
   *
   * @param {string} sessionId - 会话 ID
   * @param {string} toolCallId - 工具调用 ID
   * @param {string} state - 新的 approval.state（如 'processing' / 'approved' / 'rejected' / 'timeout'）
   */
  const updateToolCallApprovalState = (sessionId, toolCallId, state) => {
    if (!sessionId || !toolCallId) return
    const toolCallMap = toolCallsMap.value.get(sessionId)
    if (!toolCallMap) return
    // 1. 更新 approval.state（不修改 status，由 updateApprovalStateInMap 保证）
    const stateUpdated = updateApprovalStateInMap(toolCallMap, toolCallId, state)
    if (!stateUpdated) return
    triggerRef(toolCallsMap)
    _syncMessageToolCalls(sessionId)
  }

  const appendToLastAssistantMessage = (sessionId, content) => {
    const result = getLastAssistantMessage(sessions.value, sessionId)
    if (!result) return
    result.message.content = (result.message.content || '') + content
    const ver = result.message.versions?.[result.message.currentVersion]
    if (ver) ver.content = result.message.content
  }

  /**
   * 设置最后一条消息的 streamState，并同步到当前 version。
   *
   * 用于流式生命周期管理（approval.js、useStreamFinalizer 等调用）：
   * - STREAMING：标记消息开始流式输出
   * - INTERRUPTED：中断流（取消同步、清理定时器）
   * - FINALIZING / SYNCING / COMPLETED / ERROR：流结束各阶段
   *
   * @param {string} sessionId - 会话 ID
   * @param {string} state - StreamState 枚举值
   */
  const setStreamStateToLastMessage = (sessionId, state) => {
    const result = _getLast(sessionId)
    if (!result) return
    result.message.streamState = state
    result.message.isStreaming = state === StreamState.STREAMING
    const ver = result.message.versions?.[result.message.currentVersion]
    if (ver) {
      ver.streamState = state
      ver.isStreaming = state === StreamState.STREAMING
    }

    if (state === StreamState.INTERRUPTED) {
      clearToolSyncTimer(sessionId)
      clearSyncSignature(sessionId)
    }
  }

  /**
   * 设置指定索引消息的 streamState，并同步到当前 version。
   * @param {string} sessionId
   * @param {number} idx
   * @param {string} state
   */
  const setStreamStateToMessageByIdx = (sessionId, idx, state) => {
    const result = _getByIndex(sessionId, idx)
    if (!result) return
    result.message.streamState = state
    result.message.isStreaming = state === StreamState.STREAMING
    const ver = result.message.versions?.[result.message.currentVersion]
    if (ver) {
      ver.streamState = state
      ver.isStreaming = state === StreamState.STREAMING
    }
  }

  /**
   * 添加 toolCall 到最后一条 assistant 消息（同步写入 toolCallMap）
   *
   * 与 addOrUpdateToolCall 不同，此方法直接将 toolCall 对象写入 Map（不做合并），
   * 用于初始化场景（如后端历史加载）。
   *
   * @param {string} sessionId - 会话 ID
   * @param {Object} toolCall - 工具调用对象
   */
  const addToolCallToLastMessage = (sessionId, toolCall) => {
    if (!sessionId || !toolCall) return
    const toolCallMap = _ensureToolCallsMap(sessionId)
    if (!toolCallMap) return
    const key = toolCall.id || toolCall.toolCallId
    if (!key) return
    toolCallMap.set(key, toolCall)
    triggerRef(toolCallsMap)
    _syncMessageToolCalls(sessionId)
  }

  /**
   * 创建/更新 toolCall（对应 SSE tool 事件）
   *
   * 委托通用函数 addOrUpdateToolCallInMap 处理 toolCallMap 操作（含 isSynthetic 占位机制、
   * parameters 参数回查），保留 store 特定的 _syncMessageToolCalls 和 flushPendingApprovals 调用。
   *
   * @param {string} sessionId - 会话 ID
   * @param {Object} data - 工具事件数据
   */
  const addOrUpdateToolCall = (sessionId, data) => {
    if (!sessionId || !data) return
    const toolCallMap = _ensureToolCallsMap(sessionId)
    if (!toolCallMap) return

    const toolCallId = addOrUpdateToolCallInMap(toolCallMap, data)
    if (!toolCallId) return

    triggerRef(toolCallsMap)
    _syncMessageToolCalls(sessionId)
    // toolCall 创建后，检查是否有待绑定的审批（时序保护：审批事件先到达）
    flushPendingApprovals(sessionId, toolCallId)
    _debouncedToolSync(sessionId)
  }

  /**
   * 添加/更新 toolCall 到最后一条 assistant 消息（兼容旧接口，委托给 addOrUpdateToolCall）
   *
   * @param {string} sessionId - 会话 ID
   * @param {Object} data - 工具事件数据
   */
  const addOrUpdateToolCallToLastMessage = (sessionId, data) => {
    addOrUpdateToolCall(sessionId, data)
  }

  /**
   * 更新/添加工具结果（对应 SSE tool_result 事件）
   *
   * 委托通用函数 updateOrAddToolResultInMap 处理 toolCallMap 操作（含结果更新、parameters 补充），
   * 保留 store 特定的 _syncMessageToolCalls 和 flushPendingApprovals 调用。
   *
   * @param {string} sessionId - 会话 ID
   * @param {Object} data - 工具结果事件数据
   */
  const updateOrAddToolResult = (sessionId, data) => {
    if (!sessionId || !data) return
    const toolCallMap = _ensureToolCallsMap(sessionId)
    if (!toolCallMap) return

    const toolCallId = updateOrAddToolResultInMap(toolCallMap, data)
    if (!toolCallId) return

    // 仅当工具未进入终态时才刷新待绑审批。updateOrAddToolResultInMap 在终态时
    // 已将 approval 置 null，flushPendingApprovals 会找到旧批次审批并重设，
    // 导致审批面板残留（P29 次生问题：已完成工具仍显示审批确认）
    const isTerminal = isTerminalStatus(data.status)
    if (!isTerminal) {
      flushPendingApprovals(sessionId, toolCallId)
    }
    triggerRef(toolCallsMap)
    _syncMessageToolCalls(sessionId)
    _debouncedToolSync(sessionId)
  }

  /**
   * 更新/添加工具结果到最后一条 assistant 消息（兼容旧接口，委托给 updateOrAddToolResult）
   *
   * @param {string} sessionId - 会话 ID
   * @param {Object} data - 工具结果事件数据
   */
  const updateOrAddToolResultToLastMessage = (sessionId, data) => {
    updateOrAddToolResult(sessionId, data)
  }

  /**
   * 获取指定 session 的 toolCall（通过 toolCallId）
   *
   * @param {string} sessionId - 会话 ID
   * @param {string} toolCallId - 工具调用 ID
   * @returns {Object|null} toolCall 对象（不存在时返回 null）
   */
  const getToolCallById = (sessionId, toolCallId) => {
    if (!sessionId || !toolCallId) return null
    const toolCallMap = toolCallsMap.value.get(sessionId)
    if (!toolCallMap) return null
    const { toolCall } = findToolCallInMap(toolCallMap, toolCallId)
    return toolCall || null
  }

  /**
   * 获取指定 session 的所有工具调用
   *
   * @param {string} sessionId - 会话 ID
   * @returns {Array} 工具调用数组（session 不存在时返回空数组）
   */
  const getToolCallsBySession = (sessionId) => {
    if (!sessionId) return []
    const toolCallMap = toolCallsMap.value.get(sessionId)
    if (!toolCallMap) return []
    return Array.from(toolCallMap.values())
  }

  /**
   * 按 messageBackendId 过滤 toolCall
   *
   * 用于按消息分组显示工具调用（与 chat 模块的 messages 数组对齐）。
   *
   * @param {string} sessionId - 会话 ID
   * @param {number|string} messageBackendId - 消息后端 ID
   * @returns {Array} 匹配的 toolCall 数组
   */
  const getToolCallsByMessage = (sessionId, messageBackendId) => {
    if (!sessionId || messageBackendId === undefined || messageBackendId === null) return []
    const toolCallMap = toolCallsMap.value.get(sessionId)
    if (!toolCallMap) return []
    const target = String(messageBackendId)
    return Array.from(toolCallMap.values()).filter(
      tc => tc.messageBackendId !== undefined && String(tc.messageBackendId) === target
    )
  }

  /**
   * 在 session 中查找 toolCall
   *
   * @param {string} sessionId - 会话 ID
   * @param {string} toolCallId - 工具调用 ID
   * @returns {Object|null} toolCall 对象（不存在时返回 null）
   */
  const findToolCallInSession = (sessionId, toolCallId) => {
    return getToolCallById(sessionId, toolCallId)
  }

  /**
   * 更新 toolCall 的 status（用于审批通过后设置 running 状态等场景）
   *
   * 通过 Map 函数更新 toolCall.status，同步派生 message.toolCalls。
   *
   * @param {string} sessionId - 会话 ID
   * @param {string} toolCallId - 工具调用 ID
   * @param {string} status - 新的 toolCall.status
   * @returns {boolean} 是否成功更新
   */
  const updateToolCallStatus = (sessionId, toolCallId, status) => {
    if (!sessionId || !toolCallId) return false
    const toolCallMap = toolCallsMap.value.get(sessionId)
    if (!toolCallMap) return false

    // Map 未命中：从 messages 数组回填到 Map，对齐 research.js 行为
    if (!toolCallMap.has(toolCallId)) {
      const session = _findSession(sessionId)
      if (session?.messages) {
        for (const msg of session.messages) {
          if (msg.role !== 'assistant' || !Array.isArray(msg.toolCalls)) continue
          for (const tc of msg.toolCalls) {
            const key = tc.toolCallId || tc.id
            if (key && key === toolCallId && !toolCallMap.has(key)) {
              toolCallMap.set(key, { ...tc, messageBackendId: msg.backendId?.toString() })
            }
          }
        }
        triggerRef(toolCallsMap)
      }
    }

    const updated = updateToolCallStatusInMap(toolCallMap, toolCallId, status)
    if (!updated) return false
    triggerRef(toolCallsMap)
    _syncMessageToolCalls(sessionId)
    return true
  }

  /**
   * 最终化指定消息的所有非终态 toolCalls（流式完成后兜底）
   *
   * 被 messageIntegrity.finalizeToolCallsForCompletedMessage 调用（P3-22/P3-23 根因修复）：
   * WebSocket tool_call_completed / approval_approved 事件丢失或乱序时，
   * toolCallsMap（单一真相源）中 toolCall.status 可能卡在 pending/running/waiting，
   * approval.state 可能卡在 pending/processing/waiting，导致 UI 永久显示"执行中"
   * 和审批按钮不消失。
   *
   * 此方法在流式结束时兜底：将非终态工具状态修正为 COMPLETED，
   * 将非终态审批状态修正为 TIMEOUT，并同步到 message.toolCalls（派生数据）。
   *
   * @param {string} sessionId - 会话 ID
   * @param {string} [messageBackendId] - 可选，仅最终化属于该消息的 toolCalls；
   *   不传则最终化该 session 下所有 toolCalls
   * @returns {number} 最终化的 toolCall 数量
   */
  const finalizeToolCallsInMapForSession = (sessionId, messageBackendId) => {
    if (!sessionId) return 0
    const toolCallMap = toolCallsMap.value.get(sessionId)
    if (!toolCallMap || toolCallMap.size === 0) return 0

    const finalizedCount = finalizeToolCallsInMap(toolCallMap, messageBackendId)
    if (finalizedCount > 0) {
      triggerRef(toolCallsMap)
      _syncMessageToolCalls(sessionId)
      logger.info(
        `[Session] finalizeToolCallsInMap: 最终化 ${finalizedCount} 个 toolCall: ` +
        `session=${sessionId}, message=${messageBackendId || '(全部)'}`
      )
    }
    return finalizedCount
  }

  /**
   * 插入或更新会话（实时同步用）
   * 与参考项目 sync.js/handleUserEvent.js 的 session_created 处理对齐。
   * @param {Object} sessionData - 后端会话原始数据
   */
  const upsertSession = (sessionData) => {
    const normalized = transformBackendSessionToFrontend(sessionData)
    if (!normalized) return null

    const index = sessions.value.findIndex(s => s.id === normalized.id)
    if (index !== -1) {
      const existing = sessions.value[index]
      Object.assign(existing, {
        title: normalized.title,
        mode: normalized.mode,
        selectedKnowledgeBase: normalized.selectedKnowledgeBase,
        selectedKnowledgeBases: normalized.selectedKnowledgeBases,
        updatedAt: normalized.updatedAt,
        ...(existing.messages?.length === 0 && normalized.messages?.length > 0
          ? { messages: normalized.messages, messageCount: normalized.messages.length }
          : {}),
      })
      return existing
    } else {
      sessions.value.unshift(normalized)
      return normalized
    }
  }

  /** sync.js updateSessionFields 依赖的白名单 */
  const SESSION_SAFE_UPDATE_KEYS = new Set([
    'title', 'mode', 'selectedKnowledgeBase', 'selectedKnowledgeBases',
    'updatedAt', 'messageCount', 'knowledgeBases'
  ])

  /**
   * 合并会话字段（session_updated 事件处理用）
   * @param {string} sessionId
   * @param {Object} fields
   */
  const updateSessionFields = (sessionId, fields) => {
    const session = _findSession(sessionId)
    if (!session || !fields) return
    const safeFields = {}
    for (const key of Object.keys(fields)) {
      if (SESSION_SAFE_UPDATE_KEYS.has(key)) {
        safeFields[key] = fields[key]
      }
    }
    Object.assign(session, safeFields)
    session.updatedAt = Date.now()
  }

  /**
   * 从 store 中移除指定 session（同步清理 toolCallsMap / pendingApprovals）
   *
   * 与 deleteSession 不同：deleteSession 是异步方法（含后端调用 + UI 提示），
   * removeSession 是纯前端清理，用于测试场景与本地状态重置。
   *
   * @param {string} sessionId - 会话 ID
   */
  const removeSession = (sessionId) => {
    if (!sessionId) return
    const idx = sessions.value.findIndex(s => s.id === sessionId)
    if (idx !== -1) {
      sessions.value.splice(idx, 1)
    }
    if (toolCallsMap.value.has(sessionId)) {
      toolCallsMap.value.delete(sessionId)
      triggerRef(toolCallsMap)
    }
    if (pendingApprovals.value.has(sessionId)) {
      pendingApprovals.value.delete(sessionId)
      triggerRef(pendingApprovals)
    }
    if (currentSessionId.value === sessionId) {
      currentSessionId.value = sessions.value.length > 0 ? sessions.value[0].id : null
    }
  }

  const setUsageToLastMessage = (sessionId, usageData) => {
    const result = getLastAssistantMessage(sessions.value, sessionId)
    if (!result) return
    if (usageData.model !== undefined) result.message.model = usageData.model
    if (usageData.tokenCount !== undefined) result.message.tokenCount = usageData.tokenCount
    if (usageData.tokens !== undefined) result.message.tokens = usageData.tokens
    if (usageData.tokenDetail !== undefined) result.message.tokenDetail = usageData.tokenDetail
    if (usageData.responseTime !== undefined) result.message.responseTime = usageData.responseTime
    // 同步到 version，避免切换版本后 usage 数据丢失
    const ver = result.message.versions?.[result.message.currentVersion]
    if (ver) {
      if (usageData.model !== undefined) ver.model = usageData.model
      if (usageData.tokenCount !== undefined) ver.tokenCount = usageData.tokenCount
      if (usageData.tokens !== undefined) ver.tokens = usageData.tokens
      if (usageData.tokenDetail !== undefined) ver.tokenDetail = usageData.tokenDetail
      if (usageData.responseTime !== undefined) ver.responseTime = usageData.responseTime
    }
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
    // 同步清理当前会话的工具调用与待绑定审批数据
    const sessionId = currentSessionId.value
    if (sessionId) {
      if (toolCallsMap.value.has(sessionId)) {
        toolCallsMap.value.delete(sessionId)
        triggerRef(toolCallsMap)
      }
      if (pendingApprovals.value.has(sessionId)) {
        pendingApprovals.value.delete(sessionId)
        triggerRef(pendingApprovals)
      }
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

  // 幂等初始化：main.js 与 ChatView onMounted 都可能触发，重复调用只返回同一 promise，
  // 避免并发重复请求（loadSessionsFromBackend 内部亦有 _loadingPromise 兜底）
  let _initializePromise = null
  const initialize = async () => {
    if (_initializePromise) return _initializePromise
    _initializePromise = (async () => {
      logger.log('[Security] Initializing session store...')
      if (userStore.isLoggedIn) {
        await Promise.all([loadSessionsFromBackend(), loadKnowledgeBases()])
      } else {
        clearAllLocalData()
      }
    })()
    return _initializePromise
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

  /**
   * 按 backendId 定位消息并更新指定字段。
   * 用于非触发浏览器通过 WebSocket stream_content_update 等事件
   * 实时更新消息内容（content/reasoning/sources/suggestions/context）。
   * @param {string} sessionId - 会话 ID
   * @param {string} backendId - 消息的后端 ID
   * @param {string} field - 要更新的字段名
   * @param {*} value - 新值
   */
  const updateMessageFieldByBackendId = (sessionId, backendId, field, value) => {
    if (!sessionId || !backendId || !field) return
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session?.messages) return
    const msg = session.messages.find(m =>
      m.backendId?.toString() === backendId?.toString() ||
      m.id?.toString() === backendId?.toString()
    )
    if (!msg) return
    // 兼容 Vue 2 reactivity：直接赋值
    msg[field] = value
    // 同步到 versions
    const ver = msg.versions?.[msg.currentVersion]
    if (ver) ver[field] = value
  }

  // ==================== 工具调用 Map 反向同步 ====================

  /**
   * 将 toolCallsMap 中的状态同步回所有消息的 toolCalls 数组
   *
   * 方向：Map → messages（反向同步）。
   * 场景：loadSessionDetail 后 API 数据已写入 Map，需将 Map 中的完整状态
   * （含 WebSocket 事件已更新的动态字段）回写到 messages 数组供 UI 渲染。
   */
  const _syncAllMessageToolCallsFromMap = (sessionId) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session) return
    const toolCallMap = toolCallsMap.value.get(sessionId)
    if (!toolCallMap || toolCallMap.size === 0) return
    for (const msg of session.messages) {
      if (msg.role !== 'assistant') continue
      // 归属规则统一走 _collectToolCallsForMessage（Task 5.4）：
      // 无 messageBackendId 条目仅进最后一条 assistant，禁止复制到每条 assistant
      const isLast = _isLastAssistantMessage(sessionId, msg)
      const arr = _collectToolCallsForMessage(toolCallMap, msg, isLast)
      if (arr.length > 0) {
        arr.sort((a, b) => {
          const ai = a._index ?? 999
          const bi = b._index ?? 999
          return ai - bi
        })
        msg.toolCalls = arr
        const ver = msg.versions?.[msg.currentVersion]
        if (ver) ver.toolCalls = arr
      }
    }
  }

  // ==================== 消息删除 ====================

  const removeMessagesByIds = (sessionId, ids) => {
    if (!sessionId || !Array.isArray(ids) || ids.length === 0) return
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session) return
    const idSet = new Set(ids.map(String))
    session.messages = session.messages.filter(
      m => !idSet.has(String(m.id)) && !idSet.has(String(m.backendId))
    )
    const tcMap = toolCallsMap.value.get(sessionId)
    if (tcMap) {
      for (const [tcId, tc] of tcMap) {
        if (tc.messageBackendId && idSet.has(tc.messageBackendId)) tcMap.delete(tcId)
      }
    }
  }

  return {
    sessions,
    currentSessionId,
    selectedKnowledgeBase,
    selectedKnowledgeBases,
    knowledgeBases,
    isLoading,
    paginationMeta,
    // 工具调用状态（Map 为唯一真相源，按 sessionId 索引）
    toolCallsMap,
    // pendingApprovals（合成占位绑定队列）不导出：SubTask 8.4 收敛为
    // approvalStore.pendingApprovals 单一权威，本队列仅为内部中间态
    optimisticSessionIds,
    currentSession,
    loadSessionsFromBackend,
    loadMoreSessions,
    loadSessionDetail,
    createNewSession,
    switchSession,
    deleteSession,
    removeSession,
    updateSession,
    updateSessionTitle,
    upsertSession,
    updateSessionFields,
    consumeOptimisticSession,
    addMessageToSession,
    syncLastMessageToBackend,
    syncMessageByBackendIdToBackend,
    syncMessageToBackend,
    waitForSyncLock,
    flushPendingSync,
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
    setApprovalToToolCall,
    updateToolCallApprovalState,
    appendToLastAssistantMessage,
    addToolCallToLastMessage,
    addOrUpdateToolCall,
    addOrUpdateToolCallToLastMessage,
    updateOrAddToolResult,
    updateOrAddToolResultToLastMessage,
    getToolCallById,
    getToolCallsBySession,
    getToolCallsByMessage,
    findToolCallInSession,
    updateToolCallStatus,
    finalizeToolCallsInMap: finalizeToolCallsInMapForSession,
    flushPendingApprovals,
    // 同步工具调用到消息（handleSessionEvent 中 message_added 后调用，解决审批组件错位问题）
    syncMessageToolCalls: _syncMessageToolCalls,
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
    updateMessageFieldByBackendId,
    clearToolSyncTimer,
    clearSyncSignature,
    setStreamStateToLastMessage,
    setStreamStateToMessageByIdx,
    deletedSessionIds,
    removeMessagesByIds,
  }
})
