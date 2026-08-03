import { logger } from '@/utils/logger'
import { StreamState, ApprovalState } from '@/types'
import { APPROVAL_STATE_MAP } from './constants'
import {
  getSession,
  ensureSessionLoaded,
  findMessageById,
  getLastAssistantMessage,
} from './helpers'

/**
 * @typedef {import('@/composables/useRealtimeSync').RealtimeEvent} RealtimeEvent
 */

/**
 * 创建审批事件处理器（三模块共享：chat / deep_research / learning）
 *
 * 后端为每个 EventType 独立 ws_event_name，前端通过 event.type 直接区分
 * 6 个审批事件类型（approval_pending/processing/waiting/approved/rejected/timeout）。
 *
 * 通过 sessionId / options.taskId 自动路由：
 * - sessionId 存在（chat / learning / 关联 deep_research）→ approvalStore.handleApprovalEvent
 *   + sessionStore streamState 转换
 * - 仅 options.taskId 存在（独立 deep_research）→ approvalStore.handleApprovalEvent({ source, taskId })
 *   （researchStore 无 streamState 转换，深度研究状态由 task 通道事件单独管理）
 *
 * @param {Object} ctx - 依赖上下文
 * @param {Object} ctx.sessionStore - session store 实例
 * @param {Object} ctx.approvalStore - approval store 实例
 * @param {Set<string>} ctx.streamingSessions - reactive(new Set())，请求浏览器 SSE 活跃会话集合
 * @returns {{
 *   handleApprovalEvent: (sessionId: string|null, payload: Object, eventType: string, options?: { taskId?: string, source?: string }) => Promise<void>,
 * }}
 */
export const createHandleApprovalEvent = (ctx) => {
  const { sessionStore, approvalStore, streamingSessions } = ctx

  /**
   * 根据 payload.messageId 或 payload.extra.messageId 在指定会话中定位消息。
   * 用于 approval_* 等事件优先按 message_id 路由，避免最后一条 assistant 消息兜底
   * 导致非末尾消息（如重新生成中途的旧消息）审批 UI 错位到最后一条。
   * @param {string} sessionId
   * @param {Object} payload
   * @returns {Object|null}
   */
  const _findMessageByIdOrExtra = (sessionId, payload) => {
    const messageId = payload?.messageId || payload?.extra?.messageId
    if (!messageId) return null
    const session = getSession(sessionStore, sessionId)
    return findMessageById(session, messageId)
  }

  /**
   * 获取会话最后一条 assistant 消息（按 sessionId 查找 session 后委托给 helper）
   * @param {string} sessionId
   * @returns {Object|null}
   */
  const _getLastAssistantMessage = (sessionId) => {
    const session = getSession(sessionStore, sessionId)
    return getLastAssistantMessage(session)
  }

  /**
   * 收集同一 graph_interrupt_id 下所有 toolCall 的 approval 状态
   *
   * 优先使用后端返回的 remaining_pending_count（权威计数），避免因不同浏览器
   * toolCalls 事件到达顺序不一致导致本地计算结果不同。
   *
   * @param {Object} message - 消息对象
   * @param {string} graphInterruptId - LangGraph 的 interrupt_id（同一批审批共享）
   * @param {number} [remainingPendingCount] - 后端返回的剩余待审批数量（低版本兼容回退到本地计算）
   * @returns {number|Array<{toolCallId: string, approvalState: string}>}
   *   当 remainingPendingCount 有效时返回 number，否则返回本地计算的数组
   */
  const _collectSiblingApprovals = (message, graphInterruptId, remainingPendingCount) => {
    // 优先使用后端权威计数
    if (remainingPendingCount != null) {
      return remainingPendingCount
    }

    // 回退：本地遍历 toolCalls 计算（低版本兼容）
    if (!message?.toolCalls || !Array.isArray(message.toolCalls) || !graphInterruptId) return []
    const siblings = []
    for (const tc of message.toolCalls) {
      const approval = tc.approval
      if (!approval) continue
      const tcGraphId = approval.graphInterruptId || approval.extra?.graphInterruptId
      if (tcGraphId === graphInterruptId) {
        siblings.push({
          toolCallId: tc.id || tc.toolCallId || '',
          approvalState: approval.state,
        })
      }
    }
    return siblings
  }

  /**
   * 审批事件处理
   *
   * payload 格式：
   * - interrupt_id: 审批中断 ID
   * - source: 'chat' | 'deep_research' | 'learning'
   * - source_id: session_id 或 task_id
   * - session_id: 关联的 chat session_id
   *
   * @param {string|null} sessionId - WebSocket 事件所属会话 ID（chat/learning/关联 deep_research）
   * @param {Object} payload - 审批事件 payload
   * @param {string} eventType - 事件类型（approval_pending / approval_approved 等）
   * @param {Object} options - 路由选项
   * @param {string} options.taskId - 深度研究任务 ID（独立 deep_research）
   * @param {string} options.source - 事件来源模块
   */
  const handleApprovalEvent = async (sessionId, payload, eventType, options = {}) => {
    const mappedState = APPROVAL_STATE_MAP[eventType]
    const source = options.source || payload.source || 'chat'
    const taskId = options.taskId || null
    // sessionId 为 schema 必填字段
    const chatSessionId = payload.sessionId
      || payload.chatSessionId
      || sessionId

    if (!mappedState) {
      logger.warn(`[Sync] handleApprovalEvent 未知审批事件类型: ${eventType}`)
      return
    }

    // 解析 payload 中的 graphInterruptId 字段（批量审批场景）
    const graphInterruptId = payload.graphInterruptId || payload.extra?.graphInterruptId

    // 先调用 approvalStore 更新 toolCall.approval.state，再处理 streamState 转换。
    // 否则 _collectSiblingApprovals 读到的是旧状态（pending），
    // approval_approved 事件无法触发 INTERRUPTED → STREAMING 转换。
    // 统一通过 approvalStore 处理所有审批状态变更（更新 toolCall.approval.state）
    // event.type 即为审批事件类型，state 从映射表获取
    const enrichedPayload = { ...payload, type: eventType, state: mappedState }
    // approval_approved/approval_rejected 事件需要设置 approved 字段供 approvalStore 路由
    if (eventType === 'approval_approved') enrichedPayload.approved = true
    if (eventType === 'approval_rejected') enrichedPayload.approved = false

    if (sessionId) {
      // chat / learning / 关联 deep_research：路由到 approvalStore.handleApprovalEvent
      // 会话不存在或消息为空时必须 await loadSessionDetail 完成，
      // 否则 approvalStore.handleApprovalEvent 内部的 setApprovalToToolCall 会因会话不存在而 return，
      // 审批事件丢失，跨浏览器状态不同步。
      const existingSession = getSession(sessionStore, chatSessionId)
      if (!existingSession || existingSession.messages.length === 0) {
        logger.info(`[Sync] handleApprovalEvent 会话不存在或消息为空，兜底拉取详情: session=${chatSessionId}`)
        await ensureSessionLoaded(sessionStore, chatSessionId, 'handleApprovalEvent')
      }
      approvalStore.handleApprovalEvent(enrichedPayload, { source, sessionId: chatSessionId })
    } else if (taskId) {
      // 独立 deep_research：路由到 approvalStore.handleApprovalEvent（统一入口）
      // sessionId 传 undefined（独立模式），taskId 传实际值。
      // approvalStore.handleApprovalEvent 会根据 payload 中的 session_id 推导 sessionId，
      // 若仍无 sessionId 则识别为独立 deep_research 模式，仅更新 pendingApprovals。
      approvalStore.handleApprovalEvent(enrichedPayload, { source, taskId })
      logger.info(`[Sync] 审批变更(独立深度研究): taskId=${taskId}, source=${source}, tool=${payload.toolName}, eventType=${eventType}, mappedState=${mappedState}, graphInterruptId=${graphInterruptId || '(none)'}`)
      return
    }

    // 2. 消息 streamState 转换（sync 的职责，仅 chat/learning/关联 deep_research 场景）
    // 审批恢复后有 token 级流式输出，需从 INTERRUPTED 转为 STREAMING
    // 优先按 payload.messageId / payload.extra.messageId 路由（重新生成非末尾消息场景），
    // 找不到时回退到最后一条 assistant 消息（保持原行为）
    //
    // approval_pending 是"流被审批中断"的权威信号：
    // 不依赖 streamState 当前值或 stream_event 是否已到达，
    // 无论 STREAMING/undefined/COMPLETED，approval_pending 到达即转为 INTERRUPTED。
    // 幂等保护：已是 INTERRUPTED 时不重复设置。
    //
    // 统一性：所有模块（chat/deep_research/learning/chat_deep_research）的审批事件
    // 均通过此函数处理，replay 和实时推送行为一致。
    if (mappedState === ApprovalState.PENDING && !streamingSessions.has(sessionId)) {
      const targetMsg = _findMessageByIdOrExtra(sessionId, payload) || _getLastAssistantMessage(sessionId)
      if (targetMsg && targetMsg.streamState !== StreamState.INTERRUPTED) {
        const prevState = targetMsg.streamState || 'undefined'
        targetMsg.streamState = StreamState.INTERRUPTED
        targetMsg.isStreaming = true
        logger.info(
          `[Sync] approval_pending ${prevState} → INTERRUPTED: ` +
          `session=${sessionId}, source=${source}, graphInterruptId=${graphInterruptId || '(none)'}, ` +
          `message=${targetMsg.backendId || targetMsg.id}`
        )
      }
    }

    // 2b. 批量审批：INTERRUPTED → STREAMING（当 sibling approved/processing 时）
    if (graphInterruptId) {
      // 批量审批场景，查询本地 store 中同一批次的 Approval 状态
      const targetMsg = _findMessageByIdOrExtra(sessionId, payload) || _getLastAssistantMessage(sessionId)
      if (targetMsg?.streamState === StreamState.INTERRUPTED && !streamingSessions.has(sessionId)) {
        const siblingApprovals = _collectSiblingApprovals(targetMsg, graphInterruptId, payload.remainingPendingCount)

        // remaining_pending_count 为权威计数时直接返回 number，> 0 表示仍有待审批 sibling
        if (typeof siblingApprovals === 'number') {
          if (siblingApprovals > 0) {
            logger.info(
              `[Sync] 批量审批仍有 ${siblingApprovals} 个待审批 (权威计数)，保持 INTERRUPTED: ` +
              `session=${sessionId}, source=${source}, graphInterruptId=${graphInterruptId}`
            )
          }
          // count === 0 时所有已决，但无法从 count 推断具体状态，不做额外转换
          // （approval_approved / approval_rejected 等单独事件会触发 streamState 更新）
        } else {
          // 回退：本地 toolCalls 遍历（低版本兼容）
          const hasApprovedOrProcessing = siblingApprovals.some(a =>
            a.approvalState === 'approved' ||
            a.approvalState === 'processing'
          )
          const allRejectedOrTimeout = siblingApprovals.length > 0 && siblingApprovals.every(a =>
            a.approvalState === 'rejected' || a.approvalState === 'timeout'
          )

          if (hasApprovedOrProcessing) {
            // 任一 sibling 处于 approved/processing，触发 INTERRUPTED → STREAMING
            // 注意：'waiting' 不触发此转换，approval_waiting 保持 INTERRUPTED
            targetMsg.streamState = StreamState.STREAMING
            logger.info(
              `[Sync] 批量审批 sibling approved/processing，INTERRUPTED → STREAMING: ` +
              `session=${sessionId}, source=${source}, graphInterruptId=${graphInterruptId}, ` +
              `message=${targetMsg.backendId || targetMsg.id}, siblings=${siblingApprovals.length}`
            )
          } else if (allRejectedOrTimeout) {
            // 所有 sibling 均为 rejected/timeout，保持 INTERRUPTED 并标记消息为 failed
            targetMsg.isFailed = true
            logger.info(
              `[Sync] 批量审批全部 rejected/timeout，标记消息 failed: ` +
              `session=${sessionId}, source=${source}, graphInterruptId=${graphInterruptId}, ` +
              `message=${targetMsg.backendId || targetMsg.id}, siblings=${siblingApprovals.length}`
            )
          }
        }
      }
    } else if ((mappedState === ApprovalState.APPROVED || mappedState === ApprovalState.PROCESSING) && !streamingSessions.has(sessionId)) {
      // 非批量 approval_approved / approval_processing：INTERRUPTED → STREAMING
      const targetMsg = _findMessageByIdOrExtra(sessionId, payload) || _getLastAssistantMessage(sessionId)
      if (targetMsg?.streamState === StreamState.INTERRUPTED) {
        targetMsg.streamState = StreamState.STREAMING
        logger.info(`[Sync] 非请求浏览器审批 ${mappedState}，INTERRUPTED → STREAMING: session=${sessionId}, source=${source}, message=${targetMsg.backendId || targetMsg.id}`)
      }
    }

    logger.info(`[Sync] 审批变更: session=${sessionId}, chatSession=${chatSessionId}, source=${source}, tool=${payload.toolName}, eventType=${eventType}, mappedState=${mappedState}, graphInterruptId=${graphInterruptId || '(none)'}`)
  }

  return {
    handleApprovalEvent,
  }
}
