import { logger } from '@/utils/logger'
import { toCamelCase } from '@/utils/sessionTransformers'
import { getEventSessionId } from '@/utils/eventRouting'
import { StreamState, PROTECTED_STREAM_STATES } from '@/types'
import { TOOL_CALL_EVENT_TYPES, APPROVAL_EVENT_TYPES } from '@/types/realtimeEvents'
import { createHandleToolCallEvent } from './toolCallHandler'
import { createHandleApprovalEvent } from './approvalHandler'
import { createSubagentHandlers } from './subagentHandlers'
import { findMessageById, getSession, getLastAssistantMessage } from './helpers'

/**
 * @typedef {import('@/composables/useRealtimeSync').RealtimeEvent} RealtimeEvent
 */

/**
 * 创建 session 通道事件处理器（路由层）
 *
 * 模块关系（C5/cq-04 Task 2 收敛后，本模块为纯路由层，业务逻辑在独立 handler 模块）：
 * - 工厂内直接装配（与 toolCallHandler 接法一致）：toolCallHandler.js /
 *   approvalHandler.js（含 APPROVAL_STATE_MAP 映射 / 批量审批联动 / streamState 转换）、
 *   subagentHandlers.js（applySubagentContent / handleSubagentStatusChange / handleResearchStatusChange）
 * - ctx 注入（sync.js 装配后传入，仅使用注入版本，无内联实现）：messageHandlers.js
 *   （6 个 handleMessage* 系列）、streamStateHandlers.js（4 个 handleStream* + applyStreamReasoning）
 *
 * 本模块保留：事件入口（payload 转换 / 会话路由 / 有序队列接入）、applySessionEvent 守卫
 * （INTERRUPTED 跳过 / 内容增长放行 / 跳号检测 / 兜底拉取）、TOOL_CALL / APPROVAL Set 提前路由、
 * subagent_thread_id 协议标识符还原注入、一行委托式 switch。
 *
 * @param {Object} ctx - 依赖上下文（含注入的独立模块函数）
 * @param {Object} ctx.sessionStore - session store 实例
 * @param {Object} ctx.approvalStore - approval store 实例
 * @param {Object} ctx.researchStore - research store 实例
 * @param {{ getPrevSeq: Function, setSeenSeq: Function }} ctx.seqDedup - 跳号检测基线
 *   （事件已见去重由 useRealtimeSync.lastSeq 与 orderedQueue.expectedSeq 负责）
 * @param {{ process: Function }} ctx.orderedQueue - 来自 createOrderedQueue，提供有序队列处理能力
 * @param {Set<string>} ctx.fullSyncPending - 避免重复触发全量同步的 Set
 * @param {(sessionId: string, options?: Object) => Promise<{backendMessages: Array}|null>} ctx.requestFullSync - 兜底全量同步函数
 * @returns {{
 *   handleSessionEvent: (event: RealtimeEvent) => Promise<void>,
 *   applySessionEvent: (event: RealtimeEvent) => Promise<void>,
 *   handleToolCallEvent: (sessionId: string|null, taskId: string|null, payload: Object, eventType: string, source?: string) => Promise<void>,
 *   handleApprovalEvent: (sessionId: string|null, payload: Object, eventType: string, options?: { taskId?: string, source?: string }) => Promise<void>,
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

  // 审批事件处理器（三模块共享，由 approvalHandler.js 工厂函数创建）
  const { handleApprovalEvent } = createHandleApprovalEvent({ sessionStore, approvalStore })

  // 子代理 / 关联研究状态事件处理器（由 subagentHandlers.js 工厂函数创建）
  const { applySubagentContent, handleSubagentStatusChange, handleResearchStatusChange } = createSubagentHandlers({ sessionStore, researchStore })

  /**
   * 处理 session 通道事件（由 ChatView 等订阅方回调）
   * @param {RealtimeEvent} event
   */
  const handleSessionEvent = async (event) => {
    // 统一入站转换：WebSocket 事件 payload snake_case → camelCase，转换后所有下游
    // handler（messageHandlers/toolCallHandler/approvalHandler）收到的数据均为
    // camelCase，禁止再访问 snake_case 键名。
    // 防御：转换异常时跳过该事件（由后续 replay/fullSync 补偿），保证函数不抛异常，
    // 避免事件不进 orderedQueue 直接丢失
    try {
      event.payload = toCamelCase(event.payload)
    } catch (err) {
      logger.error(
        `[Sync] session 事件 payload 转换失败，跳过事件: type=${event.type}, error=${err?.message || err}`
      )
      return
    }
    // 路由字段解析（P3-R1 根因修复，统一入口 getEventSessionId）：后端将 session_id
    // 注入事件顶层（与 payload 平级，见 realtime_events.py _publish_to_session_async），
    // payload 内部不含 session_id；统一 helper 优先 payload，其次 event 顶层，
    // 否则所有 WebSocket 事件被静默丢弃。
    const sessionId = getEventSessionId(event)
    if (!sessionId) return

    // 使用有序队列处理，避免 async 回调乱序导致 seq 回退
    await orderedQueue.process(sessionId, event, async (ev) => {
      await applySessionEvent(ev)
    })
  }

  /**
   * 获取事件目标消息的 streamState。
   * message_updated / tool_call_* / approval_* 等事件可能携带 message_id；
   * 若未携带，则兜底使用最后一条 assistant 消息的状态。
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
   * 应用 session 通道事件
   * @param {RealtimeEvent} event
   */
  const applySessionEvent = async (event) => {
    // sessionId 为 schema 必填字段（P3-R1）：统一委托 getEventSessionId 解析
    // （优先 payload，其次 event 顶层，null 兜底）
    const sessionId = getEventSessionId(event)
    if (!sessionId) {
      logger.warn('[Sync] session 事件缺少 sessionId:', event.type)
      return
    }

    // 事件去重（Task 4 seq 单一权威）：事件级"已见"去重由 useRealtimeSync.lastSeq
    // （replay 起点）+ 有序队列 expectedSeq（丢弃过期事件）负责，此处不做独立去重，
    // 避免双基线发散导致跳号误判；跳号检测见下方（基线推进唯一权威是有序队列）。
    // 目标消息处于 streaming / interrupted / finalizing / syncing 状态时，跳过与 SSE 重叠的
    // WebSocket message_updated 事件，避免滞后快照覆盖本地正在流式追加的最新内容。
    // 工具调用事件（7 个 tool_call_*）不跳过：统一经 WebSocket 幂等合并写入（Task 5.1）。
    const targetStreamState = _getTargetMessageStreamState(sessionId, event)
    const earlyPayload = event.payload || event
    // message_updated 中的内容增长事件：INTERRUPTED 状态下放行（审批中断时保存的
    // AI 文字内容需同步到所有浏览器）；支持嵌套（message.content）和扁平
    // （content）两种结构：writeback_to_chat_message 发布扁平，chat 流式发布嵌套
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
      // 基线推进由有序队列 onProcessed 统一承担（本事件已入队处理），无需在此更新
      return
    }

    // 事件序列跳号检测（Task 6 seq 治理后仅作真正事件丢失的最后防线）：
    // 正常间隙/乱序已由"间隙等待 + 快照校对"兜底（见 orderedQueue.js / sync.js），
    // 此处 requestFullSync 仅应对显式错误（replay 完成后状态校验失败、处理链异常等）。
    const prevSeq = seqDedup.getPrevSeq(sessionId)
    const eventSeq = typeof event.seq === 'number' ? event.seq : prevSeq + 1
    if (eventSeq > prevSeq + 1 && !fullSyncPending.has(sessionId)) {
      const isFirstEvent = prevSeq === 0
      if (isFirstEvent) {
        // 首次事件跳号是正常行为（页面刚加载，跳号基线未初始化）：不触发全量同步，
        // 避免 SSE 流式期间 loadSessionDetail 全量替换 session 对象，破坏前端占位消息
        // 引用导致 SSE 回调（appendToLastMessage 等）失效产生两个 AI 气泡
        // （会话详情已通过 loadSessionDetail/switchSession 加载，历史事件无需同步）
        logger.info(`[Sync] 首次事件跳号(正常，跳过全量同步): session=${sessionId}, got=${eventSeq}`)
      } else {
        // 兜底路径：正常间隙已被 orderedQueue + handleOrderedQueueGap 处理（推进基线 +
        // 快照校对），到达此处说明存在异常（间隙停滞回调失败、基线推进缺失、
        // 或后端序列号真正不连续），视为显式错误触发全量同步。
        logger.warn(
          `[Sync] 事件跳号触发全量同步(兜底): session=${sessionId}, ` +
          `expected=${prevSeq + 1}, got=${eventSeq}, ` +
          `reason=间隙停滞回调未推进基线或后端序列号不连续`
        )
        fullSyncPending.add(sessionId)
        // finally 派生 promise 需消费 rejection：requestFullSync 失败（如 404）
        // 时派生 promise 同步 reject 且无人消费 → Unhandled Promise Rejection
        requestFullSync(sessionId)
          .finally(() => fullSyncPending.delete(sessionId))
          .catch(() => {
            logger.warn(`[Sync] 兜底全量同步失败: session=${sessionId}`)
          })
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

    // rd-05：工具调用 / 审批事件组以 Set.has 提前路由（枚举权威源
    // types/realtimeEvents.js，新增事件类型只需扩展 Set）
    if (TOOL_CALL_EVENT_TYPES.has(event.type)) {
      await handleToolCallEvent(sessionId, null, payload, event.type, payload.source)
      // 基线推进由有序队列 onProcessed 统一承担（本事件已入队处理），此处不再自行推进
      return
    }
    if (APPROVAL_EVENT_TYPES.has(event.type)) {
      await handleApprovalEvent(sessionId, payload, event.type, {
        source: payload.source,
        isReplay: event.isReplay === true,
      })
      // 基线推进由有序队列 onProcessed 统一承担（本事件已入队处理），此处不再自行推进
      return
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
      // 深度研究推理内容（session 频道，写入 ChatMessage.reasoning，见 streamStateHandlers）
      case 'stream_reasoning':
        ctx.applyStreamReasoning(sessionId, payload)
        break
      // 子代理图层正文/中间思考（写入 message.subagentContents[threadId]，见 subagentHandlers）
      case 'stream_subagent_content':
        applySubagentContent(sessionId, payload)
        break
      // 子代理状态变更（刷新子代理元数据，见 subagentHandlers）
      case 'subagent_status_change':
        handleSubagentStatusChange(sessionId, payload)
        break
      case 'messages_deleted':
        ctx.handleMessagesDeleted(sessionId, payload.deletedMessageIds || payload.ids || [])
        break
      case 'stream_interrupted':
        ctx.handleStreamInterrupted(sessionId, payload)
        break
      case 'stream_completed':
        ctx.handleStreamCompleted(sessionId, payload)
        break
      // 聊天关联深度研究任务状态（session 频道冗余路径，见 subagentHandlers）
      case 'status_change':
        handleResearchStatusChange(sessionId, payload)
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
    // 基线推进由有序队列 onProcessed 统一承担（本事件已入队处理），此处不再自行推进
  }

  return {
    handleSessionEvent,
    applySessionEvent,
    handleToolCallEvent,
    handleApprovalEvent,
  }
}
