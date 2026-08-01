import { logger } from '@/utils/logger'
import { transformBackendMessageToFrontend } from '@/utils/session-transformers'
import { mergeMessageFromBackend, createMessageVersion } from '@/utils/message-operations'
import { StreamState, ToolCallStatus, ApprovalState, PROTECTED_STREAM_STATES } from '@/types'
import {
  TOOL_CALL_STATUS_MAP,
  TOOL_CALL_RESULT_STATUSES,
  APPROVAL_STATE_MAP,
} from './constants'

/**
 * @typedef {import('@/composables/useRealtimeSync').RealtimeEvent} RealtimeEvent
 */

/**
 * 创建 session 通道事件处理器（最大模块）
 *
 * 原逻辑位于 sync.js L186-195（handleSessionEvent）+ L431-467（_getSessionId /
 * _getTargetMessageStreamState）+ L507-679（applySessionEvent）+ L686-770（handleMessageAdded）
 * + L778-885（handleMessageUpdated）+ L897-963（handleStreamEvent）+ L970-977（handleMessagesDeleted）
 * + L996-1094（handleToolCallEvent）+ L1216-1220（getLastAssistantMessage）
 * + L1230-1239（_findMessageByIdOrExtra）+ L1266-1388（handleApprovalEvent）
 * + L1396-1411（_collectSiblingApprovals）+ L1438-1501（handleStreamInterrupted）
 * + L1517-1571（handleStreamStarted）+ L1590-1645（_finalizeToolCallsForCompletedMessage）
 * + L1666-1723（_verifyMessageIntegrityAfterSync）+ L1749-2080（handleStreamCompleted）
 * + L2098-2172（handleStreamFinalized）+ L2194-2280（handleMessageRegenerated）
 * + L2291-2347（handleMessageRegenerateReverted）。
 *
 * @param {Object} ctx - 依赖上下文
 * @param {Object} ctx.sessionStore - session store 实例
 * @param {Object} ctx.approvalStore - approval store 实例
 * @param {Object} ctx.researchStore - research store 实例
 * @param {{ getPrevSeq: Function, setSeenSeq: Function, shouldSkip: Function }} ctx.seqDedup
 *   - 来自 createSeqDedup，提供 seq 去重能力
 * @param {{ process: Function }} ctx.orderedQueue
 *   - 来自 createOrderedQueue，提供有序队列处理能力
 * @param {Set<string>} ctx.streamingSessions - reactive(new Set())，请求浏览器 SSE 活跃会话集合
 * @param {Set<string>} ctx.thinkingSessions - reactive(new Set())，非触发浏览器"正在思考"集合
 * @param {Set<string>} ctx.fullSyncPending - 避免重复触发全量同步的 Set
 * @param {(sessionId: string, options?: Object) => Promise<{backendMessages: Array}|null>} ctx.requestFullSync
 *   - 兜底全量同步函数（由 sync.js 主文件装配后传入）
 * @returns {{
 *   handleSessionEvent: (event: RealtimeEvent) => Promise<void>,
 *   applySessionEvent: (event: RealtimeEvent) => Promise<void>,
 *   handleToolCallEvent: (sessionId: string|null, taskId: string|null, payload: Object, eventType: string, source?: string) => Promise<void>,
 *   handleApprovalEvent: (sessionId: string|null, payload: Object, eventType: string, options?: { taskId?: string, source?: string }) => Promise<void>,
 *   handleMessageUpdated: (sessionId: string, messageId: string, fields: Object) => void,
 *   handleStreamCompleted: (sessionId: string, payload: Object) => void,
 * }}
 */
export const createHandleSessionEvent = (ctx) => {
  const {
    sessionStore,
    approvalStore,
    researchStore,
    seqDedup,
    orderedQueue,
    streamingSessions,
    thinkingSessions,
    fullSyncPending,
    requestFullSync,
  } = ctx

  /**
   * 处理 session 通道事件（由 ChatView 等订阅方回调）
   *
   * session_id 为 schema 必填字段，直接从 payload.session_id 获取。
   * @param {RealtimeEvent} event
   */
  const handleSessionEvent = async (event) => {
    const sessionId = event.payload?.session_id
      || event.session_id
    if (!sessionId) return

    // 使用有序队列处理，避免 async 回调乱序导致 seq 回退
    await orderedQueue.process(sessionId, event, async (ev) => {
      await applySessionEvent(ev)
    })
  }

  /**
   * 从事件中解析 sessionId
   *
   * session_id 为 schema 必填字段，直接从 payload.session_id 获取。
   * @param {RealtimeEvent} event
   * @returns {string | null}
   */
  const _getSessionId = (event) => {
    return event.payload?.session_id
      || event.session_id
      || null
  }

  /**
   * 获取事件目标消息的 streamState。
   * message_updated / tool_call_* / approval_* 等事件可能携带 message_id；
   * 若未携带，则兜底使用最后一条 assistant 消息的状态。
   * @param {string} sessionId
   * @param {RealtimeEvent} event
   * @returns {string | null}
   */
  const _getTargetMessageStreamState = (sessionId, event) => {
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session?.messages) return null

    let messageId = null
    if (event.type === 'message_updated') {
      messageId = event.payload?.message_id || event.payload?.id
    } else {
      // tool_call_* / approval_* 等事件统一从 payload.message_id 或 payload.extra.message_id 提取
      messageId = event.payload?.message_id || event.payload?.extra?.message_id
    }

    if (messageId) {
      const msg = session.messages.find(m =>
        m.backendId?.toString() === messageId?.toString() ||
        m.id?.toString() === messageId?.toString()
      )
      if (msg) return msg.streamState || null
    }

    const lastAssistant = [...session.messages].reverse().find(m => m.role === 'assistant')
    return lastAssistant?.streamState || null
  }

  /**
   * 应用 session 通道事件
   * @param {RealtimeEvent} event
   */
  const applySessionEvent = async (event) => {
    const sessionId = _getSessionId(event)
    if (!sessionId) {
      logger.warn('[Sync] session 事件缺少 session_id:', event.type)
      return
    }

    // 事件去重：跳过已处理过的 seq（回放 + 实时推送可能重复送达同一事件）
    const dedupResult = seqDedup.shouldSkip(event, sessionId)
    if (dedupResult.skip) return
    if (dedupResult.resetToZero) seqDedup.setSeenSeq(sessionId, 0)

    // 目标消息处于 streaming / interrupted / finalizing / syncing 状态时，跳过与 SSE 重叠的
    // WebSocket message_updated 事件，避免滞后快照覆盖本地正在流式追加的最新内容。
    // 工具调用事件（7 个 tool_call_*）不再跳过，因为 SSE 已不推送工具事件（Task 3+4），
    // 所有浏览器均通过 WebSocket 接收工具调用状态。
    const targetStreamState = _getTargetMessageStreamState(sessionId, event)
    const earlyPayload = event.payload || event
    // message_updated 中的内容增长事件：非请求浏览器在 INTERRUPTED 状态下放行
    // 确保审批中断时保存的 AI 文字内容能同步到浏览器 B
    // 支持两种 payload 结构：嵌套（earlyPayload.message.content）和扁平（earlyPayload.content）
    // writeback_to_chat_message 发布的是扁平结构，chat 流式发布的是嵌套结构
    const isContentGrowingUpdate = event.type === 'message_updated'
      && targetStreamState === StreamState.INTERRUPTED
      && !streamingSessions.has(sessionId)
      && (earlyPayload?.message?.content || earlyPayload?.content)
      && (() => {
        // 获取本地消息内容长度进行比较，后端内容更长时放行
        const session = sessionStore.sessions.find(s => s.id === sessionId)
        const targetMsg = session?.messages?.find(m =>
          m.backendId?.toString() === (earlyPayload.message_id || earlyPayload.id)?.toString()
        )
        const localLen = (targetMsg?.content || '').length
        const backendContent = earlyPayload?.message?.content || earlyPayload?.content
        const backendLen = (backendContent || '').length
        return backendLen > localLen
      })()
    // 请求浏览器：不再整体跳过 message_updated，由 handleMessageUpdated 选择性处理
    // tool_calls 字段（SSE 不推送 tool 事件，触发浏览器需通过 WebSocket message_updated
    // 获取 tool_calls），仅跳过 content/reasoning 等 SSE 负责的字段。
    // 非请求浏览器：INTERRUPTED 状态下跳过 message_updated（本地审批状态是最新的），
    // 但内容增长的 message_updated 必须放行
    if (event.type === 'message_updated'
        && targetStreamState
        && PROTECTED_STREAM_STATES.has(targetStreamState)
        && !streamingSessions.has(sessionId)
        && targetStreamState === StreamState.INTERRUPTED
        && !isContentGrowingUpdate) {
      logger.debug(`[Sync] 非请求浏览器 INTERRUPTED 态，跳过 WS ${event.type}: session=${sessionId}`)
      // 仍需更新 seq，避免跳号检测误触发全量同步
      if (typeof event.seq === 'number') {
        seqDedup.setSeenSeq(sessionId, event.seq)
      }
      return
    }

    // 事件序列跳号检测
    // Task 2 修复后（_processSessionEventOrdered 间隙等待逻辑），事件跳号会大幅减少。
    // 此处保留 requestFullSync 作为兜底，应对真正的序列号丢失（重连/严重故障等场景）。
    const prevSeq = seqDedup.getPrevSeq(sessionId)
    const eventSeq = typeof event.seq === 'number' ? event.seq : prevSeq + 1
    if (eventSeq > prevSeq + 1 && !fullSyncPending.has(sessionId)) {
      const isFirstEvent = prevSeq === 0
      if (isFirstEvent) {
        // 首次事件跳号是正常行为（页面刚加载，lastSeenSeq 未初始化）
        // 不触发全量同步，避免在 SSE 流式期间 loadSessionDetail 全量替换 session 对象，
        // 破坏前端占位消息引用，导致 SSE 回调（appendToLastMessage 等）失效产生两个 AI 气泡
        // 会话详情已通过 loadSessionDetail/switchSession 加载，历史事件无需同步
        logger.info(`[Sync] 首次事件跳号(正常，跳过全量同步): session=${sessionId}, got=${eventSeq}`)
      } else {
        // 跳号原因：_processSessionEventOrdered 已等待 2 秒仍未补齐 expectedSeq，
        // 视为真正的事件丢失（重连后 lastSeenSeq 与后端 seq 不连续、或后端事件被去重跳过）
        logger.warn(
          `[Sync] 事件跳号触发全量同步: session=${sessionId}, ` +
          `expected=${prevSeq + 1}, got=${eventSeq}, ` +
          `reason=间隙等待后仍未补齐或后端序列号不连续`
        )
        fullSyncPending.add(sessionId)
        requestFullSync(sessionId).finally(() => fullSyncPending.delete(sessionId))
      }
    }

    // 如果本地没有该会话，先兜底拉取详情
    const existingSession = sessionStore.sessions.find(s => s.id === sessionId)
    if (!existingSession) {
      logger.info(`[Sync] 会话不存在，兜底拉取详情: ${sessionId}`)
      const detail = await sessionStore.loadSessionDetail(sessionId, { forceRefresh: true })
      if (!detail) {
        logger.warn(`[Sync] 无法加载会话详情，丢弃事件: ${sessionId}, type=${event.type}`)
        return
      }
    }

    const payload = event.payload || event

    switch (event.type) {
      case 'message_added':
        handleMessageAdded(sessionId, payload.message || payload)
        break
      case 'message_updated':
        handleMessageUpdated(sessionId, payload.message_id || payload.id, payload.fields || payload)
        break
      case 'stream_event':
        handleStreamEvent(sessionId, payload)
        break
      case 'messages_deleted':
        handleMessagesDeleted(sessionId, payload.deleted_message_ids || payload.ids || [])
        break
      // 10 个工具调用事件类型（每个 EventType 独立 ws_event_name）
      case 'tool_call_pending':
      case 'tool_call_input_ready':
      case 'tool_call_waiting':
      case 'tool_call_pending_approval':
      case 'tool_call_approved':
      case 'tool_call_rejected':
      case 'tool_call_running':
      case 'tool_call_completed':
      case 'tool_call_failed':
      case 'tool_call_timeout':
        await handleToolCallEvent(sessionId, null, payload, event.type, payload.source)
        break
      // 6 个审批事件类型（每个 EventType 独立 ws_event_name）
      case 'approval_pending':
      case 'approval_processing':
      case 'approval_waiting':
      case 'approval_approved':
      case 'approval_rejected':
      case 'approval_timeout':
        await handleApprovalEvent(sessionId, payload, event.type, { source: payload.source })
        break
      case 'stream_started':
        handleStreamStarted(sessionId, payload)
        break
      case 'stream_interrupted':
        handleStreamInterrupted(sessionId, payload)
        break
      case 'stream_completed':
        handleStreamCompleted(sessionId, payload)
        break
      case 'stream_finalized':
        await handleStreamFinalized(sessionId, payload)
        break
      case 'message_regenerated':
        handleMessageRegenerated(sessionId, payload)
        break
      case 'message_regenerate_reverted':
        handleMessageRegenerateReverted(sessionId, payload)
        break
      default:
        logger.debug(`[Sync] 未处理的 session 事件: ${event.type}`)
    }

    // 更新最后处理 seq
    if (typeof event.seq === 'number') {
      seqDedup.setSeenSeq(sessionId, event.seq)
    }
  }

  /**
   * 新增消息（幂等：已存在则合并）
   * @param {string} sessionId
   * @param {Object} messageData
   */
  const handleMessageAdded = (sessionId, messageData) => {
    if (!messageData || typeof messageData !== 'object') {
      logger.warn('[Sync] message_added 事件缺少消息数据')
      return
    }
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session) {
      logger.warn(`[Sync] message_added 会话不存在: ${sessionId}`)
      return
    }
    const message = transformBackendMessageToFrontend(messageData)
    if (!message) {
      logger.warn('[Sync] message_added 消息转换失败')
      return
    }

    // 去重策略：
    // 1. 精确匹配：backendId 或 id 一致
    // 2. 占位消息合并：前端 sendMessage 创建的本地占位消息（backendId 尚未设置），
    //    从后向前查找最近一条无 backendId 的同角色消息作为占位进行合并。
    //    - assistant：content 为空即可（流式占位初始为空，SSE chunk 会追加到已合并的消息）
    //    - user：content 与后端消息一致
    let existing = session.messages?.find(m =>
      (m.backendId && m.backendId?.toString() === message.backendId?.toString()) ||
      (m.id && m.id?.toString() === message.id?.toString())
    )

    // 诊断日志：占位合并前的状态
    const placeholderCount = session.messages?.filter(m => !m.backendId).length || 0
    logger.info(
      `[Sync] handleMessageAdded 诊断: session=${sessionId}, ` +
      `incoming.role=${message.role}, incoming.backendId=${message.backendId}, ` +
      `messagesCount=${session.messages?.length || 0}, ` +
      `placeholderCount=${placeholderCount}, ` +
      `exactMatch=${!!existing}`
    )

    if (!existing && message.backendId) {
      for (let i = session.messages.length - 1; i >= 0; i--) {
        const m = session.messages[i]
        if (m.backendId) break
        if (m.role !== message.role) continue
        if (m.role === 'assistant') {
          // assistant 占位合并条件：content 为空 OR isStreaming===true（当前流式占位）
          // 场景：SSE chunk 可能比 WebSocket message_added 先到达，占位已有 content，
          //       若仅 content 为空时合并，会走"消息新增"分支，产生两个 AI 气泡
          // 安全性：一个 session 同时只有一个 streaming 占位；
          //         mergeMessageFromBackend 会保护流式期间 content 不被后端覆盖
          if (!m.content || m.content === '' || m.isStreaming === true) {
            existing = m
            break
          }
        } else if (m.role === 'user') {
          if (m.content === message.content) {
            existing = m
            break
          }
        }
      }
      if (existing) {
        logger.info(`[Sync] 匹配到前端占位消息: session=${sessionId}, frontendId=${existing.id}, backendId=${message.backendId}, role=${message.role}, isStreaming=${existing.isStreaming}, contentLen=${(existing.content || '').length}`)
      } else {
        // 非触发浏览器收到 message_added 时，本地无占位消息是正常行为
        // （非触发浏览器不创建占位消息，直接走"消息新增"分支）
        logger.debug(
          `[Sync] 占位合并失败(非触发浏览器正常): session=${sessionId}, role=${message.role}, ` +
          `incomingContent=${message.content?.substring(0, 50)}, ` +
          `placeholderCount=${placeholderCount}`
        )
      }
    }

    if (existing) {
      if (message.backendId && !existing.backendId) {
        existing.backendId = message.backendId
      }
      mergeMessageFromBackend(existing, message)
      logger.info(`[Sync] 消息已存在，合并更新: session=${sessionId}, message=${message.backendId || message.id}`)
    } else {
      session.messages.push(message)
      session.messageCount = session.messages.length
      logger.info(`[Sync] 消息新增: session=${sessionId}, message=${message.backendId || message.id}`)
    }
    session.updatedAt = Date.now()

    // 消息新增/合并后，同步 toolCallsMap 到该消息的 toolCalls 数组
    // 场景：非触发浏览器 tool_call_* 事件可能先于 message_added 到达，Map 中已有数据
    // 此时新合并的 assistant 消息尚未派生 toolCalls，导致审批组件以降级方式渲染在错误位置
    if (message.role === 'assistant') {
      sessionStore.syncMessageToolCalls(sessionId, message)
    }
  }

  /**
   * 更新消息（携带完整消息数据时直接合并）
   * @param {string} sessionId
   * @param {string} messageId
   * @param {Object} fields
   */
  const handleMessageUpdated = (sessionId, messageId, fields) => {
    if (!messageId) {
      logger.warn('[Sync] message_updated 事件缺少 message_id')
      return
    }
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session?.messages) {
      logger.warn(`[Sync] message_updated 会话不存在或无消息: ${sessionId}`)
      return
    }
    const message = session.messages.find(m =>
      (m.backendId?.toString() === messageId?.toString()) ||
      (m.id?.toString() === messageId?.toString())
    )
    if (!message) {
      // 非触发浏览器可能因时序竞态（message_updated 先于 message_added 处理完）暂时找不到消息
      // 后续的 message_added 或全量同步会补偿，降级为 debug 避免干扰
      logger.debug(`[Sync] message_updated 未找到消息(时序竞态，后续补偿): session=${sessionId}, message=${messageId}`)
      return
    }

    // 幂等保护：跳过已处理过的旧事件（WebSocket 重连回放场景）
    const updateSeq = fields?._seq || fields?.seq
    if (updateSeq && message._lastUpdateSeq && updateSeq <= message._lastUpdateSeq) {
      logger.debug(`[Sync] message_updated 重复事件跳过: seq=${updateSeq}, lastSeq=${message._lastUpdateSeq}`)
      return
    }

    // Task 4: SSE 流式期间（触发浏览器），仅处理 tool_calls 字段更新，
    // 跳过 content/reasoning/sources/suggestions/context（由 SSE 负责）。
    // 原先在 applySessionEvent 中整体跳过 message_updated，导致 tool_calls 也被跳过，
    // 触发浏览器工具卡片缺失。现在改为仅跳过 SSE 负责的字段。
    if (streamingSessions.has(sessionId)) {
      if (fields?.message) {
        // 嵌套结构：仅保留 tool_calls，剔除 SSE 负责的字段后走 mergeMessageFromBackend
        const filteredMessage = { ...fields.message }
        delete filteredMessage.content
        delete filteredMessage.reasoning
        delete filteredMessage.sources
        delete filteredMessage.suggestions
        delete filteredMessage.context
        // 仅当存在 tool_calls 或用于匹配的 id 时才合并
        if (filteredMessage.tool_calls !== undefined || filteredMessage.id !== undefined) {
          const backendMessage = transformBackendMessageToFrontend(filteredMessage)
          if (backendMessage) {
            mergeMessageFromBackend(message, backendMessage)
          }
        }
      } else {
        // 扁平结构：仅提取 toolCalls / tool_calls 字段
        const toolCallFields = {}
        if (fields?.toolCalls !== undefined) toolCallFields.toolCalls = fields.toolCalls
        if (fields?.tool_calls !== undefined) toolCallFields.tool_calls = fields.tool_calls
        if (Object.keys(toolCallFields).length > 0) {
          const mapped = sessionStore._mapBackendMessageFields(toolCallFields)
          // 空值保护：后端 tool_calls 为空数组时不覆盖本地审批记录
          if (Array.isArray(mapped.toolCalls) && mapped.toolCalls.length === 0
              && Array.isArray(message.toolCalls) && message.toolCalls.length > 0) {
            logger.debug(`[Sync] message_updated 流式期间保留本地 toolCalls（后端为空）: message=${messageId}`)
          } else {
            // 走 mergeMessageFromBackend 而非 Object.assign，
            // 确保 _mergeToolCalls 的状态优先级保护生效，防止后端滞后快照（status=running）
            // 覆盖本地高优先级状态（status=completed）
            mergeMessageFromBackend(message, mapped)
          }
        }
      }

      // 记录已处理的 seq，用于后续去重
      if (updateSeq) {
        message._lastUpdateSeq = updateSeq
      }
      session.updatedAt = Date.now()
      logger.info(`[Sync] 消息更新(流式期间仅 toolCalls): session=${sessionId}, message=${messageId}`)
      return
    }

    // 非流式期间：保持原有完整合并逻辑
    if (fields?.message) {
      const backendMessage = transformBackendMessageToFrontend(fields.message)
      if (backendMessage) {
        mergeMessageFromBackend(message, backendMessage)
      }
    } else {
      const mapped = sessionStore._mapBackendMessageFields(fields)
      // tool_calls 空值保护：后端 tool_calls 为空数组时不覆盖本地审批记录
      // 场景：深度研究 writeback 时后端 ChatMessage.tool_calls 可能为空，
      // 但本地已有审批记录（通过 approval_* 系列事件同步），直接覆盖会导致审批卡片消失
      if (Array.isArray(mapped.toolCalls) && mapped.toolCalls.length === 0
          && Array.isArray(message.toolCalls) && message.toolCalls.length > 0) {
        delete mapped.toolCalls
        logger.debug(`[Sync] message_updated 保留本地 toolCalls（后端为空）: message=${messageId}`)
      }
      // 走 mergeMessageFromBackend 而非 Object.assign，
      // 确保 _mergeToolCalls 的状态优先级保护生效，防止后端滞后快照覆盖本地高优先级状态。
      // mapped 可能包含 toolCalls 之外的字段（如 reasoning/sources 等），
      // mergeMessageFromBackend 会按字段类型分别处理
      mergeMessageFromBackend(message, mapped)
    }

    // 记录已处理的 seq，用于后续去重
    if (updateSeq) {
      message._lastUpdateSeq = updateSeq
    }

    session.updatedAt = Date.now()
    logger.info(`[Sync] 消息更新: session=${sessionId}, message=${messageId}`)
  }

  /**
   * 处理流式事件（非触发浏览器通过 WebSocket 接收）
   * 触发浏览器跳过此事件（已通过 SSE 实时处理）。
   * @param {string} sessionId
   * @param {Object} payload - stream_event 事件载荷
   * @param {string} payload.message_id - 消息 ID
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
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session?.messages) return
    const message = session.messages.find(m =>
      m.backendId?.toString() === message_id?.toString() ||
      m.id?.toString() === message_id?.toString()
    )
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
   * 批量删除消息
   * @param {string} sessionId
   * @param {string[]} ids
   */
  const handleMessagesDeleted = (sessionId, ids) => {
    if (!ids?.length) {
      logger.warn('[Sync] messages_deleted 事件缺少删除 ID 列表')
      return
    }
    sessionStore.removeMessagesByIds(sessionId, ids)
    logger.info(`[Sync] 消息删除: session=${sessionId}, count=${ids.length}`)
  }

  /**
   * 处理工具调用事件（三模块共享：chat / deep_research / learning）
   *
   * 后端 Task 16+17 已改为每个 EventType 独立 ws_event_name，前端通过 event.type 直接区分
   * 7 个工具调用事件类型，映射到对应的 ToolCallStatus。
   * SSE 已移除 tool 事件（Task 3+4），工具事件统一通过 WebSocket 发布。
   *
   * 通过 sessionId / taskId 自动路由到 sessionStore 或 researchStore：
   * - sessionId 存在（chat / learning / 关联 deep_research）→ sessionStore
   * - 仅 taskId 存在（独立 deep_research）→ researchStore
   *
   * @param {string|null} sessionId - 会话 ID（chat/learning/关联 deep_research）
   * @param {string|null} taskId - 深度研究任务 ID（独立 deep_research）
   * @param {Object} payload - 事件载荷
   * @param {string} eventType - 事件类型（tool_call_pending / tool_call_completed 等）
   * @param {string} source - 事件来源模块（'chat' / 'deep_research' / 'learning'）
   */
  const handleToolCallEvent = async (sessionId, taskId, payload, eventType, source) => {
    // tool_call_id 为唯一主键（= LLM tool_call.id）
    const toolCallId = payload.tool_call_id

    if (!toolCallId) {
      logger.warn(`[Sync] handleToolCallEvent 缺少 tool_call_id: ${eventType}`, payload)
      return
    }

    // 通过 event.type 映射到 ToolCallStatus
    const mappedStatus = TOOL_CALL_STATUS_MAP[eventType]
    if (!mappedStatus) {
      logger.warn(`[Sync] handleToolCallEvent 未知工具事件类型: ${eventType}`)
      return
    }

    // 参数非空判断：{} 是 truthy 但空对象，需显式判断非空。
    // 仅在参数非空时传递，让 addOrUpdateToolCallInMap/updateOrAddToolResultInMap
    // 的 isNonEmptyParams 保护逻辑正确工作（undefined → 保留已有参数）
    const hasNonEmptyParams = !!payload.parameters
      && typeof payload.parameters === 'object'
      && !Array.isArray(payload.parameters)
      && Object.keys(payload.parameters).length > 0

    const toolData = {
      id: toolCallId,
      tool_call_id: toolCallId,
      name: payload.tool_name,
      tool_name: payload.tool_name,
      parameters: hasNonEmptyParams ? payload.parameters : undefined,
      args: hasNonEmptyParams ? payload.parameters : undefined,
      state: payload.state,
      status: mappedStatus || payload.status,
      result: payload.result,
      error: payload.error,
      is_internal: payload.is_internal || false,
      // SAFE 级自动通过标记（Phase F1）：后端 publish_tool_call payload 携带，
      // ToolCallCard 读取 toolCall.auto_approved 显示"自动通过"徽章
      auto_approved: payload.auto_approved === true,
      // 子 agent 嵌套层级字段（Phase E3）：后端 publish_tool_call payload 携带，
      // ToolCallCard 读取 toolCall.* 展示完整调用链路（非审批路径也可见）
      parent_tool_call_id: payload.parent_tool_call_id || '',
      depth: typeof payload.depth === 'number' && payload.depth > 0 ? payload.depth : 0,
      agent_name: payload.agent_name || '',
      agent_path: Array.isArray(payload.agent_path) ? payload.agent_path : [],
      risk_ceiling: payload.risk_ceiling || '',
    }

    const hasMessageId = !!payload.message_id
    const isResultEvent = TOOL_CALL_RESULT_STATUSES.has(mappedStatus)
    const storeId = sessionId || taskId
    const storeName = sessionId ? 'sessionStore' : 'researchStore'

    logger.info(
      `[Sync] 收到 ${eventType} 事件: ${storeName}=${storeId}, ` +
      `tool=${payload.tool_name}, toolCallId=${toolCallId}, ` +
      `mappedStatus=${mappedStatus}, isResult=${isResultEvent}, ` +
      `message_id=${payload.message_id || '(none)'}, source=${source || '(none)'}`
    )

    if (sessionId) {
      // chat / learning / 关联 deep_research：路由到 sessionStore
      // 防御性加载：WebSocket 事件可能在 session 加载之前到达
      // 会话不存在或消息为空时必须 await loadSessionDetail 完成，
      // 否则后续 addOrUpdateToolCall / updateOrAddToolResult 内部的 _getLastAssistantMessage
      // 会返回 null 导致事件被静默丢弃（非触发浏览器典型场景：会话在列表中但消息未加载）。
      // 与 handleApprovalEvent 的空消息检查保持一致。
      const existingSession = sessionStore.sessions.find(s => s.id === sessionId)
      if (!existingSession || !existingSession.messages || existingSession.messages.length === 0) {
        logger.info(`[Sync] handleToolCallEvent 会话不存在或消息为空，兜底拉取详情: ${sessionId}`)
        try {
          await sessionStore.loadSessionDetail(sessionId, { forceRefresh: true })
        } catch (e) {
          logger.warn(`[Sync] handleToolCallEvent 兜底加载会话失败: ${sessionId}`, e)
        }
      }

      if (isResultEvent) {
        // 工具结果事件（completed / failed / timeout）：更新工具结果
        // updateOrAddToolResultInMap 已有参数保护：仅在非空时更新，空时保留已有参数
        if (hasMessageId) {
          sessionStore.updateOrAddToolResult(sessionId, { ...toolData, messageBackendId: payload.message_id?.toString() })
        } else {
          sessionStore.updateOrAddToolResult(sessionId, toolData)
        }
        logger.info(`[Sync] 工具调用结果: session=${sessionId}, message=${payload.message_id || '(兜底)'}, tool=${payload.tool_name}, id=${toolCallId}, eventType=${eventType}`)
      } else {
        // 工具开始/运行中事件（pending / input_ready / waiting / running）：新增或更新工具调用
        if (hasMessageId) {
          sessionStore.addOrUpdateToolCall(sessionId, { ...toolData, messageBackendId: payload.message_id?.toString() })
        } else {
          sessionStore.addOrUpdateToolCall(sessionId, toolData)
        }
        // 每次 toolCall 创建后，检查是否有待绑定的审批（时序保护）
        approvalStore.flushPendingBindQueue(sessionId)
        logger.info(`[Sync] 工具调用更新: session=${sessionId}, message=${payload.message_id || '(兜底)'}, tool=${payload.tool_name}, id=${toolCallId}, eventType=${eventType}`)
      }
    } else if (taskId) {
      // 独立 deep_research：路由到 researchStore
      // researchStore.addOrUpdateToolCall 内部已调用 flushPendingApprovals，
      // 不需要额外调用 approvalStore.flushPendingBindQueue
      if (isResultEvent) {
        researchStore.updateOrAddToolResult(taskId, toolData)
      } else {
        researchStore.addOrUpdateToolCall(taskId, toolData)
      }
      logger.info(`[Sync] task ${eventType}: taskId=${taskId}, tool=${payload.tool_name}, toolCallId=${toolCallId}, mappedStatus=${mappedStatus}`)
    }
  }

  /**
   * 获取会话最后一条 assistant 消息
   * @param {string} sessionId
   * @returns {Object|null}
   */
  const getLastAssistantMessage = (sessionId) => {
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session?.messages) return null
    return [...session.messages].reverse().find(m => m.role === 'assistant') || null
  }

  /**
   * 根据 payload.message_id 或 payload.extra.message_id 在指定会话中定位消息。
   * 用于 approval_* 等事件优先按 message_id 路由，避免 getLastAssistantMessage 兜底
   * 导致非末尾消息（如重新生成中途的旧消息）审批 UI 错位到最后一条。
   * @param {string} sessionId
   * @param {Object} payload
   * @returns {Object|null}
   */
  const _findMessageByIdOrExtra = (sessionId, payload) => {
    const messageId = payload?.message_id || payload?.extra?.message_id
    if (!messageId) return null
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session?.messages) return null
    return session.messages.find(m =>
      m.backendId?.toString() === messageId.toString() ||
      m.id?.toString() === messageId.toString()
    ) || null
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
    // session_id 为 schema 必填字段
    const chatSessionId = payload.session_id
      || payload.chatSessionId
      || sessionId

    if (!mappedState) {
      logger.warn(`[Sync] handleApprovalEvent 未知审批事件类型: ${eventType}`)
      return
    }

    // SubTask 11.1: 解析 payload 中的 graph_interrupt_id 字段（批量审批场景）
    const graphInterruptId = payload.graph_interrupt_id || payload.extra?.graph_interrupt_id

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
      const existingSession = sessionStore.sessions.find(s => s.id === chatSessionId)
      if (!existingSession || existingSession.messages.length === 0) {
        logger.info(`[Sync] handleApprovalEvent 会话不存在或消息为空，兜底拉取详情: session=${chatSessionId}`)
        try {
          await sessionStore.loadSessionDetail(chatSessionId, { forceRefresh: true })
        } catch (e) {
          logger.warn(`[Sync] handleApprovalEvent 兜底加载会话失败: ${chatSessionId}`, e)
        }
      }
      approvalStore.handleApprovalEvent(enrichedPayload, { source, sessionId: chatSessionId })
    } else if (taskId) {
      // 独立 deep_research：路由到 approvalStore.handleApprovalEvent（统一入口）
      // sessionId 传 undefined（独立模式），taskId 传实际值。
      // approvalStore.handleApprovalEvent 会根据 payload 中的 session_id 推导 sessionId，
      // 若仍无 sessionId 则识别为独立 deep_research 模式，仅更新 pendingApprovals。
      approvalStore.handleApprovalEvent(enrichedPayload, { source, taskId })
      logger.info(`[Sync] 审批变更(独立深度研究): taskId=${taskId}, source=${source}, tool=${payload.tool_name}, eventType=${eventType}, mappedState=${mappedState}, graphInterruptId=${graphInterruptId || '(none)'}`)
      return
    }

    // 2. 消息 streamState 转换（sync 的职责，仅 chat/learning/关联 deep_research 场景）
    // 审批恢复后有 token 级流式输出，需从 INTERRUPTED 转为 STREAMING
    // 优先按 payload.message_id / payload.extra.message_id 路由（重新生成非末尾消息场景），
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
      const targetMsg = _findMessageByIdOrExtra(sessionId, payload) || getLastAssistantMessage(sessionId)
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
      // SubTask 11.2-11.4: 批量审批场景，查询本地 store 中同一批次的 Approval 状态
      const targetMsg = _findMessageByIdOrExtra(sessionId, payload) || getLastAssistantMessage(sessionId)
      if (targetMsg?.streamState === StreamState.INTERRUPTED && !streamingSessions.has(sessionId)) {
        const siblingApprovals = _collectSiblingApprovals(targetMsg, graphInterruptId, payload.remaining_pending_count)

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
          // 回退：本地 toolCalls 遍历（低版本兼容）
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
    } else if ((mappedState === ApprovalState.APPROVED || mappedState === ApprovalState.PROCESSING) && !streamingSessions.has(sessionId)) {
      // 非批量 approval_approved / approval_processing：INTERRUPTED → STREAMING
      const targetMsg = _findMessageByIdOrExtra(sessionId, payload) || getLastAssistantMessage(sessionId)
      if (targetMsg?.streamState === StreamState.INTERRUPTED) {
        targetMsg.streamState = StreamState.STREAMING
        logger.info(`[Sync] 非请求浏览器审批 ${mappedState}，INTERRUPTED → STREAMING: session=${sessionId}, source=${source}, message=${targetMsg.backendId || targetMsg.id}`)
      }
    }

    logger.info(`[Sync] 审批变更: session=${sessionId}, chatSession=${chatSessionId}, source=${source}, tool=${payload.tool_name}, eventType=${eventType}, mappedState=${mappedState}, graphInterruptId=${graphInterruptId || '(none)'}`)
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
      const tcGraphId = approval.graph_interrupt_id || approval.extra?.graph_interrupt_id
      if (tcGraphId === graphInterruptId) {
        siblings.push({
          toolCallId: tc.id || tc.tool_call_id || '',
          approvalState: approval.state,
        })
      }
    }
    return siblings
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
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session?.messages) {
      // 会话未加载，仅标记 thinkingSessions，等会话加载后由后续事件补偿
      thinkingSessions.add(sessionId)
      logger.info(`[Sync] stream_interrupted 会话不存在，标记正在思考: session=${sessionId}`)
      return
    }

    // payload 可能有两种结构：
    // 1. 嵌套：{ source, source_id, session_id, message_id, data: { task_id, ... } }
    // 2. 扁平：{ source, source_id, session_id, message_id, task_id, ... }
    const data = payload.data || {}
    const taskId = data.task_id || payload.task_id
    const messageId = data.message_id || payload.message_id

    // 定位目标消息（优先按 message_id，兜底最后一条 assistant 消息）
    let targetMsg = null
    if (messageId) {
      targetMsg = session.messages.find(m =>
        m.backendId?.toString() === messageId?.toString() ||
        m.id?.toString() === messageId?.toString()
      )
    }
    if (!targetMsg) {
      targetMsg = [...session.messages].reverse().find(m => m.role === 'assistant')
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
      const ver = targetMsg.versions?.[targetMsg.currentVersion]
      if (ver) ver.researchTaskId = taskId
    }

    // 设置 streamState=INTERRUPTED（避免 showContinueResearch 提前显示"继续研究"按钮）
    // 仅在非终态时设置，避免覆盖已完成消息的状态
    if (targetMsg.streamState !== StreamState.COMPLETED
        && targetMsg.streamState !== StreamState.ERROR) {
      targetMsg.streamState = StreamState.INTERRUPTED
      targetMsg.isStreaming = true
      // 同步到 versions[currentVersion]
      const ver = targetMsg.versions?.[targetMsg.currentVersion]
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

    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session?.messages) {
      // 会话不存在时仍标记 thinkingSessions，等会话加载后由 stream_completed 清理
      thinkingSessions.add(sessionId)
      logger.info(`[Sync] stream_started 会话不存在，标记正在思考: session=${sessionId}`)
      return
    }

    // 定位目标消息（优先按 message_id，兜底最后一条 assistant 消息）
    const messageId = payload.message_id
    let targetMsg = null
    if (messageId) {
      targetMsg = session.messages.find(m =>
        m.backendId?.toString() === messageId?.toString() ||
        m.id?.toString() === messageId?.toString()
      )
    }
    if (!targetMsg) {
      targetMsg = [...session.messages].reverse().find(m => m.role === 'assistant')
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
   * 最终化消息中所有非终态 toolCalls（流式完成后兜底）
   *
   * 场景：WebSocket tool_call_completed 事件丢失或乱序，导致 toolCall.status
   * 卡在 pending/running/waiting，UI 永久显示"执行中"。
   *
   * 处理规则：
   * - 有 result/output 的 toolCall → status = COMPLETED
   * - 无 result/output 的 toolCall → status = COMPLETED（流式已结束，工具必然已完成）
   * - approval.state 为 pending/processing/waiting → 不强制修改（审批流程独立于工具执行）
   *   但若流式已结束且审批仍在等待中，说明审批可能已超时，标记为 timeout
   * - 同步更新 versions[currentVersion].toolCalls
   * - 同步更新 toolCallsMap（单一真相源），确保 UI 读取的状态一致
   *
   * @param {Object} message - 目标消息对象
   * @param {string} [sessionId] - 会话 ID（用于同步 toolCallsMap）
   */
  const _finalizeToolCallsForCompletedMessage = (message, sessionId) => {
    if (!message?.toolCalls || !Array.isArray(message.toolCalls)) return

    let finalizedCount = 0
    const nonTerminalStatuses = [
      ToolCallStatus.PENDING,
      ToolCallStatus.RUNNING,
      ToolCallStatus.WAITING,
      'pending_approval',
    ]

    for (const tc of message.toolCalls) {
      if (!tc) continue
      if (nonTerminalStatuses.includes(tc.status)) {
        // 有结果 → COMPLETED，无结果也 → COMPLETED（流式已结束）
        tc.status = ToolCallStatus.COMPLETED
        if (!tc.state) tc.state = 'output-available'
        finalizedCount++
      }
      // 审批仍在 pending/processing/waiting：流式已结束说明审批已超时
      if (tc.approval
          && ['pending', 'processing', 'waiting'].includes(tc.approval.state)) {
        tc.approval.state = 'timeout'
        finalizedCount++
      }
    }

    // 同步到 versions[currentVersion].toolCalls
    const ver = message.versions?.[message.currentVersion]
    if (ver?.toolCalls && Array.isArray(ver.toolCalls)) {
      for (const verTc of ver.toolCalls) {
        if (!verTc) continue
        // 按 id/tool_call_id 匹配并同步状态
        const matched = message.toolCalls.find(tc =>
          tc && (tc.id === verTc.id || tc.tool_call_id === verTc.tool_call_id)
        )
        if (matched) {
          verTc.status = matched.status
          if (matched.state) verTc.state = matched.state
          if (matched.approval) verTc.approval = { ...matched.approval }
        }
      }
    }

    if (finalizedCount > 0) {
      logger.info(
        `[Sync] _finalizeToolCallsForCompletedMessage: 最终化 ${finalizedCount} 个 toolCall: ` +
        `message=${message.backendId || message.id}`
      )
      // 同步更新 toolCallsMap（单一真相源），确保 UI 读取的状态与 message.toolCalls 一致
      if (sessionId) {
        const msgBackendId = message.backendId?.toString() || message.id?.toString()
        sessionStore.finalizeToolCallsInMap(sessionId, msgBackendId)
      }
    }
  }

  /**
   * 全量同步完成后校验消息完整性（tool_calls + content）
   *
   * requestFullSync 内部已通过 mergeMessageFromBackend 做字段级合并，
   * 此函数作为安全网，确保触发浏览器的 tool_calls 数量和 content 长度
   * 不低于后端权威快照（工具卡片完整性 + AI 内容完整性）。
   *
   * 触发场景：
   * - 流式期间 message_updated 选择性跳过 SSE 字段，可能导致 tool_calls 缺失
   * - 时序竞态导致 ChatMessage.tool_calls 不完整
   * - AI 文本内容在工具调用开始时被部分覆盖（SSE 流式期间 content 被截断）
   *
   * 注意：backendMessages 是 requestFullSync 在合并前保存的后端原始快照，
   * 不受本地保护态合并逻辑影响，可作为完整性校验的权威基准。
   *
   * @param {string} sessionId
   * @param {string} messageId - 目标消息 backendId 或 id
   * @param {Array} backendMessages - requestFullSync 返回的后端消息快照（合并前）
   */
  const _verifyMessageIntegrityAfterSync = (sessionId, messageId, backendMessages) => {
    if (!sessionId || !messageId || !Array.isArray(backendMessages) || backendMessages.length === 0) return

    // 在 backendMessages 快照中查找对应消息（合并前的后端原始数据）
    const backendMsg = backendMessages.find(m =>
      m && (m.backendId?.toString() === messageId?.toString()
            || m.id?.toString() === messageId?.toString())
    )
    if (!backendMsg) {
      logger.debug(`[Sync] 完整性校验: 未在后端快照中找到消息: session=${sessionId}, message=${messageId}`)
      return
    }

    // 在当前 session 中重新查找本地消息（requestFullSync 可能已替换消息对象引用）
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session?.messages) return
    const localMsg = session.messages.find(m =>
      m && (m.backendId?.toString() === messageId?.toString()
            || m.id?.toString() === messageId?.toString())
    )
    if (!localMsg) {
      logger.warn(`[Sync] 完整性校验: 本地未找到消息: session=${sessionId}, message=${messageId}`)
      return
    }

    // === tool_calls 完整性校验 ===
    // 后端 toolCalls 数量应等于或大于本地（后端是权威源）
    // 若本地缺失，强制调用 mergeMessageFromBackend 触发 _mergeToolCalls 合并
    const backendToolCalls = Array.isArray(backendMsg.toolCalls) ? backendMsg.toolCalls : []
    const localToolCalls = Array.isArray(localMsg.toolCalls) ? localMsg.toolCalls : []
    if (backendToolCalls.length > localToolCalls.length) {
      logger.warn(
        `[Sync] 完整性校验: tool_calls 缺失，强制合并: session=${sessionId}, ` +
        `message=${messageId}, local=${localToolCalls.length}, backend=${backendToolCalls.length}`
      )
      // 强制合并：mergeMessageFromBackend 内部 _mergeToolCalls 会保留本地已有结果与终态
      mergeMessageFromBackend(localMsg, { toolCalls: backendToolCalls })
      // 同步到 versions[currentVersion]
      const ver = localMsg.versions?.[localMsg.currentVersion]
      if (ver) ver.toolCalls = localMsg.toolCalls
    }

    // === content 完整性校验 ===
    // 触发浏览器 SSE 流式期间可能因工具调用开始时事件覆盖导致 content 截断
    // 若本地 content 长度 < 后端 content 长度，以后端为准直接覆盖
    const backendContent = typeof backendMsg.content === 'string' ? backendMsg.content : ''
    const localContent = typeof localMsg.content === 'string' ? localMsg.content : ''
    if (backendContent.length > localContent.length) {
      logger.warn(
        `[Sync] 完整性校验: content 截断，以后端为准: session=${sessionId}, ` +
        `message=${messageId}, local=${localContent.length}, backend=${backendContent.length}`
      )
      localMsg.content = backendContent
      // 同步到 versions[currentVersion]，避免版本切换后回退到截断内容
      const ver = localMsg.versions?.[localMsg.currentVersion]
      if (ver) ver.content = backendContent
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
   * 2. 关联场景下委托更新 researchStore.taskInfo（payload.task_id 存在且 hasResearchResult）
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
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session?.messages) return

    // Task 5：深度研究模式下，chat SSE 结束时发布的 stream_completed（finalized !== true）
    // 不应让前端误判研究完成。当 finalized !== true 且消息处于 INTERRUPTED 状态时，
    // 忽略该事件（不更新消息状态，不显示"研究已完成"/"继续研究"按钮），仅由事件队列推进 seq。
    // 真正的研究完成事件由 Celery worker 通过 broadcast_stream_completed 发布
    //（finalized=true，携带 task_id/final_report），届时正常进入下方研究结果回写逻辑。
    // 普通聊天模式（无 finalized 字段或 finalized=false）消息不会处于 INTERRUPTED 状态，
    // 不受此判断影响，行为与原有逻辑一致。
    if (payload.finalized !== true) {
      const earlyMessageId = payload.message_id
      let earlyTargetMsg = null
      if (earlyMessageId) {
        earlyTargetMsg = session.messages.find(m =>
          m.backendId?.toString() === earlyMessageId?.toString() ||
          m.id?.toString() === earlyMessageId?.toString()
        )
      }
      if (!earlyTargetMsg) {
        earlyTargetMsg = [...session.messages].reverse().find(m => m.role === 'assistant')
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
    const hasResearchResult = payload.task_id && (payload.final_report !== undefined || payload.error)
    if (hasResearchResult) {
      const messageId = payload.message_id
      let targetMsg = null
      if (messageId) {
        targetMsg = session.messages.find(m =>
          m.backendId?.toString() === messageId?.toString() ||
          m.id?.toString() === messageId?.toString()
        )
      }
      if (!targetMsg) {
        // 通过 research_task_id 查找关联消息
        targetMsg = [...session.messages].reverse().find(m =>
          m.role === 'assistant' && m.researchTaskId === payload.task_id
        )
      }
      if (!targetMsg) {
        // 最后一个 assistant 消息
        targetMsg = [...session.messages].reverse().find(m => m.role === 'assistant')
      }

      if (!targetMsg) {
        // 极端情况：无 assistant 消息，仅触发全量同步
        logger.warn(`[Sync] 深度研究结果回写但未找到目标消息: session=${sessionId}, task=${payload.task_id}`)
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
        if (payload.success !== false && payload.final_report) {
          targetMsg.content = payload.final_report
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
        const nonTerminalApprovalStates = ['pending', 'processing', 'waiting']
        let pendingApprovalCount = 0
        let runningToolCount = 0
        if (targetMsg.toolCalls && Array.isArray(targetMsg.toolCalls)) {
          for (const tc of targetMsg.toolCalls) {
            if (nonTerminalApprovalStates.includes(tc.approval?.state)) pendingApprovalCount++
            if (tc.status === 'pending_approval' || tc.status === 'running') runningToolCount++
            // 对于仍在 pending/processing/waiting 状态的审批，研究完成/失败后强制清理为终态
            if (tc.approval && nonTerminalApprovalStates.includes(tc.approval.state)) {
              tc.approval.state = payload.success !== false ? 'approved' : 'rejected'
            }
            // 如果工具还是 pending_approval/running 状态，研究都结束了，根据实际情况设置
            if (tc.status === 'pending_approval' || tc.status === 'running') {
              if (tc.result || tc.output) {
                tc.status = ToolCallStatus.COMPLETED
              } else {
                tc.status = payload.success !== false ? ToolCallStatus.COMPLETED : ToolCallStatus.FAILED
              }
            }
          }
        }
        // 同步清理 versions 中的审批状态
        const ver = targetMsg.versions?.[targetMsg.currentVersion]
        if (ver?.toolCalls) {
          for (const tc of ver.toolCalls) {
            if (tc.approval && nonTerminalApprovalStates.includes(tc.approval.state)) {
              tc.approval.state = payload.success !== false ? 'approved' : 'rejected'
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
        if (payload.task_id) {
          clearedApprovalCount = approvalStore.clearByTaskId(payload.task_id)
        }

        logger.info(
          `[Sync] 深度研究结果回写 - 审批状态清理: ` +
          `session=${sessionId}, task=${payload.task_id}, ` +
          `success=${payload.success !== false}, ` +
          `toolCalls总数=${targetMsg.toolCalls?.length || 0}, ` +
          `清理前pending审批数=${pendingApprovalCount}, ` +
          `清理前运行中工具数=${runningToolCount}, ` +
          `clearByTaskId清除数=${clearedApprovalCount}`
        )

        logger.info(`[Sync] 深度研究结果回写: session=${sessionId}, task=${payload.task_id}, success=${payload.success !== false}`)

        // 触发全量同步确保数据一致性
        requestFullSync(sessionId)

        // 委托更新 researchStore.taskInfo（关联 chat 场景主路径）
        // 解耦 taskInfo 更新与 task 频道订阅状态，确保 DeepResearchView 未打开时 taskInfo 也实时更新
        // 幂等性：与 handleTaskEvent 的 stream_completed 分支形成双路径，updateTaskFromEvent 使用 force:true 保证一致
        if (payload.task_id) {
          try {
            researchStore.updateTaskFromEvent(payload.task_id, payload)
            logger.info(
              `[Sync] handleStreamCompleted 委托更新 taskInfo: ` +
              `taskId=${payload.task_id}, success=${payload.success !== false}`
            )
          } catch (err) {
            logger.warn(
              `[Sync] handleStreamCompleted 委托更新 taskInfo 失败: ` +
              `taskId=${payload.task_id}, error=${err?.message || err}`
            )
          }
        }

        return
      }
    }
    // === 深度研究结果处理结束 ===

    const messageId = payload.message_id
    let targetMsg = null
    if (messageId) {
      targetMsg = session.messages.find(m =>
        m.backendId?.toString() === messageId?.toString() ||
        m.id?.toString() === messageId?.toString()
      )
    }
    if (!targetMsg) {
      targetMsg = [...session.messages].reverse().find(m => m.role === 'assistant')
    }

    if (!targetMsg) {
      logger.info(`[Sync] 流式完成但未找到目标消息: session=${sessionId}, message=${messageId || '(兜底)'}`)
      return
    }

    // V8 Task 5：记录后端 content_length 用于早期可观测性
    // 实际完整性校验由 _verifyMessageIntegrityAfterSync 通过后端快照对比完成
    if (typeof payload.content_length === 'number') {
      const localLen = (targetMsg.content || '').length
      if (payload.content_length > localLen) {
        logger.warn(
          `[Sync] stream_completed 后端 content_length 大于本地: ` +
          `session=${sessionId}, message=${messageId || '(兜底)'}, ` +
          `local=${localLen}, backend=${payload.content_length}`
        )
      } else {
        logger.debug(
          `[Sync] stream_completed content_length 校验通过: ` +
          `session=${sessionId}, local=${localLen}, backend=${payload.content_length}`
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

        // 关键修复：请求浏览器也需要最终化 toolCalls 状态
        // 场景：WebSocket tool_call_completed 事件丢失或未到达时，
        // toolCall.status 可能卡在 pending/running，导致 UI 永久显示"执行中"
        _finalizeToolCallsForCompletedMessage(targetMsg, sessionId)
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
        _finalizeToolCallsForCompletedMessage(targetMsg, sessionId)
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
          const s = sessionStore.sessions.find(s => s.id === sessionId)
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
      // 关键修复：非请求浏览器标记 COMPLETED 后，立即最终化所有非终态 toolCalls
      // 防止 WebSocket tool_call_completed 事件丢失或乱序导致 toolCall 卡在 pending/running/waiting
      // 与请求浏览器路径（L1374-1403）行为对齐，确保跨浏览器工具状态一致
      _finalizeToolCallsForCompletedMessage(targetMsg, sessionId)
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
        _verifyMessageIntegrityAfterSync(sessionId, targetMessageId, result.backendMessages)
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
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session?.messages) return

    const messageId = payload.message_id
    let targetMsg = null
    if (messageId) {
      targetMsg = session.messages.find(m =>
        m.backendId?.toString() === messageId?.toString() ||
        m.id?.toString() === messageId?.toString()
      )
    }
    if (!targetMsg) {
      targetMsg = [...session.messages].reverse().find(m => m.role === 'assistant')
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
    const refreshedSession = sessionStore.sessions.find(s => s.id === sessionId)
    const refreshedTarget = messageId
      ? refreshedSession?.messages?.find(m =>
          m.backendId?.toString() === messageId?.toString() ||
          m.id?.toString() === messageId?.toString()
        )
      : [...(refreshedSession?.messages || [])].reverse().find(m => m.role === 'assistant')

    if (refreshedTarget
        && refreshedTarget.streamState !== StreamState.INTERRUPTED
        && refreshedTarget.streamState !== StreamState.ERROR) {
      refreshedTarget.streamState = StreamState.COMPLETED
      refreshedTarget.isStreaming = false
      logger.info(`[Sync] stream_finalized 非请求浏览器同步后标记 completed: session=${sessionId}, message=${messageId || '(兜底)'}`)
    }
  }

  /**
   * 重新生成消息事件（message_regenerated）
   *
   * 后端在 archive_current_version 后广播此事件，前端镜像相同逻辑：
   * 1. 归档当前版本到 versions 数组（保留 content/toolCalls/sources/reasoning 等快照）
   * 2. 追加新空版本并设置 currentVersion 指向它
   * 3. 清空顶层 content/toolCalls/sources/reasoning/suggestions/context
   * 4. 标记 streamState=STREAMING，让后续 SSE chunks 追加到新空版本
   *
   * 幂等保护：
   * - 若 currentVersion 指向的版本已是空 STREAMING 版本（content 为空且无 toolCalls），
   *   说明事件已处理过，跳过避免重复归档（应对 WebSocket 重连回放场景）。
   *
   * 事件顺序保证：
   * - 后端先广播 message_regenerated（Redis pubsub），再返回 SSE 流式响应；
   * - Redis pubsub 快于 HTTP 流式首字节，确保事件先于 SSE 首 chunk 到达。
   *
   * @param {string} sessionId
   * @param {Object} payload - 至少包含 message_id
   */
  const handleMessageRegenerated = (sessionId, payload) => {
    const messageId = payload.message_id || payload.id
    if (!messageId) {
      logger.warn('[Sync] message_regenerated 事件缺少 message_id')
      return
    }
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session?.messages) {
      logger.warn(`[Sync] message_regenerated 会话不存在或无消息: ${sessionId}`)
      return
    }
    const message = session.messages.find(m =>
      m.backendId?.toString() === messageId?.toString() ||
      m.id?.toString() === messageId?.toString()
    )
    if (!message) {
      logger.warn(`[Sync] message_regenerated 未找到消息: session=${sessionId}, message=${messageId}`)
      return
    }

    // 幂等保护：currentVersion 指向的版本已是空 STREAMING 版本时跳过
    if (Array.isArray(message.versions) && message.versions.length > 0) {
      const currentVer = message.versions[message.currentVersion]
      if (currentVer
          && currentVer.streamState === StreamState.STREAMING
          && !(currentVer.content || '').trim()
          && (!currentVer.toolCalls || currentVer.toolCalls.length === 0)) {
        logger.info(`[Sync] message_regenerated 幂等跳过：当前版本已是空 STREAMING 版本: session=${sessionId}, message=${messageId}`)
        return
      }
    }

    // 1. 归档当前版本：versions 不存在或为空时用 createMessageVersion 初始化
    if (!Array.isArray(message.versions) || message.versions.length === 0) {
      message.versions = [createMessageVersion(message)]
      message.currentVersion = 0
    } else {
      // 将当前顶层字段同步到当前版本快照，确保归档前快照是最新的
      // （前端 versions[currentVersion] 通常已与顶层同步，此处显式同步兜底）
      const currentVer = message.versions[message.currentVersion]
      if (currentVer) {
        currentVer.content = message.content || ''
        currentVer.sources = message.sources || []
        currentVer.toolCalls = message.toolCalls || []
        currentVer.reasoning = message.reasoning || null
        currentVer.suggestions = message.suggestions || null
        currentVer.context = message.context || null
        currentVer.streamState = message.streamState || StreamState.COMPLETED
        currentVer.isStreaming = false
      }
    }

    // 2. 追加新空版本（与后端 archive_current_version 的空版本结构对齐）
    const newVersion = {
      id: message.id,
      content: '',
      sources: [],
      toolCalls: [],
      reasoning: null,
      suggestions: null,
      context: null,
      attachmentIds: message.attachmentIds || [],
      attachments: message.attachments || [],
      streamState: StreamState.STREAMING,
      isStreaming: true,
    }
    message.versions.push(newVersion)

    // 3. 设置 currentVersion 指向新空版本
    message.currentVersion = message.versions.length - 1

    // 4. 清空顶层字段（流式输出会重新填充）
    message.content = ''
    message.toolCalls = []
    message.sources = []
    message.reasoning = null
    message.suggestions = null
    message.context = null
    message.images = []

    // 5. 标记流式状态供 UI 显示
    message.streamState = StreamState.STREAMING
    message.isStreaming = true

    session.updatedAt = Date.now()
    logger.info(`[Sync] 消息重新生成版本归档: session=${sessionId}, message=${messageId}, versions=${message.versions.length}`)
  }

  /**
   * 重新生成失败回滚事件（message_regenerate_reverted）
   *
   * 后端在重新生成启动即失败（无任何输出内容）时，删除空版本并恢复上一个版本数据。
   * 前端镜像此操作：弹出最后一个空版本，恢复 currentVersion 和顶层字段。
   *
   * @param {string} sessionId
   * @param {Object} payload - 包含 message_id 和回滚后的完整 message 数据
   */
  const handleMessageRegenerateReverted = (sessionId, payload) => {
    const messageId = payload.message_id || payload.id
    const backendMessage = payload.message
    if (!messageId) {
      logger.warn('[Sync] message_regenerate_reverted 事件缺少 message_id')
      return
    }
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session?.messages) {
      logger.warn(`[Sync] message_regenerate_reverted 会话不存在: ${sessionId}`)
      return
    }
    const message = session.messages.find(m =>
      m.backendId?.toString() === messageId?.toString() ||
      m.id?.toString() === messageId?.toString()
    )
    if (!message) {
      logger.warn(`[Sync] message_regenerate_reverted 未找到消息: ${messageId}`)
      return
    }

    // 弹出最后一个（空的失败版本）
    if (Array.isArray(message.versions) && message.versions.length > 1) {
      message.versions.pop()
      message.currentVersion = message.versions.length - 1
    }

    // 用后端回滚后的完整数据恢复顶层字段
    if (backendMessage) {
      const transformed = transformBackendMessageToFrontend(backendMessage)
      if (transformed) {
        message.content = transformed.content
        message.toolCalls = transformed.toolCalls
        message.sources = transformed.sources
        message.reasoning = transformed.reasoning
        message.suggestions = transformed.suggestions
        message.context = transformed.context
        message.versions = transformed.versions
        message.currentVersion = transformed.currentVersion
      }
    } else {
      // 无后端数据时，用当前版本恢复
      const ver = message.versions?.[message.currentVersion]
      if (ver) {
        message.content = ver.content || ''
        message.toolCalls = ver.toolCalls || []
        message.sources = ver.sources || []
        message.reasoning = ver.reasoning || null
      }
    }

    message.streamState = StreamState.COMPLETED
    message.isStreaming = false

    session.updatedAt = Date.now()
    logger.info(`[Sync] 消息重新生成失败回滚: session=${sessionId}, message=${messageId}`)
  }

  return {
    handleSessionEvent,
    applySessionEvent,
    handleToolCallEvent,
    handleApprovalEvent,
    handleMessageUpdated,
    handleStreamCompleted,
  }
}
