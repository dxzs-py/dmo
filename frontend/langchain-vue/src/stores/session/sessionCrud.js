/**
 * session store 切片：会话生命周期 CRUD 域
 * （拆分自原 stores/session.js，C5/cq-04 Task 3，行为保持）
 *
 * 持有 state：optimisticSessionIds（乐观会话去重标记）
 * actions：createNewSession / switchSession / deleteSession / removeSession /
 * updateSession / updateSessionTitle / upsertSession / updateSessionFields /
 * consumeOptimisticSession
 *
 * 跨切片运行时依赖（runtimeDeps，由 index.js 在全部切片创建后注册）：
 * - _removeSessionToolCallState（toolCallState，删除会话时同步清理 Map）
 */
import { ref, triggerRef } from 'vue'
import { chatAPI } from '@/api/chat'
import { useUserStore } from '../user'
import { ElMessage } from 'element-plus'
import { logger } from '../../utils/logger'
import { getModeLabel } from '../../utils/format'
import { isApiSuccess, transformBackendSessionToFrontend } from '../../utils/sessionTransformers'

export const createSessionCrudSlice = (sessionList, runtimeDeps) => {
  const { sessions, currentSessionId, loadSessionDetail, setSelectedKnowledgeBases } = sessionList

  /** @type {import('vue').Ref<Set<string>>} */
  const optimisticSessionIds = ref(new Set())

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
        sessionList.selectedKnowledgeBase.value = session.selectedKnowledgeBase
      } else {
        sessionList.selectedKnowledgeBase.value = null
      }
      if (session?.selectedKnowledgeBases) {
        setSelectedKnowledgeBases(session.selectedKnowledgeBases)
      } else {
        sessionList.selectedKnowledgeBases.value = []
      }
    }
  }

  const deleteSession = async (sessionId) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session) return

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
        if (error === 'cancel' || error?.toString?.().includes('cancel')) return
        logger.error('Failed to delete session from backend:', error)
        throw error
      }
    }

    // 用 filter 按 session_id 过滤移除（非按 index splice，避免 await 后 index 过期误删相邻会话）
    sessions.value = sessions.value.filter(s => s.id !== sessionId)
    // 清理该会话关联的工具调用与待绑定审批数据（Map 为唯一真相源）
    runtimeDeps._removeSessionToolCallState(sessionId)
    // 清理该会话关联的审批条目（延迟导入避免循环依赖）
    try {
      const { useApprovalStore } = await import('../approval')
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
    runtimeDeps._removeSessionToolCallState(sessionId)
    if (currentSessionId.value === sessionId) {
      currentSessionId.value = sessions.value.length > 0 ? sessions.value[0].id : null
    }
  }

  return {
    // state
    optimisticSessionIds,
    // actions
    consumeOptimisticSession,
    createNewSession,
    switchSession,
    deleteSession,
    removeSession,
    updateSession,
    updateSessionTitle,
    upsertSession,
    updateSessionFields,
  }
}
