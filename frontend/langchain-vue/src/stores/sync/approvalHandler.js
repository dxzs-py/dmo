import { logger } from '@/utils/logger'
import { StreamState, ApprovalState } from '@/types'
import { scheduleSubagentsRefresh } from '@/composables/useSubagents'
import { APPROVAL_STATE_MAP } from './constants'
import { getSession, getLastAssistantMessage } from './helpers'

/**
 * 创建审批事件处理器（三模块共享：chat / deep_research / learning）
 *
 * 装配方式：由 handleSessionEvent.js 工厂内直接创建（与 toolCallHandler 一致，
 * ctx 注入 sessionStore / approvalStore）；返回的 handleApprovalEvent 同时经
 * sync.js 返回值注入 handleTaskEvent，供 task 频道审批事件共享同一处理逻辑。
 *
 * 原实现位于 handleSessionEvent.js 内联（C5/cq-04 Task 2 下沉，行为保持）：
 * APPROVAL_STATE_MAP 映射 / 批量审批联动 / streamState 影响逻辑逐字搬运，
 * 主流程保持原版连续形态（内聚函数不按行数硬拆）；
 * 仅保留两个真实辅助：_findMessageByIdOrExtra（3 处复用的消息定位）、
 * _collectSiblingApprovals（批次审批状态收集查询）。
 *
 * @param {Object} ctx - 依赖上下文
 * @param {Object} ctx.sessionStore - session store 实例
 * @param {Object} ctx.approvalStore - approval store 实例
 * @returns {{
 *   handleApprovalEvent: (sessionId: string|null, payload: Object, eventType: string, options?: { taskId?: string, source?: string }) => Promise<void>,
 * }}
 */
export const createHandleApprovalEvent = (ctx) => {
  const { sessionStore, approvalStore } = ctx

  /**
   * 根据 payload.messageId 或 payload.extra.messageId 在指定会话中定位消息。
   * 用于 approval_* 等事件优先按 message_id 路由，避免 getLastAssistantMessage 兜底
   * 导致非末尾消息（如重新生成中途的旧消息）审批 UI 错位到最后一条。
   * @param {string} sessionId
   * @param {Object} payload
   * @returns {Object|null}
   */
  const _findMessageByIdOrExtra = (sessionId, payload) => {
    const messageId = payload?.messageId || payload?.extra?.messageId
    if (!messageId) return null
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session?.messages) return null
    return session.messages.find(m =>
      m.backendId?.toString() === messageId.toString() ||
      m.id?.toString() === messageId.toString()
    ) || null
  }

  /**
   * 收集同一 graph_interrupt_id 下所有 toolCall 的 approval 状态
   *
   * 优先使用后端返回的 remaining_pending_count（权威计数），避免因不同浏览器
   * toolCalls 事件到达顺序不一致导致本地计算结果不同；后端仅在 APPROVED + 批次
   * 场景注入该字段（approval_service 哨兵 _UNSET），未注入时本地遍历计算。
   *
   * @param {Object} message - 消息对象
   * @param {string} graphInterruptId - LangGraph 的 interrupt_id（同一批审批共享）
   * @param {number} [remainingPendingCount] - 后端返回的剩余待审批数量（未注入时本地计算）
   * @returns {number|Array<{toolCallId: string, approvalState: string}>}
   *   当 remainingPendingCount 有效时返回 number，否则返回本地计算的数组
   */
  const _collectSiblingApprovals = (message, graphInterruptId, remainingPendingCount) => {
    // 优先使用后端权威计数
    if (remainingPendingCount != null) {
      return remainingPendingCount
    }

    // 本地遍历 toolCalls 计算 sibling 状态（后端未注入权威计数时）
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
   * 审批事件处理（三模块共享：chat / deep_research / learning）
   *
   * 后端 Task 16+17 已改为每个 EventType 独立 ws_event_name，前端通过 event.type 直接区分
   * 6 个审批事件类型（approval_pending/processing/waiting/approved/rejected/timeout）。
   *
   * 通过 sessionId / options.taskId 自动路由：
   * - sessionId 存在（chat / learning / 关联 deep_research）→ approvalStore.handleApprovalEvent
   *   + sessionStore streamState 转换
   * - 仅 options.taskId 存在（独立 deep_research）→ approvalStore.handleApprovalEvent({ source, taskId })
   *   （researchStore 无 streamState 转换，深度研究状态由 task 通道事件单独管理）
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
    // isReplay 标记（useRealtimeSync.dispatchEvent 注入）：历史回放事件仅用于状态重建，
    // 不应触发 ElMessage / appendToLastMessage / 恢复流等副作用（否则重新打开页面重复提示）。
    const isReplay = options.isReplay === true
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

    // 子代理审批状态变化（pending/approved/rejected/timeout）→ 立即刷新子代理元数据：
    // SubAgentCard 审批可用性按子代理自身状态判断（审批自治）。pending 到达时后端
    // SubAgentInstance 状态变为 interrupted_pending_user_input（卡片应显示"等待你的确认"
    // 且审批按钮可用）；终态后恢复 running。两方向都需要拉取 /subagents 才能反映到卡片，
    // 不依赖下一个子代理工具事件，消除状态滞后窗口
    if (
      payload.subagentThreadId
      && (eventType === 'approval_pending'
        || eventType === 'approval_approved'
        || eventType === 'approval_rejected'
        || eventType === 'approval_timeout')
      && (chatSessionId || taskId)
    ) {
      // 父线程 id 取 payload.sourceId（深研=task_id，chat/learning=session_id），
      // 与 toolCallHandler 拉取口径一致（chat 关联深研子代理挂在 research task 下）
      scheduleSubagentsRefresh(payload.sourceId || chatSessionId || taskId)
    }

    if (sessionId) {
      // chat / learning / 关联 deep_research：路由到 approvalStore.handleApprovalEvent
      // 会话不存在或消息为空时必须 await loadSessionDetail 完成，
      // 否则 approvalStore.handleApprovalEvent 内部的 setApprovalToToolCall 会因会话不存在而 return，
      // 审批事件丢失，跨浏览器状态不同步。
      const existingSession = sessionStore.sessions.find(s => s.id === chatSessionId)
      if (!existingSession || existingSession.messages.length === 0) {
        logger.info(`[Sync] handleApprovalEvent 会话不存在或消息为空，兜底拉取详情: session=${chatSessionId}`)
        try {
          await sessionStore.loadSessionDetail(chatSessionId, { forceRefresh: true })
        } catch (e) {
          logger.warn(`[Sync] handleApprovalEvent 兜底加载会话失败: ${chatSessionId}`, e)
        }
      }
      approvalStore.handleApprovalEvent(enrichedPayload, { source, sessionId: chatSessionId, isReplay })
    } else if (taskId) {
      // 独立 deep_research：路由到 approvalStore.handleApprovalEvent（统一入口）
      // sessionId 传 undefined（独立模式），taskId 传实际值。
      // approvalStore.handleApprovalEvent 会根据 payload 中的 session_id 推导 sessionId，
      // 若仍无 sessionId 则识别为独立 deep_research 模式，仅更新 pendingApprovals。
      approvalStore.handleApprovalEvent(enrichedPayload, { source, taskId, isReplay })
      logger.info(`[Sync] 审批变更(独立深度研究): taskId=${taskId}, source=${source}, tool=${payload.toolName}, eventType=${eventType}, mappedState=${mappedState}, graphInterruptId=${graphInterruptId || '(none)'}`)
      return
    }

    // 2. 消息 streamState 转换（sync 的职责，仅 chat/learning/关联 deep_research 场景）：
    // approval_pending → INTERRUPTED
    //
    // 优先按 payload.messageId / payload.extra.messageId 路由（重新生成非末尾消息场景），
    // 找不到时回退到最后一条 assistant 消息（保持原行为）。
    //
    // approval_pending 是"流被审批中断"的权威信号：
    // 不依赖 streamState 当前值或 stream_event 是否已到达，
    // 无论 STREAMING/undefined/COMPLETED，approval_pending 到达即转为 INTERRUPTED。
    // 幂等保护：已是 INTERRUPTED 时不重复设置。
    //
    // 统一性：所有模块（chat/deep_research/learning/chat_deep_research）的审批事件
    // 均经此处理，replay 和实时推送行为一致。
    // 执行与连接解耦后所有浏览器均通过 WebSocket 接收事件，不再区分触发浏览器，
    // 审批挂起时消息统一转为 INTERRUPTED（请求浏览器由 sendMessage 主流程等待，
    // 审批恢复后经 approval_store 恢复为 STREAMING）。
    if (mappedState === ApprovalState.PENDING) {
      const targetMsg = _findMessageByIdOrExtra(sessionId, payload) || getLastAssistantMessage(getSession(sessionStore, sessionId))
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

    // 2b. 审批已决后的消息 streamState 转换：
    // - 批量审批（graphInterruptId 存在）：sibling approved/processing → STREAMING；
    //   全部 rejected/timeout → 保持 INTERRUPTED 并标记 failed；权威计数 > 0 → 保持
    // - 非批量 approval_approved / approval_processing：INTERRUPTED → STREAMING
    if (graphInterruptId) {
      // SubTask 11.2-11.4: 批量审批场景，查询本地 store 中同一批次的 Approval 状态
      const targetMsg = _findMessageByIdOrExtra(sessionId, payload) || getLastAssistantMessage(getSession(sessionStore, sessionId))
      if (targetMsg?.streamState === StreamState.INTERRUPTED) {
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
        } else {
          // 后端未注入权威计数（非 APPROVED 或非批次场景）时，本地遍历 toolCalls 计算 sibling 状态
          const hasApprovedOrProcessing = siblingApprovals.some(a =>
            a.approvalState === 'approved' ||
            a.approvalState === 'processing'
          )
          const allRejectedOrTimeout = siblingApprovals.length > 0 && siblingApprovals.every(a =>
            a.approvalState === 'rejected' || a.approvalState === 'timeout'
          )

          if (hasApprovedOrProcessing) {
            // SubTask 11.3: 任一 sibling 处于 approved/processing，触发 INTERRUPTED → STREAMING
            // 注意：'waiting' 不触发此转换，approval_waiting 保持 INTERRUPTED
            targetMsg.streamState = StreamState.STREAMING
            logger.info(
              `[Sync] 批量审批 sibling approved/processing，INTERRUPTED → STREAMING: ` +
              `session=${sessionId}, source=${source}, graphInterruptId=${graphInterruptId}, ` +
              `message=${targetMsg.backendId || targetMsg.id}, siblings=${siblingApprovals.length}`
            )
          } else if (allRejectedOrTimeout) {
            // SubTask 11.4: 所有 sibling 均为 rejected/timeout，保持 INTERRUPTED 并标记消息为 failed
            targetMsg.isFailed = true
            logger.info(
              `[Sync] 批量审批全部 rejected/timeout，标记消息 failed: ` +
              `session=${sessionId}, source=${source}, graphInterruptId=${graphInterruptId}, ` +
              `message=${targetMsg.backendId || targetMsg.id}, siblings=${siblingApprovals.length}`
            )
          }
        }
      }
    } else if (mappedState === ApprovalState.APPROVED || mappedState === ApprovalState.PROCESSING) {
      // 非批量 approval_approved / approval_processing：INTERRUPTED → STREAMING
      const targetMsg = _findMessageByIdOrExtra(sessionId, payload) || getLastAssistantMessage(getSession(sessionStore, sessionId))
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
