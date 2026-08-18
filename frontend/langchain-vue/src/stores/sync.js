import { defineStore } from 'pinia'
import { watch, reactive } from 'vue'
import { useRealtimeSync } from '@/composables/useRealtimeSync'
import { useSnapshotSync } from '@/composables/useSnapshotSync'
import { logger } from '@/utils/logger'
import { mergeMessageFromBackend } from '@/utils/messageOperations'
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
 * 架构说明：执行与连接解耦后的 WebSocket 单一事件通道
 *
 * 聊天执行由 FastAPI 执行服务单协程运行（chat 与 deep_research 同构），
 * 前端发送消息为普通 POST（{ status: 'started' }），所有流式/工具/审批事件
 * 统一经 WebSocket 广播，所有浏览器（含请求浏览器）一致消费，无 SSE 通道。
 *
 * WebSocket（所有浏览器共享）：
 *   - 跨浏览器同步事件（session_created/deleted/updated 等）
 *   - 7 个工具调用事件（tool_call_pending/waiting/running/completed/failed/timeout/rejected）：
 *     所有浏览器统一通过 WebSocket 接收工具调用状态
 *   - 6 个审批事件（approval_pending/processing/waiting/approved/rejected/timeout）：
 *     所有浏览器通过 WebSocket 同步审批状态
 *   - message_updated：供所有浏览器感知消息内容变更
 *   - stream_event / stream_completed / stream_finalized：流式内容与结束/最终化信号
 *   - 4 个学习工作流事件（workflow_step / workflow_state_update / workflow_completed / workflow_failed）：
 *     所有浏览器通过 WebSocket 同步学习工作流进度与状态（Task 23），
 *     由 onWorkflowEvent 回调委托给 workflowStore，WorkflowView watch store 更新 UI
 *
 * 工具调用状态唯一真相源：
 *   工具调用状态统一由 WebSocket 推送（tool_call_* 事件，所有浏览器一致），
 *   汇聚到 sessionStore.toolCallsMap（Map 为唯一真相源），
 *   通过 _findMatchingToolCall 的 id 精确匹配 + PROTECTED_STATUSES 状态保护
 *   实现幂等合并。
 *
 * 请求浏览器唯一剩余差异（streamingSessions）：
 *   请求浏览器在 stream_completed 后由 sendMessage 主流程执行 finalizeStream
 *   （最终 PATCH + chatFinalize → 后端广播 stream_finalized），因此
 *   handleStreamCompleted / handleStreamFinalized 对请求浏览器不重复全量同步，
 *   其余浏览器在 stream_finalized 后统一全量同步并最终化 toolCalls。
 *
 * 模块拆分说明（Task 10）：
 *   主文件仅负责状态初始化、装配各 handler、公共方法、watch 与 return。
 *   具体事件处理逻辑拆分到 ./sync/ 子目录下：
 *   - constants.js     : 事件类型与状态映射常量
 *   - seqDedup.js      : seq 跳号检测基线（Task 4 后不再做事件已见去重，lastSeq 为唯一权威）
 *   - orderedQueue.js  : 有序事件队列（仅排序与间隙等待，丢弃时联动 advanceBaseline）
 *   - handleUserEvent.js   : user 通道事件处理（session_created/deleted/updated）
 *   - handleSessionEvent.js: session 通道事件处理（含工具调用/审批/消息/流式状态等）
 *   - handleTaskEvent.js   : task 通道事件处理（独立深度研究 + 学习工作流 workflow_* 事件）
 *
 * 业务 store 依赖解耦（SubTask 8.2）：
 *   本文件不再静态 import session/approval/research/workflow 4 个业务 store
 *   （原顶层静态依赖与 approval.js → sync.js 形成模块加载期循环依赖）。
 *   现改为惰性解析：
 *   - 模块加载期仅保留 sync.js → useRealtimeSync.js 单向静态依赖；
 *   - 业务 store 实例经 _getStores()（动态 import + Promise 缓存）在运行期解析；
 *   - 11 个 handler 工厂由 _ensureHandlers()（装配器）在首次使用时接收注入的
 *     store 实例完成装配，即"调用时注入"。
 *   本 store 为同步 setup，setup 期不触碰任何业务 store（同步可用的状态与方法
 *   均不依赖业务 store）；异步入口（handleRealtimeEvent / handleApprovalAction /
 *   initialize / watch 回调 / requestFullSync）内部 await 解析。
 */
export const useSyncStore = defineStore('sync', () => {
  const realtime = useRealtimeSync()

  /** @type {(() => void) | null} */
  let unsubscribeUser = null

  // === 状态初始化 ===

  /**
   * 惰性加载业务 store 模块（SubTask 8.2：消除 sync.js ↔ 业务 store 静态循环依赖）
   *
   * 仅缓存模块导入 Promise（与 useRealtimeSync.js 的 _getSyncStoreModule 同模式），
   * store 实例在每个调用点通过 useXStore() 按当前 pinia 实例解析（保持原语义）。
   * 本函数只应被 async 方法/回调调用，禁止在同步 setup 中使用。
   */
  /** @type {Promise<[Object, Object, Object, Object]> | null} */
  let storeModulesPromise = null
  const _getStores = async () => {
    if (!storeModulesPromise) {
      storeModulesPromise = Promise.all([
        import('@/stores/session'),
        import('@/stores/approval'),
        import('@/stores/research'),
        import('@/stores/workflow'),
      ])
    }
    const [sessionMod, approvalMod, researchMod, workflowMod] = await storeModulesPromise
    return {
      sessionStore: sessionMod.useSessionStore(),
      approvalStore: approvalMod.useApprovalStore(),
      researchStore: researchMod.useResearchStore(),
      workflowStore: workflowMod.useWorkflowStore(),
    }
  }

  /**
   * seq 跳号检测基线（Task 4 收敛后：不再承担"事件已见"去重，
   * 仅维护 per-session 基线供 handleSessionEvent.js 跳号检测使用；
   * 事件级去重唯一权威是 useRealtimeSync.lastSeq）
   */
  const seqDedup = createSeqDedup()

  /**
   * 统一推进事件基线（Task 4：双基线发散修复的唯一联动点）
   *
   * 职责：同时推进 seqDedup 跳号检测基线 与 realtime.lastSeq 事件级去重基线，
   * 保证各基线单调收敛、互不背离，避免 orderedQueue 丢弃事件（seq < expectedSeq）后
   * seqDedup 基线不推进导致 handleSessionEvent.js 跳号检测误判、
   * 触发多余 requestFullSync 覆盖较新状态。
   *
   * 调用路径：
   * - orderedQueue 丢弃事件时（createOrderedQueue({ onDropped }) 注入本函数）
   * - 其他需要显式推进基线的路径（如有）
   *
   * @param {string} sessionId
   * @param {number} seq - 事件 seq（丢弃事件或需推进基线的 seq）
   */
  const advanceBaseline = (sessionId, seq) => {
    if (!sessionId || typeof seq !== 'number') return
    seqDedup.setSeenSeq(sessionId, seq)
    // lastSeq 为事件级去重唯一权威（channel 维度），同步推进保持基线收敛
    realtime.advanceLastSeq(`session_${sessionId}`, seq)
  }

  /**
   * 跳号检测触发的全量同步去重锁（单一职责：仅锁 handleSessionEvent.js 的跳号检测路径）
   *
   * Task 9.2 与 streamStateHandlers.guardedRequestFullSync（streamStateHandlers.js 内部
   * 自维护同一会话的全量同步锁）的分工说明：
   *   - 本锁：handleSessionEvent.js 事件序列跳号检测（seq > prevSeq + 1）触发的
   *     requestFullSync 兜底（应对真正的事件丢失）
   *   - streamStateHandlers 锁：流结束路径（stream_completed / stream_finalized）触发的
   *     guardedRequestFullSync（后端数据已持久化，可安全全量同步）
   * 两锁各自独立、互不感知（模块边界清晰，改动面最小原则下不合并）。
   * 同一会话同一时刻至多各发起一次全量同步，requestFullSync 内部按保护态合并，
   * 不会因两处并发请求产生数据回退。
   *
   * @type {Set<string>}
   */
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
   * 有序队列间隙停滞回调（Task 6：间隙即触发快照校对）
   *
   * 触发时机：orderedQueue 等待 GAP_WAIT_MS 后 expectedSeq 仍缺失（真实乱序/丢包），
   * 将正常处理队列中最小 seq 事件，并在处理前同步调用本函数。
   *
   * 处理内容：
   * 1. 推进 seqDedup 跳号检测基线到 minSeqInQueue - 1（标记缺失 seq 为"已跳过"），
   *    避免 applySessionEvent 处理 minSeqInQueue 时误判跳号（eventSeq > prevSeq + 1）
   *    触发 requestFullSync 全量替换本地较新状态。
   *    注意：**不推进 realtime.lastSeq**——lastSeq 只由已处理事件推进
   *    （advanceLastSeq 是唯一写入入口）；缺失事件从未到达/从未处理，不视为"已见"，
   *    重连 replay 仍可从缺失 seq 补发。
   * 2. 异步触发一次快照校对（useSnapshotSync），补齐 gap 造成的状态缺失：
   *    - 非流式期间：直接触发
   *    - 流式期间：不触发（SSE 提供内容、WebSocket 事件为补充，避免打断流式渲染），
   *      由流结束路径（stream_completed / stream_finalized 的 guardedRequestFullSync）兜底
   *
   * requestFullSync 不在此处触发：间隙 ≠ 事件丢失（P3-R1 已修复事件路由后
   * 真实丢包概率极低），全量同步只应在真正需要时触发（如 replay 完成后的
   * 状态校验失败、处理链显式错误），避免因单次间隙误触发全量同步。
   *
   * @param {string} sessionId
   * @param {number} expectedSeq - 间隙起始 seq（缺失序列的首个 seq）
   * @param {number} minSeqInQueue - 将正常处理的最小 seq（缺失序列的末个 seq + 1）
   */
  const handleOrderedQueueGap = (sessionId, expectedSeq, minSeqInQueue) => {
    if (!sessionId || typeof minSeqInQueue !== 'number') return
    const gapEndSeq = minSeqInQueue - 1
    // 1. 推进跳号检测基线（跳过缺失 seq，防 applySessionEvent 误判跳号触发全量同步）
    seqDedup.setSeenSeq(sessionId, gapEndSeq)
    // 2. 流式期间不触发快照校对，流结束后由 guardedRequestFullSync 兜底
    if (streamingSessions.has(sessionId)) {
      logger.info(
        `[Sync] 流式期间事件间隙，跳过快照校对（流结束后兜底）: session=${sessionId}, ` +
        `gap=[${expectedSeq}, ${gapEndSeq}]`
      )
      return
    }
    // 3. 异步触发快照校对，补齐 gap 造成的状态缺失（不阻塞事件处理）
    try {
      const { syncFromSnapshot } = useSnapshotSync(sessionId)
      syncFromSnapshot().catch(err => {
        logger.warn(
          `[Sync] 间隙快照校对失败（非致命）: session=${sessionId}, gap=[${expectedSeq}, ${gapEndSeq}], ` +
          `error=${err?.message || err}`
        )
      })
    } catch (err) {
      logger.warn(
        `[Sync] 触发间隙快照校对异常（非致命）: session=${sessionId}, gap=[${expectedSeq}, ${gapEndSeq}], ` +
        `error=${err?.message || err}`
      )
    }
  }

  /** session 通道事件有序队列（封装 _sessionEventQueue Map 与 _processSessionEventOrdered）；
   *  仅负责排序与间隙等待；丢弃过期事件时通过 onDropped 联动 advanceBaseline，
   *  间隙停滞时通过 onGapStalled 联动 handleOrderedQueueGap（推进跳号基线 + 快照校对），
   *  处理完成时通过 onProcessed 联动 advanceBaseline（本队列为序列处理唯一权威，
   *  处理完即推进 seqDedup/lastSeq 基线，消除双基线发散导致的跳号误判） */
  const orderedQueue = createOrderedQueue({
    onDropped: advanceBaseline,
    onGapStalled: handleOrderedQueueGap,
    onProcessed: advanceBaseline,
  })

  // === 公共方法（与流式生命周期相关） ===

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

    const { sessionStore } = await _getStores()

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

  // === 装配 handlers（惰性，SubTask 8.2） ===
  // 分层装配（Task 16）：底层 handler → 流式状态 handler → session handler
  // 独立模块（messageHandlers / streamStateHandlers / messageIntegrity）
  // 提供与 handleSessionEvent.js 内联版本逻辑等价的工厂函数，
  // 通过依赖注入方式传入 createHandleSessionEvent，实现模块职责分离。
  //
  // 装配器接收 _getStores() 注入的 store 实例（"调用时注入"），
  // 仅在首次需要时执行一次（Promise 缓存）；所有被注入的 store 均在
  // 事件/动作触发时（运行期）解析，模块加载期无业务 store 依赖。
  /** @type {Promise<{ handleSessionEvent: Function, handleTaskEvent: Function, handleUserEvent: Function }> | null} */
  let handlersPromise = null
  const _ensureHandlers = () => {
    if (!handlersPromise) {
      handlersPromise = _getStores().then(({ sessionStore, approvalStore, researchStore, workflowStore }) => {
        // Step 1: 创建底层 handler（消息 CRUD + 完整性校验）
        const messageHandlers = createMessageHandlers({ sessionStore })
        const integrityHandlers = createMessageIntegrityHandlers({ sessionStore })

        // Step 2: 创建流式状态 handler（依赖 messageIntegrity）
        const streamStateHandlers = createStreamStateHandlers({
          sessionStore,
          approvalStore,
          researchStore,
          streamingSessions,
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
          fullSyncPending,
          requestFullSync,
          // 注入独立模块函数，handleSessionEvent.js 中优先使用注入版本，回退内联版本
          ...messageHandlers,
          ...streamStateHandlers,
        })
        const {
          handleSessionEvent,
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

        const { handleUserEvent } = createHandleUserEvent({
          sessionStore,
          realtime,
          handleRealtimeEvent,
        })

        return { handleSessionEvent, handleTaskEvent, handleUserEvent }
      })
    }
    return handlersPromise
  }

  /**
   * 统一实时事件处理器（三模块共享入口）
   *
   * 合并 handleSessionEvent + handleTaskEvent 的统一入口，通过 session_id / task_id
   * 自动路由到正确的处理通道。路由字段解析顺序：
   *   payload.sessionId || event.sessionId
   *   payload.taskId    || event.taskId
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
   * 均使用此函数作为回调（含 handleUserEvent 中 session_created 自动订阅），
   * 通过 payload + event 顶层字段自动区分模块和 store，避免双重订阅。
   *
   * @param {RealtimeEvent} event - 实时事件对象
   */
  const handleRealtimeEvent = async (event) => {
    const { handleSessionEvent, handleTaskEvent } = await _ensureHandlers()
    const payload = event?.payload || event || {}
    // 修复：从 event 顶层提取 sessionId/taskId，作为 fallback
    // 后端 _publish_to_session_async 将 sessionId 注入到 event 顶层（与 payload 平级），不在 payload 内。
    // 与 useRealtimeSync.getChannelKey / dispatchEvent / handleSessionEvent 解析逻辑对齐。
    const sessionId = payload.sessionId || event.sessionId
    const taskId = payload.taskId || event.taskId

    if (sessionId) {
      await handleSessionEvent(event)
    } else if (taskId) {
      await handleTaskEvent(event)
    } else {
      // 日志级别 warn：修复后此分支不应再触发，保留用于异常排查
      logger.warn(`[Sync] handleRealtimeEvent 无法路由（缺少 session_id 和 task_id）: type=${event?.type}`)
    }
  }

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
   * - 仅重置本会话状态，不影响其他会话；不清理 streamingSessions。
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
   * 深度研究 SSE 审批事件转发（视图层入口收敛，SubTask 11.3）
   *
   * 背景：ChatView 深度研究 SSE 流中的审批事件（approval / approval_timeout /
   * approval_processed / approval_history）原由视图层直接调用 approvalStore，
   * 现统一经 sync 层转发，保持事件处理的语义与数据完全一致。
   *
   * 说明：SSE 事件类型与 WebSocket 审批事件（approval_pending 等 6 种，经
   * handleApprovalEvent 的状态映射 + streamState 转换）不同——深度研究 SSE
   * 直接下发审批业务数据，因此此处直接委托 approvalStore 处理，不做转换。
   *
   * 视图层以 fire-and-forget 方式调用（返回 Promise 不 await）；内部经 _getStores()
   * 惰性解析 approvalStore（SubTask 8.2），失败仅记录日志，不影响调用方。
   *
   * @param {'handleApprovalEvent'|'restoreFromSSEHistory'} action - 转发动作
   * @param {Object} data - 审批数据
   * @param {Object} [options] - 转发选项（透传 approvalStore 方法参数）
   * @param {string} [options.source] - 事件来源 'chat' | 'deep_research'
   * @param {string} [options.taskId] - 深度研究任务 ID
   * @param {string} [options.sessionId] - 聊天会话 ID
   * @returns {Promise<void>}
   */
  const handleApprovalAction = async (action, data, options = {}) => {
    try {
      const { approvalStore } = await _getStores()
      if (action === 'restoreFromSSEHistory') {
        approvalStore.restoreFromSSEHistory(data, options.taskId, options.sessionId)
        return
      }
      approvalStore.handleApprovalEvent(data, options)
    } catch (err) {
      logger.error('[Sync] handleApprovalAction 转发失败:', err)
    }
  }

  /**
   * 初始化同步监听
   */
  const initialize = async () => {
    try {
      if (unsubscribeUser) {
        unsubscribeUser()
      }
      const { handleUserEvent } = await _ensureHandlers()
      unsubscribeUser = realtime.subscribeUserEvents(handleUserEvent)
      logger.log('[Sync] 实时同步 store 已初始化')
    } catch (err) {
      logger.error('[Sync] 初始化实时同步 store 失败:', err)
    }
  }

  // WebSocket 重连成功后，对当前会话触发一次全量同步兜底
  watch(
    () => realtime.connectionStatus.value,
    async (status, prevStatus) => {
      if (prevStatus === 'reconnecting' && status === 'connected') {
        try {
          const { sessionStore } = await _getStores()
          const currentSessionId = sessionStore.currentSessionId
          if (currentSessionId) {
            logger.info(`[Sync] WebSocket 重连成功，触发全量同步: ${currentSessionId}`)
            requestFullSync(currentSessionId)
          }
        } catch (err) {
          logger.error('[Sync] 重连全量同步失败:', err)
        }
      }
    }
  )

  return {
    startStreaming,
    stopStreaming,
    handleRealtimeEvent,
    handleApprovalAction,
    initialize,
    resetSessionSeq,
  }
})
