import { logger } from '@/utils/logger'
import { StreamState, ToolCallStatus, ApprovalState } from '@/types'
import {
  NON_TERMINAL_APPROVAL_STATES,
} from './constants'
import {
  findMessageById,
  getSession,
  getCurrentVersion,
  getLastAssistantMessage,
} from './helpers'

/**
 * @typedef {import('@/composables/useRealtimeSync').RealtimeEvent} RealtimeEvent
 */

/**
 * 创建流式状态机处理器
 *
 * 处理 stream_event / stream_started / stream_interrupted /
 * stream_completed / stream_finalized 事件，维护消息的 StreamState 状态转换。
 *
 * @param {Object} ctx - 依赖上下文
 * @param {Object} ctx.sessionStore - session store 实例
 * @param {Object} ctx.approvalStore - approval store 实例
 * @param {Object} ctx.researchStore - research store 实例
 * @param {Set<string>} ctx.streamingSessions - reactive(new Set())，请求浏览器 SSE 活跃会话集合
 * @param {Set<string>} ctx.thinkingSessions - reactive(new Set())，非触发浏览器"正在思考"集合
 * @param {(sessionId: string, options?: Object) => Promise<{backendMessages: Array}|null>} ctx.requestFullSync
 *   - 兜底全量同步函数
 * @param {(message: Object, sessionId?: string) => void} ctx.finalizeToolCalls
 *   - 来自 messageIntegrity，最终化非终态 toolCalls
 * @param {(sessionId: string, messageId: string, backendMessages: Array) => void} ctx.verifyMessageIntegrity
 *   - 来自 messageIntegrity，全量同步后完整性校验
 * @returns {{
 *   handleStreamEvent: (sessionId: string, payload: Object) => void,
 *   handleStreamStarted: (sessionId: string, payload: Object) => void,
 *   handleStreamInterrupted: (sessionId: string, payload: Object) => void,
 *   handleStreamCompleted: (sessionId: string, payload: Object) => void,
 *   handleStreamFinalized: (sessionId: string, payload: Object) => Promise<void>,
 * }}
 */
export const createStreamStateHandlers = (ctx) => {
  const {
    sessionStore,
    approvalStore,
    researchStore,
    streamingSessions,
    thinkingSessions,
    requestFullSync,
    finalizeToolCalls,
    verifyMessageIntegrity,
  } = ctx

  /**
   * 处理流式事件（非触发浏览器通过 WebSocket 接收）
   * 触发浏览器跳过此事件（已通过 SSE 实时处理）。
   * @param {string} sessionId
   * @param {Object} payload - stream_event 事件载荷
   * @param {string} payload.messageId - 消息 ID
   * @param {string} payload.event_type - 事件类型（reasoning/sources/suggestions/context/content_update）
   * @param {Object} payload.data - 事件数据
   * @param {number} [payload.seq] - 序列号（幂等保护）
   */
  const handleStreamEvent = (sessionId, payload) => {
    // 触发浏览器跳过（已通过 SSE 实时处理）
    if (streamingSessions.has(sessionId)) {
      logger.debug(`[Sync] stream_event 跳过（触发浏览器 SSE 活跃）: session=${sessionId}`)
      return
    }

    const { message_id, event_type, data, seq } = payload
    if (!message_id || !event_type) {
      logger.warn('[Sync] stream_event 缺少 message_id 或 event_type')
      return
    }

    // 查找消息
    const session = getSession(sessionStore, sessionId)
    if (!session?.messages) return
    const message = findMessageById(session, message_id)
    if (!message) {
      logger.debug(`[Sync] stream_event 未找到消息(时序竞态): session=${sessionId}, message=${message_id}`)
      return
    }

    // stream_event 设置 streamState=STREAMING（仅当 falsy 时）。
    // 作用：处理实时推送场景下 stream_event 先到达的情况，使后续 message_updated 能在
    // STREAMING 状态下处理（允许内容增长）。
    // 注意：approval_pending 现在是"流被审批中断"的权威信号（见 handleApprovalEvent 修复），
    // 不依赖 stream_event 是否已到达。replay 场景下 approval_pending 先到达设 INTERRUPTED，
    // stream_event 到达时 INTERRUPTED 是 truthy，不覆盖回 STREAMING。
    if (!message.streamState) {
      message.streamState = StreamState.STREAMING
      message.isStreaming = true
      logger.info(
        `[Sync] stream_event 设置 streamState=STREAMING: ` +
        `session=${sessionId}, message=${message_id}, type=${event_type}`
      )
    }

    // seq 幂等保护
    if (seq && message._lastStreamEventSeq && seq <= message._lastStreamEventSeq) {
      logger.debug(`[Sync] stream_event 重复事件跳过: seq=${seq}, lastSeq=${message._lastStreamEventSeq}`)
      return
    }

    // event_type → field 映射
    const fieldMap = {
      'stream_reasoning': 'reasoning',
      'stream_sources': 'sources',
      'stream_suggestions': 'suggestions',
      'stream_context': 'context',
      'stream_content_update': 'content',
    }
    const field = fieldMap[event_type]
    if (!field) {
      logger.warn(`[Sync] stream_event 未知 event_type: ${event_type}`)
      return
    }

    // stream_content_update 事件取 data.content，其他事件取 data
    const value = event_type === 'stream_content_update' ? data.content : data
    sessionStore.updateMessageFieldByBackendId(sessionId, message_id, field, value)

    if (seq) message._lastStreamEventSeq = seq
    logger.info(`[Sync] stream_event 处理: session=${sessionId}, message=${message_id}, type=${event_type}`)
  }

  /**
   * 流式输出被中断（stream_interrupted 事件）
   *
   * 事件来源：后端 views_chat.py 在 chat SSE 发送 deep_research 事件时发布，
   * 通过 WebSocket session 频道广播到所有浏览器。
   *
   * 语义：深度研究模式下，chat SSE 流被中断，Celery worker 仍在后台执行研究任务。
   * 消息进入 INTERRUPTED 状态，等待 Celery worker 完成后通过 stream_completed
   * （finalized=true，携带 task_id/final_report）回写结果。
   *
   * 处理逻辑（所有浏览器统一）：
   * - 设置消息的 researchTaskId（让 ChatMessage.reasoningMode='deep-research'，
   *   AiReasoning 显示"正在进行深度研究"而非"正在思考"）
   * - 设置消息的 streamState=INTERRUPTED（避免 showContinueResearch 提前显示"继续研究"按钮）
   * - 设置消息的 isStreaming=true（让 UI 显示思考动画）
   * - 标记 thinkingSessions（让 ChatView 的 isStreaming=true）
   *
   * 幂等性：
   * - 触发浏览器已通过 SSE deep_research 事件（setDeepResearchTask）完成上述设置，
   *   此事件对触发浏览器是幂等的。
   * - 非触发浏览器只能通过此事件感知深度研究模式，此事件是必需的。
   *
   * @param {string} sessionId
   * @param {Object} payload - payload.data 包含 task_id、message_id 等字段
   */
  const handleStreamInterrupted = (sessionId, payload) => {
    const session = getSession(sessionStore, sessionId)
    if (!session?.messages) {
      // 会话未加载，仅标记 thinkingSessions，等会话加载后由后续事件补偿
      thinkingSessions.add(sessionId)
      logger.info(`[Sync] stream_interrupted 会话不存在，标记正在思考: session=${sessionId}`)
      return
    }

    // payload 可能有两种结构（entry-point toCamelCase 已转换，均为 camelCase）：
    // 1. 嵌套：{ source, sourceId, sessionId, messageId, data: { taskId, ... } }
    // 2. 扁平：{ source, sourceId, sessionId, messageId, taskId, ... }
    const taskId = payload.taskId || (payload.data && payload.data.taskId) || null
    const messageId = payload.messageId || (payload.data && payload.data.messageId) || null

    // 定位目标消息（优先按 messageId，兜底最后一条 assistant 消息）
    let targetMsg = null
    if (messageId) {
      targetMsg = findMessageById(session, messageId)
    }
    if (!targetMsg) {
      targetMsg = getLastAssistantMessage(session)
    }

    if (!targetMsg) {
      logger.warn(`[Sync] stream_interrupted 未找到目标消息: session=${sessionId}, message=${messageId || '(兜底)'}`)
      thinkingSessions.add(sessionId)
      return
    }

    // 设置 researchTaskId（让 ChatMessage.reasoningMode='deep-research'）
    if (taskId && !targetMsg.researchTaskId) {
      targetMsg.researchTaskId = taskId
      // 同步到 versions[currentVersion]
      const ver = getCurrentVersion(targetMsg)
      if (ver) ver.researchTaskId = taskId
    }

    // 设置 streamState=INTERRUPTED（避免 showContinueResearch 提前显示"继续研究"按钮）
    // 仅在非终态时设置，避免覆盖已完成消息的状态
    if (targetMsg.streamState !== StreamState.COMPLETED
        && targetMsg.streamState !== StreamState.ERROR) {
      targetMsg.streamState = StreamState.INTERRUPTED
      targetMsg.isStreaming = true
      // 同步到 versions[currentVersion]
      const ver = getCurrentVersion(targetMsg)
      if (ver) {
        ver.streamState = StreamState.INTERRUPTED
        ver.isStreaming = true
      }
    }

    // 标记 thinkingSessions（让 ChatView 的 isStreaming=true，UI 显示思考动画）
    thinkingSessions.add(sessionId)

    logger.info(
      `[Sync] stream_interrupted 消息进入 INTERRUPTED 状态: session=${sessionId}, ` +
      `message=${messageId || '(兜底)'}, task=${taskId || '(无)'}`
    )
  }

  /**
   * 流式输出开始（stream_started 事件）
   *
   * 事件来源：后端 research_runner.py 在 execute_research_async 开始时发布，
   * 通过 WebSocket session 频道广播到所有浏览器。
   *
   * 处理逻辑：
   * - 触发浏览器（streamingSessions 中）：SSE 已在本地管理 isStreaming 状态，跳过。
   * - 非触发浏览器：标记会话为"正在思考"，设置目标消息 isStreaming=true、streamState=STREAMING，
   *   使 UI 显示"正在思考"指示器。后续 stream_completed 事件会清理此状态。
   *
   * @param {string} sessionId
   * @param {Object} payload - 包含 task_id、message_id、source 等字段
   */
  const handleStreamStarted = (sessionId, payload) => {
    // 触发浏览器通过 SSE 本地管理 isStreaming 状态，跳过 WebSocket 事件
    if (streamingSessions.has(sessionId)) {
      logger.debug(`[Sync] stream_started 跳过（触发浏览器 SSE 活跃）: session=${sessionId}`)
      return
    }

    const session = getSession(sessionStore, sessionId)
    if (!session?.messages) {
      // 会话不存在时仍标记 thinkingSessions，等会话加载后由 stream_completed 清理
      thinkingSessions.add(sessionId)
      logger.info(`[Sync] stream_started 会话不存在，标记正在思考: session=${sessionId}`)
      return
    }

    // 定位目标消息（优先按 messageId，兜底最后一条 assistant 消息）
    const messageId = payload.messageId
    let targetMsg = null
    if (messageId) {
      targetMsg = findMessageById(session, messageId)
    }
    if (!targetMsg) {
      targetMsg = getLastAssistantMessage(session)
    }

    if (targetMsg) {
      // 非触发浏览器：标记会话为"正在思考"
      thinkingSessions.add(sessionId)

      // 深度研究模式：消息处于 INTERRUPTED 状态（触发浏览器 SSE 已结束，由后端 stream_started 广播）
      // 此时非触发浏览器不应推进 streamState，仅设置 isStreaming=true，
      // 由 ChatMessage.vue 的 AiReasoning 根据 streamState=INTERRUPTED + researchTaskId
      // 显示"正在进行深度研究"文案；若覆盖为 STREAMING 会破坏深度研究卡片的运行态展示。
      if (targetMsg.streamState === StreamState.INTERRUPTED) {
        targetMsg.isStreaming = true
        logger.info(`[Sync] stream_started 非触发浏览器标记正在进行深度研究: session=${sessionId}, message=${messageId || '(兜底)'}`)
        return
      }

      // 仅在非终态时设置 STREAMING，避免覆盖已完成消息的状态
      if (targetMsg.streamState !== StreamState.COMPLETED
          && targetMsg.streamState !== StreamState.ERROR) {
        targetMsg.isStreaming = true
        targetMsg.streamState = StreamState.STREAMING
      }
      logger.info(`[Sync] stream_started 非触发浏览器标记正在思考: session=${sessionId}, message=${messageId || '(兜底)'}, source=${payload.source || '(none)'}`)
    } else {
      // 无目标消息时仍标记 thinkingSessions
      thinkingSessions.add(sessionId)
      logger.info(`[Sync] stream_started 未找到目标消息，标记正在思考: session=${sessionId}`)
    }
  }

  /**
   * 处理 stream_completed 事件（session 频道）
   *
   * 模块关系：
   * - 深度研究模块与聊天模块相互独立，各自维护自身状态。
   * - 仅"聊天模块的深度研究模式"（关联场景）需要两模块实时同步：
   *   聊天模块处理消息回写，同时委托更新深度研究模块的 taskInfo。
   * - 独立深度研究模式不走 session 频道，由 handleTaskEvent 的 stream_completed
   *   分支单独处理 taskInfo 更新，两路径互不干扰。
   *
   * 职责：
   * 1. 处理 chat 消息回写（content/reasoning/streamState 更新）
   * 2. 关联场景下委托更新 researchStore.taskInfo（payload.taskId 存在且 hasResearchResult）
   *    解耦 taskInfo 更新与 task 频道订阅状态，确保 DeepResearchView 未打开时 taskInfo 也实时更新
   *    幂等性：与 handleTaskEvent 的 stream_completed 分支形成双路径，updateTaskFromEvent 使用 force:true 保证一致
   *
   * 事件语义（新）：
   * - finalized=false：后端 generator 刚结束，但前端 PATCH 尚未完成。
   *   非请求浏览器不应触发全量同步（会拿到陈旧 content 覆盖本地）。
   * - finalized=true 或字段缺失（旧后端兼容）：可安全触发全量同步。
   *
   * @param {string} sessionId
   * @param {Object} payload
   */
  const handleStreamCompleted = (sessionId, payload) => {
    const session = getSession(sessionStore, sessionId)
    if (!session?.messages) return

    // 深度研究模式下，chat SSE 结束时发布的 stream_completed（finalized !== true）
    // 不应让前端误判研究完成。当 finalized !== true 且消息处于 INTERRUPTED 状态时，
    // 忽略该事件（不更新消息状态，不显示"研究已完成"/"继续研究"按钮），仅由事件队列推进 seq。
    // 真正的研究完成事件由 Celery worker 通过 broadcast_stream_completed 发布
    //（finalized=true，携带 task_id/final_report），届时正常进入下方研究结果回写逻辑。
    // 普通聊天模式（无 finalized 字段或 finalized=false）消息不会处于 INTERRUPTED 状态，
    // 不受此判断影响，行为与原有逻辑一致。
    if (payload.finalized !== true) {
      const earlyMessageId = payload.messageId
      let earlyTargetMsg = null
      if (earlyMessageId) {
        earlyTargetMsg = findMessageById(session, earlyMessageId)
      }
      if (!earlyTargetMsg) {
        earlyTargetMsg = getLastAssistantMessage(session)
      }
      if (earlyTargetMsg && earlyTargetMsg.streamState === StreamState.INTERRUPTED) {
        logger.info(
          `[Sync] stream_completed(finalized=${payload.finalized}) 消息处于 INTERRUPTED（深度研究模式），` +
          `忽略事件: session=${sessionId}, message=${earlyMessageId || '(兜底)'}`
        )
        return
      }
    }

    // === 处理深度研究最终结果 ===
    // 深度研究完成事件由 Celery worker 回写时发布，始终携带 task_id 和 finalized=true，
    // 与聊天 SSE 结束时的 stream_completed（无 task_id, finalized=false）区分。
    // 即使 final_report 为空字符串（AI 回复过短且磁盘文件提取失败），也需进入回写逻辑：
    // 1. 标记消息 COMPLETED + isStreaming=false（解除 FINALIZING 卡死状态）
    // 2. 触发 requestFullSync 从后端拉取 writeback_to_chat_message 已写入的正确内容
    const hasResearchResult = payload.taskId && (payload.finalReport !== undefined || payload.error)
    if (hasResearchResult) {
      const messageId = payload.messageId
      let targetMsg = null
      if (messageId) {
        targetMsg = findMessageById(session, messageId)
      }
      if (!targetMsg) {
        // 通过 researchTaskId 查找关联消息
        targetMsg = [...session.messages].reverse().find(m =>
          m.role === 'assistant' && m.researchTaskId === payload.taskId
        )
      }
      if (!targetMsg) {
        // 最后一个 assistant 消息
        targetMsg = getLastAssistantMessage(session)
      }

      if (!targetMsg) {
        // 极端情况：无 assistant 消息，仅触发全量同步
        logger.warn(`[Sync] 深度研究结果回写但未找到目标消息: session=${sessionId}, task=${payload.taskId}`)
        requestFullSync(sessionId)
        return
      }

      if (targetMsg) {
        // 计算深度研究耗时并设置到 reasoning.duration
        // 触发浏览器: chat SSE 很快结束，message.timestamp 接近深度研究开始时间
        // 非触发浏览器: message_added 事件在 chat SSE 开始时发布，timestamp 也接近深度研究开始时间
        // 注意：mergeMessageFromBackend 的 reasoning 完成态保护（message-operations.js）
        // 在 streamState=COMPLETED 且本地 reasoning 非空时不用后端覆盖，
        // 故此处设置的 duration 和 content 均不会被 requestFullSync 覆盖
        if (targetMsg.timestamp) {
          const researchDuration = Math.ceil((Date.now() - targetMsg.timestamp) / 1000)
          if (researchDuration > 0) {
            if (!targetMsg.reasoning) {
              targetMsg.reasoning = { content: '' }
            }
            targetMsg.reasoning = { ...targetMsg.reasoning, duration: researchDuration }
          }
        }
        // 更新 reasoning.content 为完成态消息
        // 避免完成后展开 AiReasoning 仍显示旧的"正在调度深度研究工作流..."内容
        if (targetMsg.reasoning) {
          const successMsg = payload.success !== false
            ? '深度研究已完成'
            : `深度研究执行失败：${payload.error || '未知错误'}`
          targetMsg.reasoning = { ...targetMsg.reasoning, content: successMsg }
        }
        // 更新消息内容
        if (payload.success !== false && payload.finalReport) {
          targetMsg.content = payload.finalReport
        } else if (payload.error) {
          targetMsg.content = `深度研究执行失败：${payload.error}`
        }
        targetMsg.isStreaming = false
        targetMsg.streamState = StreamState.COMPLETED
        // 深度研究任务完成，清理非触发浏览器的"正在思考"状态
        thinkingSessions.delete(sessionId)

        // 深度研究任务完成，清理所有工具审批状态
        // 防止研究结束后详情中仍残留审批状态
        // 统一清理 pending/processing/waiting 三种非终态审批为对应终态。
        // waiting 状态由批量审批场景下 _handleProcessed 设置（同批次还有 pending 时），
        // 若研究完成时仍有工具卡在 waiting，将永久显示"等待其他审批"。
        let pendingApprovalCount = 0
        let runningToolCount = 0
        if (targetMsg.toolCalls && Array.isArray(targetMsg.toolCalls)) {
          for (const tc of targetMsg.toolCalls) {
            if (NON_TERMINAL_APPROVAL_STATES.includes(tc.approval?.state)) pendingApprovalCount++
            if (tc.status === 'pending_approval' || tc.status === 'running') runningToolCount++
            // 对于仍在 pending/processing/waiting 状态的审批，研究完成/失败后强制清理为终态
            if (tc.approval && NON_TERMINAL_APPROVAL_STATES.includes(tc.approval.state)) {
              tc.approval.state = payload.success !== false ? ApprovalState.APPROVED : ApprovalState.REJECTED
            }
            // 如果工具还是 pending_approval/running/approved 状态，研究都结束了，根据实际情况设置
            // approved 也需纳入：审批通过但 tool_call_completed 事件丢失时状态会卡住（issue_new_c）
            if (tc.status === 'pending_approval' || tc.status === 'running' || tc.status === ToolCallStatus.APPROVED) {
              if (tc.result || tc.output) {
                tc.status = ToolCallStatus.COMPLETED
              } else {
                tc.status = payload.success !== false ? ToolCallStatus.COMPLETED : ToolCallStatus.FAILED
              }
            }
          }
        }
        // 同步清理 versions 中的审批状态
        const ver = getCurrentVersion(targetMsg)
        if (ver?.toolCalls) {
          for (const tc of ver.toolCalls) {
            if (tc.approval && NON_TERMINAL_APPROVAL_STATES.includes(tc.approval.state)) {
              tc.approval.state = payload.success !== false ? ApprovalState.APPROVED : ApprovalState.REJECTED
            }
            if (tc.status === 'pending_approval' || tc.status === 'running') {
              if (tc.result || tc.output) {
                tc.status = ToolCallStatus.COMPLETED
              } else {
                tc.status = payload.success !== false ? ToolCallStatus.COMPLETED : ToolCallStatus.FAILED
              }
            }
          }
        }
        // 清理 pendingApprovals 中属于这个任务的审批
        let clearedApprovalCount = 0
        if (payload.taskId) {
          clearedApprovalCount = approvalStore.clearByTaskId(payload.taskId)
        }

        logger.info(
          `[Sync] 深度研究结果回写 - 审批状态清理: ` +
          `session=${sessionId}, task=${payload.taskId}, ` +
          `success=${payload.success !== false}, ` +
          `toolCalls总数=${targetMsg.toolCalls?.length || 0}, ` +
          `清理前pending审批数=${pendingApprovalCount}, ` +
          `清理前运行中工具数=${runningToolCount}, ` +
          `clearByTaskId清除数=${clearedApprovalCount}`
        )

        logger.info(`[Sync] 深度研究结果回写: session=${sessionId}, task=${payload.taskId}, success=${payload.success !== false}`)

        // 触发全量同步确保数据一致性
        requestFullSync(sessionId)

        // 委托更新 researchStore.taskInfo（关联 chat 场景主路径）
        // 解耦 taskInfo 更新与 task 频道订阅状态，确保 DeepResearchView 未打开时 taskInfo 也实时更新
        // 幂等性：与 handleTaskEvent 的 stream_completed 分支形成双路径，updateTaskFromEvent 使用 force:true 保证一致
        if (payload.taskId) {
          try {
            researchStore.updateTaskFromEvent(payload.taskId, payload)
            logger.info(
              `[Sync] handleStreamCompleted 委托更新 taskInfo: ` +
              `taskId=${payload.taskId}, success=${payload.success !== false}`
            )
          } catch (err) {
            logger.warn(
              `[Sync] handleStreamCompleted 委托更新 taskInfo 失败: ` +
              `taskId=${payload.taskId}, error=${err?.message || err}`
            )
          }
        }

        return
      }
    }
    // === 深度研究结果处理结束 ===

    const messageId = payload.messageId
    let targetMsg = null
    if (messageId) {
      targetMsg = findMessageById(session, messageId)
    }
    if (!targetMsg) {
      targetMsg = getLastAssistantMessage(session)
    }

    if (!targetMsg) {
      logger.info(`[Sync] 流式完成但未找到目标消息: session=${sessionId}, message=${messageId || '(兜底)'}`)
      return
    }

    // 记录后端 content_length 用于早期可观测性
    // 实际完整性校验由 verifyMessageIntegrityAfterSync 通过后端快照对比完成
    if (typeof payload.contentLength === 'number') {
      const localLen = (targetMsg.content || '').length
      if (payload.contentLength > localLen) {
        logger.warn(
          `[Sync] stream_completed 后端 contentLength 大于本地: ` +
          `session=${sessionId}, message=${messageId || '(兜底)'}, ` +
          `local=${localLen}, backend=${payload.contentLength}`
        )
      }
    } else {
      logger.info(
        `[Sync] 流式完成: session=${sessionId}, local=${localLen}, backend=${payload.contentLength}`
        )
      }
    }

    const isRequestBrowser = streamingSessions.has(sessionId)
    // finalized 字段：false 表示后端尚未确认 PATCH 完成；true 或 undefined（旧后端）表示可安全同步
    const finalized = payload.finalized !== false

    if (isRequestBrowser) {
      // 仅在 onStreamEnd 已完成 PATCH（状态为 SYNCING）时才允许兜底 COMPLETED
      // FINALIZING 状态表示 PATCH 尚未完成，不应绕过
      if (finalized
          && targetMsg.streamState === StreamState.SYNCING) {
        targetMsg.streamState = StreamState.COMPLETED
        targetMsg.isStreaming = false
        logger.info(`[Sync] stream_completed 兜底 syncing→completed: session=${sessionId}`)

        // 请求浏览器也需要最终化 toolCalls 状态
        // 场景：WebSocket tool_call_completed 事件丢失或未到达时，
        // toolCall.status 可能卡在 pending/running，导致 UI 永久显示"执行中"
        finalizeToolCalls(targetMsg, sessionId)
      } else if (finalized
                 && targetMsg.streamState === StreamState.FINALIZING) {
        // FINALIZING 状态：PATCH 尚未完成，不兜底，等待 onStreamEnd 完成
        logger.info(`[Sync] stream_completed 收到时请求浏览器处于 finalizing，等待 PATCH 完成: session=${sessionId}`)
      } else if (targetMsg.streamState === StreamState.STREAMING
                 || targetMsg.streamState === StreamState.FINALIZING
                 || targetMsg.streamState === StreamState.SYNCING) {
        logger.info(`[Sync] stream_completed 收到时请求浏览器消息状态为 ${targetMsg.streamState}，等待本地流程: session=${sessionId}, finalized=${finalized}`)
      } else if (finalized && targetMsg.streamState === StreamState.COMPLETED) {
        // 消息已 COMPLETED：确保 toolCalls 也最终化（可能 WebSocket 事件乱序导致 toolCall 未更新）
        finalizeToolCalls(targetMsg, sessionId)
        logger.info(`[Sync] stream_completed 请求浏览器消息已 completed，最终化 toolCalls: session=${sessionId}`)
      } else {
        logger.info(`[Sync] stream_completed 收到时请求浏览器消息状态为 ${targetMsg.streamState}，不切换: session=${sessionId}`)
      }
      return
    }

    // 非请求浏览器
    if (!finalized) {
      // 后端尚未确认 PATCH 完成：仅标记 FINALIZING，不触发全量同步
      // 真正的全量同步由 stream_finalized 事件触发

      // 深度研究非触发浏览器：stream_completed(finalized=false) 是 SSE 结束事件，
      // 但 Celery 后台任务仍在执行。保持 STREAMING + isStreaming=true，
      // 让"正在思考"持续显示，直到 stream_completed(携带 task_id/final_report) 到达。
      if (thinkingSessions.has(sessionId)) {
        logger.info(`[Sync] stream_completed(finalized=false) 深度研究非触发浏览器保持正在思考: session=${sessionId}`)
        return
      }

      if (targetMsg.streamState === StreamState.STREAMING) {
        targetMsg.streamState = StreamState.FINALIZING
        targetMsg.isStreaming = false
        logger.info(`[Sync] stream_completed(finalized=false) 标记 finalizing，不触发全量同步: session=${sessionId}`)

        // 如果 stream_finalized 15 秒内未到达（请求浏览器 chatFinalize 失败等），
        // 主动全量同步并标记 COMPLETED，防止永久卡在 FINALIZING
        const msgId = targetMsg.backendId || targetMsg.id
        setTimeout(() => {
          const s = getSession(sessionStore, sessionId)
          const m = s?.messages?.find(m =>
            (m.backendId || m.id) === msgId
          )
          if (m?.streamState === StreamState.FINALIZING) {
            logger.warn(`[Sync] FINALIZING 超时(15s)，主动全量同步: session=${sessionId}`)
            requestFullSync(sessionId).then(() => {
              if (m.streamState === StreamState.FINALIZING) {
                m.streamState = StreamState.COMPLETED
                m.isStreaming = false
              }
            })
          }
        }, 15000)
      } else {
        logger.info(`[Sync] stream_completed(finalized=false) 消息状态 ${targetMsg.streamState}，保持: session=${sessionId}`)
      }
      return
    }

    // finalized=true（或旧后端）：可安全标记 COMPLETED 并触发全量同步
    // INTERRUPTED 是终态（审批超时、用户主动中断等），不应被 stream_completed 覆盖：
    // 审批恢复时由 handleApprovalEvent 先将 INTERRUPTED → STREAMING，
    // 再由后续 stream_completed 自然推进到 COMPLETED；若此刻仍为 INTERRUPTED，
    // 说明审批未恢复，保持终态。与 handleStreamFinalized 行为对齐。
    if (targetMsg.streamState !== StreamState.COMPLETED
        && targetMsg.streamState !== StreamState.FINALIZING
        && targetMsg.streamState !== StreamState.SYNCING
        && targetMsg.streamState !== StreamState.INTERRUPTED) {
      targetMsg.streamState = StreamState.COMPLETED
      targetMsg.isStreaming = false
      // 非请求浏览器标记 COMPLETED 后，立即最终化所有非终态 toolCalls
      // 防止 WebSocket tool_call_completed 事件丢失或乱序导致 toolCall 卡在 pending/running/waiting
      // 与请求浏览器路径（isRequestBrowser 分支）行为对齐，确保跨浏览器工具状态一致
      finalizeToolCalls(targetMsg, sessionId)
    }
    // 清理非触发浏览器的"正在思考"状态（finalized=true 表示流式已最终化）
    thinkingSessions.delete(sessionId)
    // finalized=true 时后端数据已是权威，非 FINALIZING/SYNCING 态均触发全量同步
    // 全量同步完成后，校验 tool_calls 数量和 content 长度（工具卡片 + AI 内容完整性）
    if (targetMsg.streamState !== StreamState.FINALIZING
        && targetMsg.streamState !== StreamState.SYNCING) {
      const targetMessageId = messageId
      || targetMsg.backendId?.toString()
      || targetMsg.id?.toString()
      requestFullSync(sessionId).then((result) => {
        if (!result?.backendMessages) return
        verifyMessageIntegrity(sessionId, targetMessageId, result.backendMessages)
      })
    }

    logger.info(`[Sync] 流式完成: session=${sessionId}, message=${messageId || '(兜底)'}, finalized=${finalized}`)
  }

  /**
   * 流式最终化完成（stream_finalized 事件）
   *
   * 语义：请求浏览器已完成 PATCH 同步，后端 content 已持久化。
   * 非请求浏览器收到此事件后，可安全拉取后端数据（不会拿到陈旧 content）。
   *
   * 处理逻辑：
   * - 请求浏览器：本地数据已是权威，直接标记 COMPLETED，不触发全量同步
   * - 非请求浏览器：在状态非 COMPLETED 时触发全量同步，通过 allowContentMerge=true
   *   显式声明允许后端 content 覆盖本地（保留 streamState 不被修改，保护态语义完整）；
   *   同步完成后再标记 COMPLETED（纳入保护态，防止后续覆盖）
   * - INTERRUPTED/ERROR 是终态，不应被 stream_finalized 覆盖
   *
   * @param {string} sessionId
   * @param {Object} payload
   */
  const handleStreamFinalized = async (sessionId, payload) => {
    // 清理非触发浏览器的"正在思考"状态（stream_finalized 事件标志最终化完成）
    thinkingSessions.delete(sessionId)
    const session = getSession(sessionStore, sessionId)
    if (!session?.messages) return

    const messageId = payload.messageId
    let targetMsg = null
    if (messageId) {
      targetMsg = findMessageById(session, messageId)
    }
    if (!targetMsg) {
      targetMsg = getLastAssistantMessage(session)
    }

    if (!targetMsg) {
      logger.info(`[Sync] stream_finalized 未找到目标消息: session=${sessionId}, message=${messageId || '(兜底)'}`)
      return
    }

    // ERROR 是终态，不应被 stream_finalized 覆盖
    // INTERRUPTED 需要区分：如果审批已恢复（handleApprovalEvent 已转为 STREAMING），
    // 则不应再被拦截；如果仍为 INTERRUPTED 说明审批未恢复，保持不变
    if (targetMsg.streamState === StreamState.ERROR) {
      logger.info(`[Sync] stream_finalized 消息处于 ERROR，保持: session=${sessionId}`)
      return
    }
    if (targetMsg.streamState === StreamState.INTERRUPTED) {
      logger.info(`[Sync] stream_finalized 消息仍处于 INTERRUPTED（审批未恢复），保持: session=${sessionId}`)
      return
    }

    const isRequestBrowser = streamingSessions.has(sessionId)
    const wasCompleted = targetMsg.streamState === StreamState.COMPLETED

    if (isRequestBrowser) {
      // 请求浏览器：本地数据已是权威，直接标记 COMPLETED
      targetMsg.streamState = StreamState.COMPLETED
      targetMsg.isStreaming = false
      logger.info(`[Sync] stream_finalized 请求浏览器标记 completed: session=${sessionId}, message=${messageId || '(兜底)'}, wasCompleted=${wasCompleted}`)
      return
    }

    // 非请求浏览器：先触发全量同步（此时本地状态非 COMPLETED，后端数据可合并）
    // 关键：不在同步前标记 COMPLETED，否则 COMPLETED 保护态会阻止后端数据合并。
    // FINALIZING/SYNCING 等保护态下，通过 allowContentMerge=true 显式声明允许 content 覆盖：
    // stream_finalized 表示后端 PATCH 已完成，content 为权威最终内容，
    // 不需要修改 streamState 来绕过保护态，保留状态语义完整性。
    if (!wasCompleted) {
      const prevState = targetMsg.streamState
      logger.info(`[Sync] stream_finalized 非请求浏览器触发全量同步: session=${sessionId}, message=${messageId || '(兜底)'}, prevState=${prevState}`)
      await requestFullSync(sessionId, { allowContentMerge: true })
    }

    // 同步完成后再标记 COMPLETED（纳入保护态，防止后续覆盖）
    // 重新查找 targetMsg，因为 requestFullSync 可能替换了消息对象引用
    const refreshedSession = getSession(sessionStore, sessionId)
    const refreshedTarget = messageId
      ? findMessageById(refreshedSession, messageId)
      : getLastAssistantMessage(refreshedSession)

    if (refreshedTarget
        && refreshedTarget.streamState !== StreamState.INTERRUPTED
        && refreshedTarget.streamState !== StreamState.ERROR) {
      refreshedTarget.streamState = StreamState.COMPLETED
      refreshedTarget.isStreaming = false
      logger.info(`[Sync] stream_finalized 非请求浏览器同步后标记 completed: session=${sessionId}, message=${messageId || '(兜底)'}`)
    }
  }

  return {
    handleStreamEvent,
    handleStreamStarted,
    handleStreamInterrupted,
    handleStreamCompleted,
    handleStreamFinalized,
  }
}
