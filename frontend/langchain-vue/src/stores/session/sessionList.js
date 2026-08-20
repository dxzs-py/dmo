/**
 * session store 切片：会话列表加载 / 初始化 / 知识库选择域
 * （拆分自原 stores/session.js，C5/cq-04 Task 3，行为保持）
 *
 * 持有 state：sessions / currentSessionId / selectedKnowledgeBase /
 * selectedKnowledgeBases / knowledgeBases / isLoading / lastLoadedUserId / paginationMeta
 *
 * 跨切片运行时依赖（runtimeDeps，由 index.js 在全部切片创建后注册）：
 * - _syncToolCallsMapFromMessages / syncAllMessageToolCallsFromMap（toolCallState）
 * - restoreSelectedVersions（versionFlow）
 * - _clearAllToolCallState（toolCallState）
 */
import { ref, watch } from 'vue'
import router from '../../router'
import { chatAPI } from '@/api/chat'
import { knowledgeAPI } from '@/api/knowledge'
import { useUserStore } from '../user'
import { ElMessage } from 'element-plus'
import { logger } from '../../utils/logger'
import { getQueryParam } from '../../utils/format'
import {
  isApiSuccess,
  transformBackendSessionToFrontend,
} from '../../utils/sessionTransformers'
import { mergeMessageFromBackend } from '../../utils/messageOperations'
import { CURRENT_SESSION_ID_KEY, _safeGetLocalStorage, _safeRemoveLocalStorage } from './helpers'

export const createSessionListSlice = (runtimeDeps) => {
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
  const paginationMeta = ref({
    total: 0,
    page: 1,
    pageSize: 20,
    totalPages: 1,
    hasMore: false,
  })

  const userStore = useUserStore()

  const clearAllLocalData = () => {
    sessions.value = []
    currentSessionId.value = null
    selectedKnowledgeBase.value = null
    selectedKnowledgeBases.value = []
    lastLoadedUserId.value = null
    // 同步清理工具调用与待绑定审批数据（Map 为唯一真相源）
    runtimeDeps._clearAllToolCallState()
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
          // 同步 API 加载的 toolCalls 到 toolCallsMap。
          // 必须以 detailData.messages（API 原始顺序 = 后端 DB 数组顺序，权威）为基准，
          // 而非 existingSession.messages：后者是本地派生数据，可能已被 WebSocket 竞态
          // 污染（事件先于 API 到达插入 Map → _syncMessageToolCalls 回写乱序 → merge 时
          // 本地顺序优先 _mergeToolCalls 保持乱序 → 循环污染）。每次 API 加载都重置
          // Map 插入顺序为权威顺序，切断污染循环。
          runtimeDeps._syncToolCallsMapFromMessages(sessionId, detailData.messages || [])
          // 反向同步：将 Map 中的完整状态回写到 messages 数组
          runtimeDeps.syncAllMessageToolCallsFromMap(sessionId)
        }
        logger.log(`[Session] Loaded detail for session ${sessionId} with ${detailData.messages?.length || 0} messages`)
        // 刷新兜底（Task 6.6）：恢复未固化消息的本地选中版本索引（纯视图，不写后端）
        runtimeDeps.restoreSelectedVersions(sessionId)
        return detailData
      }
    } catch (error) {
      logger.error('Failed to load session detail:', error)
    }
    return null
  }

  const getSessionMessages = (sessionId) => {
    const session = sessions.value.find(s => s.id === sessionId)
    return session ? session.messages : []
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
      // 重置初始化缓存：首次 initialize() 可能在未登录时执行（main.js 早于登录，
      // 仅执行 clearAllLocalData）。若 _initializePromise 被缓存为未登录版本，
      // 登录后此处直接返回旧 promise，会话列表将永远不会从 API 加载，
      // 表现为侧边栏只有 WebSocket session_created 注入的会话、刷新后恢复。
      _initializePromise = null
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
    // state
    sessions,
    currentSessionId,
    selectedKnowledgeBase,
    selectedKnowledgeBases,
    knowledgeBases,
    isLoading,
    lastLoadedUserId,
    paginationMeta,
    // actions
    loadSessionsFromBackend,
    loadMoreSessions,
    loadSessionDetail,
    getSessionMessages,
    setSelectedKnowledgeBase,
    setSelectedKnowledgeBases,
    loadKnowledgeBases,
    initialize,
    clearAllLocalData,
    touchSessionUpdatedAt,
  }
}
