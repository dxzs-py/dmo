import { defineStore } from 'pinia'
import { watch, reactive } from 'vue'
import { useRealtimeSync } from '@/composables/useRealtimeSync'
import { useSessionStore } from '@/stores/session'
import { useApprovalStore } from '@/stores/approval'
import { useResearchStore } from '@/stores/research'
import { useWorkflowStore } from '@/stores/workflow'
import { logger } from '@/utils/logger'
import { mergeMessageFromBackend } from '@/utils/message-operations'
import { PROTECTED_STREAM_STATES } from '@/types'
import { createSeqDedup } from './sync/seqDedup'
import { createOrderedQueue } from './sync/orderedQueue'
import { createHandleUserEvent } from './sync/handleUserEvent'
import { createHandleSessionEvent } from './sync/handleSessionEvent'
import { createHandleTaskEvent } from './sync/handleTaskEvent'
import { createMessageHandlers } from './sync/messageHandlers'
import { createMessageIntegrityHandlers } from './sync/messageIntegrity'
import { createStreamStateHandlers } from './sync/streamStateHandlers'

/**
 * @typedef {import('@/composables/useRealtimeSync').RealtimeEvent} RealtimeEvent
 */

/**
 * 架构说明：SSE 与 WebSocket 的职责划分
 *
 * SSE（请求浏览器独占）：
 *   - 流式 chunks、reasoning、sources、suggestions、context —— 请求浏览器的实时数据源
 *   - tool / tool_result 事件仍保留在 SSE 通道（stream_helpers.py 仍 yield），
 *     用于请求浏览器的实时工具调用渲染
 *
 * WebSocket（所有浏览器共享）：
 *   - 跨浏览器同步事件（session_created/deleted/updated 等）
 *   - 8 个工具调用事件（tool_call_pending/input_ready/waiting/running/completed/failed/timeout/rejected）：
 *     所有浏览器（含请求浏览器）均通过 WebSocket 接收工具调用状态
 *   - 6 个审批事件（approval_pending/processing/waiting/approved/rejected/timeout）：
 *     所有浏览器通过 WebSocket 同步审批状态
 *   - message_updated：供非请求浏览器感知消息内容变更
 *   - 4 个学习工作流事件（workflow_step / workflow_state_update / workflow_completed / workflow_failed）：
 *     所有浏览器通过 WebSocket 同步学习工作流进度与状态（Task 23），
 *     由 onWorkflowEvent 回调委托给 workflowStore，WorkflowView watch store 更新 UI
 *
 * 双通道冗余设计（tool/tool_result）：
 *   请求浏览器同时从 SSE 和 WebSocket 接收工具调用事件，
 *   通过 utils/sse.js 的 SSE_DEFERRED_EVENT_TYPES 把 SSE 的 tool/tool_result 延迟
 *   到下一个事件循环（setTimeout 0），让 WebSocket 优先处理，
 *   避免请求浏览器工具调用计数滞后。
 *   两个通道的事件最终汇聚到 sessionStore.toolCallsMap（Map 为唯一真相源），
 *   通过 _findMatchingToolCall 的 id 精确匹配 + PROTECTED_STATUSES 状态保护
 *   实现幂等合并。
 *
 * 关键规则：请求浏览器在流式期间，以 SSE 为准处理 content/reasoning 等字段，
 * 跳过 WebSocket 中与 SSE 重叠的 message_updated，
 * 避免双通道数据冲突导致"输出到一半被刷新"。
 * 工具调用事件采用双通道冗余设计（见上方说明），由 SSE_DEFERRED_EVENT_TYPES
 * 协调顺序，最终汇聚到 toolCallsMap 实现幂等合并。
 *
 * 模块拆分说明（Task 10）：
 *   主文件仅负责状态初始化、装配各 handler、公共方法、watch 与 return。
 *   具体事件处理逻辑拆分到 ./sync/ 子目录下：
 *   - constants.js     : 事件类型与状态映射常量
 *   - seqDedup.js      : seq 去重器（lastSeenSeq + shouldSkip）
 *   - orderedQueue.js  : 有序事件队列（_processSessionEventOrdered）
 *   - handleUserEvent.js   : user 通道事件处理（session_created/deleted/updated）
 *   - handleSessionEvent.js: session 通道事件处理（含工具调用/审批/消息/流式状态等）
 *   - handleTaskEvent.js   : task 通道事件处理（独立深度研究 + 学习工作流 workflow_* 事件）
 */
export const useSyncStore = defineStore('sync', () => {
  const realtime = useRealtimeSync()
  const sessionStore = useSessionStore()
  const approvalStore = useApprovalStore()
  const researchStore = useResearchStore()
  const workflowStore = useWorkflowStore()

  /** @type {(() => void) | null} */
  let unsubscribeUser = null

  // === 状态初始化 ===

  /** seq 去重器（封装 lastSeenSeq Map 与 shouldSkip 逻辑） */
  const seqDedup = createSeqDedup()

  /** session 通道事件有序队列（封装 _sessionEventQueue Map 与 _processSessionEventOrdered） */
  const orderedQueue = createOrderedQueue()

  /** 避免重复触发全量同步 */
  /** @type {Set<string>} */
  const fullSyncPending = new Set()

  /**
   * 当前正在通过 SSE 流式输出的会话集合。
   * 仅用于 stream_event / stream_started / stream_completed / stream_finalized
   * 的请求浏览器识别，以及 message_updated 跳过逻辑。
   * 工具调用事件（7 个 tool_call_*）不再依赖该集合跳过（SSE 已不推送工具事件）。
   * 使用 reactive(new Set()) 确保 add/delete 操作触发 Vue 响应式追踪。
   * @type {Set<string>}
   */
  const streamingSessions = reactive(new Set())

  /**
   * 非触发浏览器通过 WebSocket stream_started 事件感知"正在思考"的会话集合。
   * 触发浏览器通过 SSE 本地 isStreaming 状态感知，不依赖此集合。
   * @type {Set<string>}
   */
  const thinkingSessions = reactive(new Set())

  // === 公共方法（与流式生命周期相关） ===

  /**
   * 判断指定会话是否处于"正在思考"状态（非触发浏览器）
   * @param {string} sessionId
   * @returns {boolean}
   */
  const isThinking = (sessionId) => thinkingSessions.has(sessionId)

  /**
   * 标记会话开始流式输出（由 chat store 在 sendMessage 时调用）
   * @param {string} sessionId
   */
  const startStreaming = (sessionId) => {
    if (sessionId) {
      streamingSessions.add(sessionId)
      // 通知 realtime 模块 SSE 活跃，WebSocket 断开时不显示"已断开"
      realtime.incrementStreaming()
      logger.info(`[Sync] 开始流式: ${sessionId}`)
    }
  }

  /**
   * 标记会话结束流式输出（由 chat store 在 finally 块中调用）
   * @param {string} sessionId
   */
  const stopStreaming = (sessionId) => {
    if (sessionId) {
      streamingSessions.delete(sessionId)
      // 通知 realtime 模块 SSE 结束
      realtime.decrementStreaming()
      logger.info(`[Sync] 结束流式: ${sessionId}`)
    }
  }

  /**
   * 兜底全量同步：用后端完整状态替换/校准本地会话
   *
   * 保护态消息（STREAMING/INTERRUPTED/FINALIZING/SYNCING/COMPLETED）使用字段级合并，
   * 避免后端快照整体替换本地内容。
   *
   * 注意：COMPLETED 纳入保护态是为了防止 stream_completed(finalized=false) 触发的
   * 全量同步覆盖已确认的内容。非请求浏览器的全量同步由 handleStreamFinalized 在
   * 状态尚未标记为 COMPLETED 时触发，并通过 options.allowContentMerge=true 显式声明
   * 允许 content 覆盖（stream_finalized 标志后端 PATCH 已持久化，content 为权威）。
   *
   * @param {string} sessionId
   * @param {Object} [options={}] - 合并选项，透传给 mergeMessageFromBackend
   * @param {boolean} [options.allowContentMerge=false] - 是否允许在保护态下强制合并 content
   * @returns {Promise<{backendMessages: Array}|null>} 返回后端消息快照（合并前）供完整性校验
   */
  const requestFullSync = async (sessionId, options = {}) => {
    if (!sessionId) return null

    const localSession = sessionStore.sessions.find(s => s.id === sessionId)
    // 保护态消息：使用字段级合并，避免后端快照整体替换本地内容
    const protectedMessages = (localSession?.messages || []).filter(m => PROTECTED_STREAM_STATES.has(m.streamState))
    const protectedIdSet = new Set(
      protectedMessages
        .map(m => m.backendId?.toString() || m.id?.toString())
        .filter(Boolean)
    )

    const detail = await sessionStore.loadSessionDetail(sessionId, { forceRefresh: true })
    if (!detail) {
      logger.warn(`[Sync] 全量同步失败，无法加载会话详情: ${sessionId}`)
      return null
    }

    let session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session) {
      sessionStore.upsertSession(detail)
      session = sessionStore.sessions.find(s => s.id === sessionId)
    }
    if (!session) return null

    // 保存后端消息快照（合并前），供调用方做 tool_calls/content 完整性校验
    const backendMessagesSnapshot = session.messages
      ? session.messages.map(m => ({ ...m }))
      : []

    // 若本地存在保护态消息，使用字段级合并而非整体替换
    if (protectedMessages.length > 0 && session.messages) {
      const mergedMessages = []
      const handledIds = new Set()

      for (const backendMsg of session.messages) {
        const mid = backendMsg.backendId?.toString() || backendMsg.id?.toString()
        if (mid && protectedIdSet.has(mid)) {
          const local = protectedMessages.find(
            m => (m.backendId?.toString() || m.id?.toString()) === mid
          )
          if (local) {
            // 字段级合并：后端补充非内容字段，本地保留流式/中断中内容
            // options.allowContentMerge=true 时允许后端 content 覆盖本地（stream_finalized 场景）
            mergeMessageFromBackend(local, backendMsg, options)
            mergedMessages.push(local)
            handledIds.add(mid)
            continue
          }
        }
        mergedMessages.push(backendMsg)
      }

      // 把本地保护态消息中后端没有的部分补回
      for (const local of protectedMessages) {
        const mid = local.backendId?.toString() || local.id?.toString()
        if (mid && !handledIds.has(mid)) {
          mergedMessages.push(local)
        }
      }

      mergedMessages.sort((a, b) =>
        (a.timestamp || a.createdAt || 0) - (b.timestamp || b.createdAt || 0)
      )
      session.messages = mergedMessages
      session.messageCount = mergedMessages.length
    }

    logger.info(`[Sync] 全量同步完成: session=${sessionId}, messages=${session.messages?.length || 0}, allowContentMerge=${options.allowContentMerge === true}`)
    return { backendMessages: backendMessagesSnapshot }
  }

  // === 装配 handlers ===
  // 分层装配（Task 16）：底层 handler → 流式状态 handler → session handler
  // 独立模块（messageHandlers / streamStateHandlers / messageIntegrity）
  // 提供与 handleSessionEvent.js 内联版本逻辑等价的工厂函数，
  // 通过依赖注入方式传入 createHandleSessionEvent，实现模块职责分离。
  //
  // Step 1: 创建底层 handler（消息 CRUD + 完整性校验）
  const messageHandlers = createMessageHandlers({ sessionStore, streamingSessions })
  const integrityHandlers = createMessageIntegrityHandlers({ sessionStore })

  // Step 2: 创建流式状态 handler（依赖 messageIntegrity）
  const streamStateHandlers = createStreamStateHandlers({
    sessionStore,
    approvalStore,
    researchStore,
    streamingSessions,
    thinkingSessions,
    requestFullSync,
    finalizeToolCalls: integrityHandlers.finalizeToolCallsForCompletedMessage,
    verifyMessageIntegrity: integrityHandlers.verifyMessageIntegrityAfterSync,
  })

  // Step 3: 创建 session handler（注入独立模块的函数）
  const sessionHandlers = createHandleSessionEvent({
    sessionStore,
    approvalStore,
    researchStore,
    seqDedup,
    orderedQueue,
    streamingSessions,
    thinkingSessions,
    fullSyncPending,
    requestFullSync,
    // 注入独立模块函数，handleSessionEvent.js 中优先使用注入版本，回退内联版本
    ...messageHandlers,
    ...streamStateHandlers,
  })
  const {
    handleSessionEvent,
    applySessionEvent,
    handleToolCallEvent,
    handleApprovalEvent,
  } = sessionHandlers

  const { handleTaskEvent } = createHandleTaskEvent({
    researchStore,
    handleToolCallEvent,
    handleApprovalEvent,
    onWorkflowEvent: (eventType, payload, taskId) => {
      // 学习工作流事件委托给 workflowStore，由 WorkflowView watch store 变化更新 UI
      workflowStore.updateWorkflowFromEvent(eventType, payload, taskId)
    },
  })

  /**
   * 统一实时事件处理器（三模块共享入口）
   *
   * 合并 handleSessionEvent + handleTaskEvent 的统一入口，通过 session_id / task_id
   * 自动路由到正确的处理通道。路由字段解析顺序：
   *   payload.session_id || event.session_id
   *   payload.task_id    || event.task_id
   * （后端 _publish_to_session_async 将 session_id 注入到 event 顶层，与 payload 平级，
   *  需 fallback 到 event 顶层才能正确路由。）
   *
   * 路由规则：
   * - session_id 存在（chat / learning / 关联 deep_research）→ handleSessionEvent
   *   （含有序队列处理，确保 seq 顺序执行）
   * - 仅 task_id 存在（独立 deep_research）→ handleTaskEvent
   *   （task 通道事件量小，无需 seq 去重）
   *
   * 用于 WebSocket 订阅入口统一化：所有模块的 subscribeSession / subscribeTask
   * 均使用此函数作为回调（含 applyUserEvent 中 session_created 自动订阅），
   * 通过 payload + event 顶层字段自动区分模块和 store，避免双重订阅。
   *
   * @param {RealtimeEvent} event - 实时事件对象
   */
  const handleRealtimeEvent = async (event) => {
    const payload = event?.payload || event || {}
    // 修复：从 event 顶层提取 session_id/task_id，作为 fallback
    // 后端 _publish_to_session_async 将 session_id 注入到 event 顶层（与 payload 平级），不在 payload 内。
    // 与 useRealtimeSync.getChannelKey / dispatchEvent / handleSessionEvent 解析逻辑对齐。
    const sessionId = payload.session_id || event.session_id
    const taskId = payload.task_id || event.task_id

    if (sessionId) {
      await handleSessionEvent(event)
    } else if (taskId) {
      await handleTaskEvent(event)
    } else {
      // 日志级别 warn：修复后此分支不应再触发，保留用于异常排查
      logger.warn(`[Sync] handleRealtimeEvent 无法路由（缺少 session_id 和 task_id）: type=${event?.type}`)
    }
  }

  const { handleUserEvent, applyUserEvent } = createHandleUserEvent({
    sessionStore,
    realtime,
    handleRealtimeEvent,
  })

  // === 其他公共方法 ===

  /**
   * 重置指定会话的 seq 状态（lastSeenSeq + _sessionEventQueue）
   *
   * 使用场景：
   * - DeepResearchView 切换任务时调用 subscribeSession(replayFromSeq=0) 触发后端回放，
   *   但 lastSeenSeq Map 和 _sessionEventQueue.expectedSeq 不会因 replayFromSeq=0 自动重置，
   *   导致回放的低 seq 事件被 applySessionEvent 的去重逻辑跳过、
   *   或被 _processSessionEventOrdered 当作"过期事件"丢弃。
   *   调用方在 subscribeSession 前调用此函数手动重置，确保回放事件能正常处理。
   *
   * 重置内容：
   * - lastSeenSeq.delete(sessionId)：清除 seq 去重基线，让 applySessionEvent 不再跳过低 seq
   * - _sessionEventQueue 该 session 状态：expectedSeq=0、清空 queue、processing=false
   *
   * 注意：
   * - 仅重置本会话状态，不影响其他会话；不清理 streamingSessions/thinkingSessions。
   * - 建议在 subscribeSession(replayFromSeq=0) 之前调用，避免与正在处理的事件队列竞态。
   * - 若 _sessionEventQueue.processing===true 时调用，可能丢失未处理完的事件；
   *   调用方需确保调用时机不在事件处理过程中。
   *
   * @param {string} sessionId - 会话 ID
   */
  const resetSessionSeq = (sessionId) => {
    if (!sessionId) return
    seqDedup.resetSeq(sessionId)
    orderedQueue.reset(sessionId)
    logger.info(`[Sync] 重置 session seq 状态: ${sessionId}`)
  }

  /**
   * 初始化同步监听
   */
  const initialize = () => {
    if (unsubscribeUser) {
      unsubscribeUser()
    }
    unsubscribeUser = realtime.subscribeUserEvents(handleUserEvent)
    logger.log('[Sync] 实时同步 store 已初始化')
  }

  // WebSocket 重连成功后，对当前会话触发一次全量同步兜底
  watch(
    () => realtime.connectionStatus.value,
    (status, prevStatus) => {
      if (prevStatus === 'reconnecting' && status === 'connected') {
        const currentSessionId = sessionStore.currentSessionId
        if (currentSessionId) {
          logger.info(`[Sync] WebSocket 重连成功，触发全量同步: ${currentSessionId}`)
          requestFullSync(currentSessionId)
        }
      }
    }
  )

  return {
    streamingSessions,
    thinkingSessions,
    isThinking,
    startStreaming,
    stopStreaming,
    handleToolCallEvent,
    handleApprovalEvent,
    handleRealtimeEvent,
    initialize,
    handleUserEvent,
    handleSessionEvent,
    handleTaskEvent,
    applyUserEvent,
    applySessionEvent,
    resetSessionSeq,
    requestFullSync,
  }
})
