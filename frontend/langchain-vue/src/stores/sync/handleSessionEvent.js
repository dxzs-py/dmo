import { logger } from '@/utils/logger'
import { toCamelCase } from '@/utils/sessionTransformers'
import { getEventSessionId } from '@/utils/eventRouting'
import { StreamState, ApprovalState, PROTECTED_STREAM_STATES } from '@/types'
import { scheduleSubagentsRefresh } from '@/composables/useSubagents'
import { APPROVAL_STATE_MAP } from './constants'
import { createHandleToolCallEvent } from './toolCallHandler'
import {
  findMessageById,
  getSession,
  getLastAssistantMessage,
} from './helpers'

/**
 * @typedef {import('@/composables/useRealtimeSync').RealtimeEvent} RealtimeEvent
 */

/**
 * 创建 session 通道事件处理器（最大模块）
 *
 * 模块关系：本模块通过 ctx 接受 sync.js 注入的独立模块函数（Task 16），
 * 仅使用注入版本（无内联实现）。注入的函数来自：
 *   - messageHandlers.js：handleMessageAdded / handleMessageUpdated / handleMessagesDeleted
 *     / handleMessageRegenerated / handleMessageRegenerateReverted
 *   - streamStateHandlers.js：handleStreamEvent / handleStreamInterrupted
 *     / handleStreamCompleted / handleStreamFinalized
 *
 * @param {Object} ctx - 依赖上下文（含注入的独立模块函数）
 * @param {Object} ctx.sessionStore - session store 实例
 * @param {Object} ctx.approvalStore - approval store 实例
 * @param {Object} ctx.researchStore - research store 实例
 * @param {{ getPrevSeq: Function, setSeenSeq: Function }} ctx.seqDedup
 *   - 来自 createSeqDedup，仅提供跳号检测基线（Task 4：事件已见去重由
 *     useRealtimeSync.lastSeq 与 orderedQueue.expectedSeq 负责，本模块不做）
 * @param {{ process: Function }} ctx.orderedQueue
 *   - 来自 createOrderedQueue，提供有序队列处理能力
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
    fullSyncPending,
    requestFullSync,
  } = ctx

  // 工具调用事件处理器（三模块共享，由 toolCallHandler.js 工厂函数创建）
  const { handleToolCallEvent } = createHandleToolCallEvent({ sessionStore, approvalStore, researchStore })

  /**
   * 处理 session 通道事件（由 ChatView 等订阅方回调）
   *
   * sessionId 为 schema 必填字段，入口 toCamelCase 转换后从 payload.sessionId 获取。
   * @param {RealtimeEvent} event
   */
  const handleSessionEvent = async (event) => {
    // 统一入站转换：WebSocket 事件 payload snake_case → camelCase
    // 此转换后所有下游 handler（messageHandlers/toolCallHandler/approvalHandler）
    // 收到的数据均为 camelCase，禁止再访问 snake_case 键名
    // 防御：转换异常时跳过该事件（事件丢弃由后续 replay/fullSync 补偿），
    // 保证函数不抛异常，避免事件不进 orderedQueue 直接丢失
    try {
      event.payload = toCamelCase(event.payload)
    } catch (err) {
      logger.error(
        `[Sync] session 事件 payload 转换失败，跳过事件: type=${event.type}, error=${err?.message || err}`
      )
      return
    }
    // 路由字段解析（P3-R1 根因修复，统一入口 getEventSessionId）：
    // 后端将 session_id 注入事件顶层（与 payload 平级，见 realtime_events.py
    // _publish_to_session_async），payload 内部不含 session_id。
    // 统一 helper 优先 payload，其次 event 顶层，否则所有 WebSocket 事件被静默丢弃。
    const sessionId = getEventSessionId(event)
    if (!sessionId) return

    // 使用有序队列处理，避免 async 回调乱序导致 seq 回退
    await orderedQueue.process(sessionId, event, async (ev) => {
      await applySessionEvent(ev)
    })
  }

  /**
   * 从事件中解析 sessionId
   *
   * sessionId 为 schema 必填字段（P3-R1 根因修复）：
   * 后端将 session_id 注入事件顶层（event.sessionId，与 payload 平级），
   * payload 内部不含 session_id。统一委托 getEventSessionId 解析
   * （优先 payload，其次 event 顶层，null 兜底）。
   * @param {RealtimeEvent} event
   * @returns {string | null}
   */
  const _getSessionId = (event) => {
    return getEventSessionId(event)
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
    const session = getSession(sessionStore, sessionId)
    if (!session?.messages) return null

    let messageId = event.type === 'message_updated'
      ? event.payload?.messageId
      : (event.payload?.messageId || event.payload?.extra?.messageId)

    if (messageId) {
      const msg = findMessageById(session, messageId)
      if (msg) return msg.streamState || null
    }

    const lastAssistant = getLastAssistantMessage(session)
    return lastAssistant?.streamState || null
  }

  /**
   * 应用子代理图层正文/中间思考事件（stream_subagent_content，session 频道）
   *
   * spec D10：
   * - 按 subagentThreadId 写入 message.subagentContents[threadId]（content/reasoningContent 追加累计）；
   * - 未知 threadId（chunk 先于 spawn 元数据到达）：动态初始化条目（时序竞争防御）；
   * - 子代理名称/深度从 payload.data 透传（用于摘要卡片展示）。
   *
   * @param {string} sessionId
   * @param {Object} payload - toCamelCase 后：{ source, sourceId, messageId, sessionId, subagentThreadId, data: { agentName, depth, content, reasoningContent } }
   */
  const _applySubagentContent = (sessionId, payload) => {
    const data = payload.data || payload
    // subagentThreadId：子代理 SSE 定向推送路由标识符（handleSessionEvent 入口
    // 从事件顶层 subagent_thread_id 注入为 camelCase）。
    const subagentThreadId = payload.subagentThreadId || data.subagentThreadId || ''
    if (!subagentThreadId) {
      logger.debug(`[Sync] stream_subagent_content 缺少 subagentThreadId，丢弃: session=${sessionId}`)
      return
    }

    const session = getSession(sessionStore, sessionId)
    if (!session?.messages) {
      logger.debug(`[Sync] stream_subagent_content 会话未加载: session=${sessionId}`)
      return
    }
    let targetMsg = null
    if (payload.messageId) {
      targetMsg = findMessageById(session, payload.messageId)
    }
    if (!targetMsg) {
      targetMsg = getLastAssistantMessage(session)
    }
    if (!targetMsg) return

    const content = data.content || ''
    const reasoningContent = data.reasoningContent || ''
    if (!content && !reasoningContent) return

    // 动态初始化条目（chunk 先于 spawn 元数据到达的时序竞争防御）
    if (!targetMsg.subagentContents || typeof targetMsg.subagentContents !== 'object') {
      targetMsg.subagentContents = {}
    }
    const entry = targetMsg.subagentContents[subagentThreadId] || { content: '', reasoningContent: '' }
    if (content) entry.content = (entry.content || '') + content
    if (reasoningContent) entry.reasoningContent = (entry.reasoningContent || '') + reasoningContent
    if (data.agentName) entry.agentName = data.agentName
    if (typeof data.depth === 'number') entry.depth = data.depth
    targetMsg.subagentContents[subagentThreadId] = entry

    // 同步到当前版本快照（与 toolCalls 的版本同步语义一致）
    const ver = targetMsg.versions?.[targetMsg.currentVersion]
    if (ver) {
      if (!ver.subagentContents || typeof ver.subagentContents !== 'object') {
        ver.subagentContents = {}
      }
      const verEntry = ver.subagentContents[subagentThreadId] || { content: '', reasoningContent: '' }
      if (content) verEntry.content = (verEntry.content || '') + content
      if (reasoningContent) verEntry.reasoningContent = (verEntry.reasoningContent || '') + reasoningContent
      if (data.agentName) verEntry.agentName = data.agentName
      if (typeof data.depth === 'number') verEntry.depth = data.depth
      ver.subagentContents[subagentThreadId] = verEntry
    }

    logger.debug(
      `[Sync] stream_subagent_content 应用: session=${sessionId}, message=${targetMsg.backendId || targetMsg.id}, ` +
      `threadId=${subagentThreadId}, contentLen=${content.length}, reasoningLen=${reasoningContent.length}`
    )
  }

  /**
   * 应用 stream_reasoning 事件（session 频道，聊天模块深度研究模式的推理内容）
   *
   * 深度研究 worker（adapter.py）每次 LLM 节点产生 reasoning_content 时广播
   * STREAM_REASONING，payload 中携带该节点的完整推理文本。此处写入
   * ChatMessage.reasoning（duration=0 表示推理进行中，与代理模式 strategy.py
   * "duration: 0" 语义一致）；推理结束由 handleStreamCompleted 回写 duration。
   *
   * 与 task 频道（handleTaskEvent → researchStore.setTaskReasoning）双通道写入，
   * 分别驱动聊天消息 AiReasoning 与深度研究详情页 AiReasoning。
   *
   * @param {string} sessionId
   * @param {Object} payload - toCamelCase 后：{ source, sourceId, messageId, sessionId, taskId, data: { content } }
   */
  const _applyStreamReasoning = (sessionId, payload) => {
    const content = payload.data?.content || payload.content || ''
    if (!content) return
    const session = getSession(sessionStore, sessionId)
    let targetMsg = null
    if (payload.messageId) {
      targetMsg = findMessageById(session, payload.messageId)
    }
    if (!targetMsg) {
      targetMsg = getLastAssistantMessage(session)
    }
    if (!targetMsg) {
      logger.debug(`[Sync] stream_reasoning 未找到目标消息: session=${sessionId}, message=${payload.messageId || '(兜底)'}`)
      return
    }
    targetMsg.reasoning = { content, duration: 0 }
  }

  /**
   * 应用 session 通道事件
   * @param {RealtimeEvent} event
   */
  const applySessionEvent = async (event) => {
    const sessionId = _getSessionId(event)
    if (!sessionId) {
      logger.warn('[Sync] session 事件缺少 sessionId:', event.type)
      return
    }

    // 事件去重（Task 4 seq 单一权威）：
    // - 事件级"已见"去重由 useRealtimeSync.lastSeq（决定 replay 起点）+ 本层有序队列
    //   expectedSeq（丢弃 seq < expectedSeq 的重复/过期事件）负责，此处不再做独立去重，
    //   避免双基线发散导致跳号误判。
    // - 跳号检测见下方（基于 seqDedup 基线，基线由 applySessionEvent 末尾与
    //   sync.js advanceBaseline 联动推进）。

    // 目标消息处于 streaming / interrupted / finalizing / syncing 状态时，跳过与 SSE 重叠的
    // WebSocket message_updated 事件，避免滞后快照覆盖本地正在流式追加的最新内容。
    // 工具调用事件（7 个 tool_call_*）不跳过：所有浏览器统一经 WebSocket tool_call_* 事件
    // 写入（addOrUpdateToolCallInMap / updateOrAddToolResultInMap，id/toolCallId 精确匹配），
    // 重复推送同一工具时幂等合并，无需按流状态跳过（Task 5.1）。
    const targetStreamState = _getTargetMessageStreamState(sessionId, event)
    const earlyPayload = event.payload || event
    // message_updated 中的内容增长事件：INTERRUPTED 状态下放行
    // 确保审批中断时保存的 AI 文字内容能同步到所有浏览器
    // 支持两种 payload 结构：嵌套（earlyPayload.message.content）和扁平（earlyPayload.content）
    // writeback_to_chat_message 发布的是扁平结构，chat 流式发布的是嵌套结构
    const isContentGrowingUpdate = event.type === 'message_updated'
      && targetStreamState === StreamState.INTERRUPTED
      && (earlyPayload?.message?.content || earlyPayload?.content)
      && (() => {
        // 获取本地消息内容长度进行比较，后端内容更长时放行
        const session = sessionStore.sessions.find(s => s.id === sessionId)
        const targetMsg = session?.messages?.find(m =>
          m.backendId?.toString() === (earlyPayload.messageId)?.toString()
        )
        const localLen = (targetMsg?.content || '').length
        const backendContent = earlyPayload?.message?.content || earlyPayload?.content
        const backendLen = (backendContent || '').length
        return backendLen > localLen
      })()
    // 执行与连接解耦后所有浏览器统一处理：INTERRUPTED 状态下跳过 message_updated
    // （本地审批状态是最新的），但内容增长的 message_updated 必须放行
    if (event.type === 'message_updated'
        && targetStreamState
        && PROTECTED_STREAM_STATES.has(targetStreamState)
        && targetStreamState === StreamState.INTERRUPTED
        && !isContentGrowingUpdate) {
      logger.debug(`[Sync] INTERRUPTED 态，跳过 WS ${event.type}: session=${sessionId}`)
      // 仍需更新 seq，避免跳号检测误触发全量同步
      if (typeof event.seq === 'number') {
        seqDedup.setSeenSeq(sessionId, event.seq)
      }
      return
    }

    // 事件序列跳号检测（Task 6 seq 治理后，此路径基本不再因间隙触发）：
    // seqDedup 基线由三条路径共同推进，保证与有序队列 expectedSeq 收敛一致：
    // - applySessionEvent 末尾 setSeenSeq（处理成功）
    // - sync.js advanceBaseline（orderedQueue 丢弃过期事件联动）
    // - sync.js handleOrderedQueueGap（orderedQueue 间隙停滞联动：处理最小 seq 前
    //   推进基线到 minSeqInQueue - 1，跳过缺失 seq，避免此处误判跳号）
    // 间隙场景已由"正常处理最小 seq + 快照校对"兜底（见 orderedQueue.js / sync.js），
    // 此处 requestFullSync 仅作为最后防线，应对真正的事件丢失
    // （replay 完成后的状态校验失败、处理链异常等显式错误），不再因单次间隙触发。
    const prevSeq = seqDedup.getPrevSeq(sessionId)
    const eventSeq = typeof event.seq === 'number' ? event.seq : prevSeq + 1
    if (eventSeq > prevSeq + 1 && !fullSyncPending.has(sessionId)) {
      const isFirstEvent = prevSeq === 0
      if (isFirstEvent) {
        // 首次事件跳号是正常行为（页面刚加载，跳号基线未初始化）
        // 不触发全量同步，避免在 SSE 流式期间 loadSessionDetail 全量替换 session 对象，
        // 破坏前端占位消息引用，导致 SSE 回调（appendToLastMessage 等）失效产生两个 AI 气泡
        // 会话详情已通过 loadSessionDetail/switchSession 加载，历史事件无需同步
        logger.info(`[Sync] 首次事件跳号(正常，跳过全量同步): session=${sessionId}, got=${eventSeq}`)
      } else {
        // 兜底路径：正常间隙已被 orderedQueue + handleOrderedQueueGap 处理
        // （推进基线 + 快照校对），到达此处说明存在异常（如间隙停滞回调失败、
        // 基线推进缺失、或后端序列号真正不连续），视为显式错误触发全量同步。
        logger.warn(
          `[Sync] 事件跳号触发全量同步(兜底): session=${sessionId}, ` +
          `expected=${prevSeq + 1}, got=${eventSeq}, ` +
          `reason=间隙停滞回调未推进基线或后端序列号不连续`
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

    // subagent_thread_id（spec D10）：事件顶层协议路由标识符（snake_case，不参与
    // camelCase 转换），由 useRealtimeSync.onMessage 还原后保留在 event 顶层。
    // 注入 payload.subagentThreadId（camelCase）供下游 toolCallHandler / approval
    // 归集按子代理 thread 路由，前端其余代码不直接访问 snake_case 键。
    if (event.subagent_thread_id) {
      payload.subagentThreadId = event.subagent_thread_id
    }

    switch (event.type) {
      case 'message_added':
        ctx.handleMessageAdded(sessionId, payload.message || payload)
        break
      case 'message_updated':
        ctx.handleMessageUpdated(sessionId, payload.messageId, payload.fields || payload)
        break
      case 'stream_event':
        ctx.handleStreamEvent(sessionId, payload)
        break
      // 深度研究推理内容（stream_reasoning 事件，session 频道）
      // 聊天模块深度研究模式：Celery worker 每次 LLM 节点产生推理时广播
      // STREAM_REASONING 到 session + task 双频道。session 频道在此写入
      // ChatMessage.reasoning（duration=0 表示推理进行中，与代理模式语义一致）。
      case 'stream_reasoning':
        _applyStreamReasoning(sessionId, payload)
        break
      // 子代理图层正文/中间思考更新（spec D10/MODIFIED：路由收敛）：
      // 后端 STREAM_SUBAGENT_CONTENT 事件（adapter._on_subagent_content /
      // chat_service._on_subagent_content），payload.data 携带
      // content/reasoningContent/agentName/depth（data 不含 agentPath）。
      // 前端按顶层 subagentThreadId 路由写入 message.subagentContents[threadId]，
      // 实时累计子代理图层正文。
      case 'stream_subagent_content':
        _applySubagentContent(sessionId, payload)
        break
      case 'messages_deleted':
        ctx.handleMessagesDeleted(sessionId, payload.deletedMessageIds || payload.ids || [])
        break
      // 工具调用事件类型（每个 EventType 独立 ws_event_name）
      case 'tool_call_pending':
      case 'tool_call_waiting':
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
        await ctx.handleApprovalEvent(sessionId, payload, event.type, {
          source: payload.source,
          isReplay: event.isReplay === true,
        })
        break
      case 'stream_interrupted':
        ctx.handleStreamInterrupted(sessionId, payload)
        break
      case 'stream_completed':
        ctx.handleStreamCompleted(sessionId, payload)
        break
      case 'status_change':
        // 聊天关联深度研究任务状态实时推送（session + task 双频道，Task 4）。
        // task 频道由 handleTaskEvent 处理；此处处理 session 频道冗余路径，
        // 确保 ChatView 打开时 DeepResearchView 的 taskInfo 也实时更新。
        // payload.source_id = task_id（DEEP_RESEARCH 来源）。
        if (payload.sourceId) {
          researchStore.setTaskStatus(payload.sourceId, payload)
          logger.info(
            `[Sync] session status_change(关联研究): taskId=${payload.sourceId}, ` +
            `status=${payload.status || 'unknown'}, session=${sessionId}`
          )
        }
        break
      case 'stream_finalized':
        await ctx.handleStreamFinalized(sessionId, payload)
        break
      case 'message_regenerated':
        ctx.handleMessageRegenerated(sessionId, payload)
        break
      case 'message_regenerate_reverted':
        ctx.handleMessageRegenerateReverted(sessionId, payload)
        break
      case 'message_finalized':
        ctx.handleMessageFinalized(sessionId, payload)
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
      scheduleSubagentsRefresh(chatSessionId || taskId)
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

    // 2b. 批量审批：INTERRUPTED → STREAMING（当 sibling approved/processing 时）
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

  // 将 handleApprovalEvent 注入 ctx，使 switch 可通过 ctx.handleApprovalEvent 调用
  ctx.handleApprovalEvent = handleApprovalEvent

  return {
    handleSessionEvent,
    applySessionEvent,
    handleToolCallEvent,
    handleApprovalEvent,
  }
}
