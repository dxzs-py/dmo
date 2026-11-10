/**
 * session store 切片：消息域（消息增删、后端同步、流式状态、字段写入）
 * （拆分自原 stores/session.js，C5/cq-04 Task 3，行为保持）
 *
 * 持有闭包状态：_syncLocks（并发同步锁）/ _lastSyncSignatures（同步签名去重）/
 * _toolSyncTimers（debounced 工具同步定时器）
 *
 * 跨切片运行时依赖（runtimeDeps，由 index.js 在全部切片创建后注册）：
 * - _removeSessionToolCallState / _pruneToolCallsByRemovedMessageIds（toolCallState）
 * - clearFinalizeTimer（versionFlow，链式删除竞态防护）
 * - _debouncedToolSync 本切片导出给 toolCallState 使用（addOrUpdateToolCall 等）
 */
import { chatAPI } from '@/api/chat'
import { useUserStore } from '../user'
import { logger } from '../../utils/logger'
import { transformFrontendMessageToBackend } from '../../utils/sessionTransformers'
import {
  locateInSessions,
  setLastMessageField,
  createMessageVersion,
} from '../../utils/messageOperations'
import { StreamState } from '../../types'
import { _safeRemoveLocalStorage, _versionSelectionKey } from './helpers'

export const createMessageFlowSlice = (sessionList, runtimeDeps) => {
  const { sessions, currentSessionId } = sessionList

  const userStore = useUserStore()

  // 定位收敛（spec 组 5）：locateInSessions 为跨会话定位唯一权威；
  // 不传 messageId 时保持原"末条消息且必须为 assistant"兜底语义
  const _getLast = (sessionId) => locateInSessions(sessions.value, sessionId)
  const _setLastField = (sessionId, field, value) => setLastMessageField(sessions.value, sessionId, field, value)

  /**
   * 判断消息是否有值得同步到后端的内容。
   *
   * 背景：WebSocket 断链期间前端收不到任何正文快照，本地 message.content 为空；
   * 但工具卡片/定时同步仍会整条 PATCH（content: ''），会把后端已落库的正文清空。
   * 正文、工具、来源、审批全为空时，本次同步没有任何信息量，直接跳过。
   *
   * @param {Object} message
   * @returns {boolean}
   */
  const _hasSyncableContent = (message) => {
    if (!message) return false
    // 固化/版本切换属显式状态变更，即使正文为空也必须落库
    if (message.isFinalized) return true
    if ((message.content || '').trim()) return true
    if (Array.isArray(message.toolCalls) && message.toolCalls.length > 0) return true
    if (Array.isArray(message.sources) && message.sources.length > 0) return true
    if (message.approval && Object.keys(message.approval).length > 0) return true
    return false
  }

  const addMessageToSession = (sessionId, message) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session) return
    const messageWithVersions = {
      ...message,
      versions: [createMessageVersion(message)],
      currentVersion: 0
    }
    session.messages.push(messageWithVersions)
    session.messageCount = session.messages.length
  }

  // 防止 syncLastMessageToBackend 并发调用导致重复 PATCH
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
   */
  const flushPendingSync = async (sessionId) => {
    await syncLastMessageToBackend(sessionId)
  }

  /**
   * 同步最后一条消息到后端（PATCH 已有消息）。
   * 消息记录由后端 ChatStreamView 创建并经 WebSocket 广播 MESSAGE_ADDED，
   * 本地尚未拿到 backendId 时跳过，等待广播补齐。
   * @param {string} sessionId
   */
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
      if (!lastMessage.backendId) return
      const backendMsg = transformFrontendMessageToBackend(lastMessage)
      // 空壳保护：本地没有正文/工具/来源/审批时不 PATCH，
      // 避免把后端已持久化的正文写成空（WS 断链场景）
      if (!_hasSyncableContent(lastMessage)) return
      // 签名检测：内容未变化则跳过 PATCH，避免流结束后重复请求
      const signature = `${lastMessage.backendId}:${lastMessage.content?.length}:${lastMessage.toolCalls?.length}:${lastMessage.sources?.length}`
      if (_lastSyncSignatures.get(sessionId) === signature) return
      _lastSyncSignatures.set(sessionId, signature)
      try {
        await chatAPI.updateMessage(lastMessage.backendId, backendMsg)
      } catch (error) {
        logger.error('Failed to update message in backend:', error)
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
   */
  const syncMessageToBackend = async (sessionId, messageIndex) => {
    if (!userStore.isLoggedIn) return
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session?.messages || !session.messages[messageIndex]) return
    const message = session.messages[messageIndex]
    if (!message.backendId) return
    if (!_hasSyncableContent(message)) return
    const backendMsg = transformFrontendMessageToBackend(message)
    try {
      await chatAPI.updateMessage(message.backendId, backendMsg)
    } catch (error) {
      logger.error('Failed to update message in backend:', error)
    }
  }

  /** debounced 工具调用同步 — 工具调用/结果到达后立即同步到后端 */
  const _toolSyncTimers = {}
  const _debouncedToolSync = (sessionId) => {
    if (_toolSyncTimers[sessionId]) clearTimeout(_toolSyncTimers[sessionId])
    _toolSyncTimers[sessionId] = setTimeout(() => {
      syncLastMessageToBackend(sessionId).catch(() => {})
      delete _toolSyncTimers[sessionId]
    }, 300)
  }

  /**
   * 按 messageBackendId 精确同步指定消息到后端
   * @param {string} sessionId
   * @param {string} messageBackendId - 后端消息 ID
   */
  const syncMessageByBackendIdToBackend = async (sessionId, messageBackendId) => {
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

    const backendMsg = transformFrontendMessageToBackend(message)
    try {
      await chatAPI.updateMessage(message.backendId, backendMsg)
    } catch (error) {
      logger.error('Failed to update message by backendId in backend:', error)
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

  /**
   * 会话删除时的同步资源回收入口（D-7）：清理按会话键缓存的
   * _syncLocks / _lastSyncSignatures / _toolSyncTimers。
   * 这些 Map/对象按 sessionId 只增不清会随长期使用缓慢泄漏；
   * session store 的 deleteSession / removeSession 动作负责调用。
   * @param {string} sessionId
   */
  const cleanupSessionSyncState = (sessionId) => {
    if (!sessionId) return
    _syncLocks.delete(sessionId)
    _lastSyncSignatures.delete(sessionId)
    clearToolSyncTimer(sessionId)
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

  const setApprovalToLastMessage = (sessionId, approval) => {
    _setLastField(sessionId, 'approval', approval)
    _setLastField(sessionId, 'approvalState', approval?.state || 'pending')
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
      runtimeDeps._removeSessionToolCallState(sessionId)
    }
  }

  const removeMessagePairFromSession = (sessionId, userMessageId) => {
    // 链式截断删除：删除第 N 轮（user 消息）及其后全部消息，N 之前完整保留。
    // 与后端 ChatMessagePairDeleteView 的链式截断语义一致。
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session) return
    const userIdx = session.messages.findIndex(m => m.id === userMessageId || m.backendId === userMessageId)
    if (userIdx === -1) return
    // 记录被删除消息（splice 前），随后清理其版本选择状态
    const deletedMessages = session.messages.slice(userIdx)
    const deletedCount = session.messages.length - userIdx
    session.messages.splice(userIdx, deletedCount)
    session.messageCount = session.messages.length
    session.updatedAt = Date.now()
    // 竞态防护（Task 6.7）：待固化末轮消息对被链式删除时，销毁本地超时计时器、
    // 丢弃被删除消息的未固化版本选择状态（localStorage），禁止删除后再执行固化并发脏写。
    runtimeDeps.clearFinalizeTimer(sessionId)
    for (const msg of deletedMessages) {
      _safeRemoveLocalStorage(_versionSelectionKey(sessionId, msg.backendId || msg.id))
    }
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

  // ==================== 消息删除 ====================

  /**
   * 删除指定消息中的一个 artifact（右侧详情面板的产物移除）。
   * artifacts 为纯前端 UI 状态（无后端同步链路），仅修改 store 内消息对象，
   * 保证单向数据流：组件层只 emit 事件，由消息属主（store）执行删除。
   * @param {string} sessionId - 会话 ID
   * @param {string} messageId - 消息 ID
   * @param {number} index - artifact 下标
   * @returns {boolean} 是否发生删除
   */
  const removeMessageArtifact = (sessionId, messageId, index) => {
    if (!sessionId || !messageId || !Number.isInteger(index)) return false
    const located = locateInSessions(sessions.value, sessionId, messageId)
    if (!located) return false
    const { message } = located
    if (!Array.isArray(message.artifacts) || index < 0 || index >= message.artifacts.length) {
      return false
    }
    message.artifacts.splice(index, 1)
    return true
  }

  const removeMessagesByIds = (sessionId, ids) => {
    if (!sessionId || !Array.isArray(ids) || ids.length === 0) return
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session) return
    const idSet = new Set(ids.map(String))
    session.messages = session.messages.filter(
      m => !idSet.has(String(m.id)) && !idSet.has(String(m.backendId))
    )
    runtimeDeps._pruneToolCallsByRemovedMessageIds(sessionId, ids)
  }

  return {
    // actions
    addMessageToSession,
    syncLastMessageToBackend,
    syncMessageByBackendIdToBackend,
    syncMessageToBackend,
    waitForSyncLock,
    flushPendingSync,
    updateLastMessage,
    appendToLastMessage,
    setApprovalToLastMessage,
    setStreamStateToLastMessage,
    clearCurrentSessionMessages,
    removeMessagePairFromSession,
    updateMessageFieldByBackendId,
    removeMessageArtifact,
    removeMessagesByIds,
    clearToolSyncTimer,
    clearSyncSignature,
    cleanupSessionSyncState,
    // 切片间共享（toolCallState.addOrUpdateToolCall / updateOrAddToolResult 使用）
    _debouncedToolSync,
  }
}
