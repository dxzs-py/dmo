import { logger } from '@/utils/logger'
import { StreamState } from '@/types'
import { ElNotification } from 'element-plus'
import {
  findMessageById,
  getSession,
  getCurrentVersion,
  getLastAssistantMessage,
} from './helpers'
import { finalizeToolCallsForResearchResult } from './messageIntegrity'

/**
 * @typedef {import('@/composables/useRealtimeSync').RealtimeEvent} RealtimeEvent
 */

/**
 * 创建流式状态机处理器
 *
 * 处理 stream_event / stream_reasoning / stream_interrupted /
 * stream_completed / stream_finalized 事件，维护消息的 StreamState 状态转换
 * 与推理内容（ChatMessage.reasoning）写入。
 *
 * @param {Object} ctx - 依赖上下文
 * @param {Object} ctx.sessionStore - session store 实例
 * @param {Object} ctx.approvalStore - approval store 实例
 * @param {Object} ctx.researchStore - research store 实例
 * @param {Set<string>} ctx.streamingSessions - reactive(new Set())，请求浏览器 SSE 活跃会话集合
 * @param {(sessionId: string, options?: Object) => Promise<{backendMessages: Array}|null>} ctx.requestFullSync
 *   - 兜底全量同步函数
 * @param {(message: Object, sessionId?: string) => void} ctx.finalizeToolCalls
 *   - 来自 messageIntegrity，最终化非终态 toolCalls
 * @param {(sessionId: string, messageId: string, backendMessages: Array) => void} ctx.verifyMessageIntegrity
 *   - 来自 messageIntegrity，全量同步后完整性校验
 * @returns {{
 *   handleStreamEvent: (sessionId: string, payload: Object) => void,
 *   applyStreamReasoning: (sessionId: string, payload: Object) => void,
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
    requestFullSync,
    finalizeToolCalls,
    verifyMessageIntegrity,
  } = ctx

  /**
   * 同一会话全量同步去重锁（本模块内部维护）
   * handleStreamCompleted（finalized=true 分支）与 handleStreamFinalized 可能对同一会话
   * 先后触发 requestFullSync，通过该 Set 确保同一会话同一时刻只发起一次全量同步。
   * @type {Set<string>}
   */
  const fullSyncPending = new Set()

  /**
   * 去重包装的 requestFullSync：同一会话已有进行中的全量同步时直接返回 null
   * @param {string} sessionId
   * @param {Object} [options] - 透传给 requestFullSync 的合并选项
   * @returns {Promise<{backendMessages: Array}|null>} 返回后端消息快照（合并前）；被去重跳过时返回 null
   */
  const guardedRequestFullSync = async (sessionId, options) => {
    if (fullSyncPending.has(sessionId)) {
      logger.debug(`[Sync] 全量同步进行中，跳过重复触发: session=${sessionId}`)
      return null
    }
    fullSyncPending.add(sessionId)
    try {
      return await requestFullSync(sessionId, options)
    } catch (err) {
      // 防御：全量同步失败仅记录日志并返回 null（调用方按"被跳过"处理），
      // 避免所有 requestFullSync 调用链产生 unhandled rejection；
      // 数据不一致由后续 replay/新事件触发全量同步补偿
      logger.error(
        `[Sync] 全量同步失败: session=${sessionId}, error=${err?.message || err}`
      )
      return null
    } finally {
      fullSyncPending.delete(sessionId)
    }
  }

  /**
   * 处理流式事件（WebSocket 通道，所有浏览器统一消费）
   *
   * 执行与连接解耦后 chat agent 由执行服务运行，流式事件（content/reasoning/
   * sources/suggestions/context）经 WebSocket 广播到所有浏览器；请求浏览器不再
   * 有 SSE 通道，故此处对所有浏览器一致处理，不再跳过。
   * @param {string} sessionId
   * @param {Object} payload - stream_event 事件载荷
   * @param {string} payload.messageId - 消息 ID
   * @param {string} payload.eventType - 事件类型（reasoning/sources/suggestions/context/content_update）
   * @param {Object} payload.data - 事件数据
   * @param {number} [payload.seq] - 序列号（幂等保护）
   */
  const handleStreamEvent = (sessionId, payload) => {
    const { messageId, eventType, data, seq } = payload
    if (!messageId || !eventType) {
      // stream_event 事件在非触发浏览器的 WebSocket 重放中可能无 messageId
      // （如 approval/interrupted 等由专用 handler 处理的事件），静默跳过。
      logger.debug(`[Sync] stream_event 缺少 messageId 或 eventType: messageId=${messageId}, eventType=${eventType}`)
      return
    }

    // 查找消息
    const session = getSession(sessionStore, sessionId)
    if (!session?.messages) return
    const message = findMessageById(session, messageId)
    if (!message) {
      logger.debug(`[Sync] stream_event 未找到消息(时序竞态): session=${sessionId}, message=${messageId}`)
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
        `session=${sessionId}, message=${messageId}, type=${eventType}`
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
    const field = fieldMap[eventType]
    if (!field) {
      // 通用 stream_event（deep_research / research_task_id / model_fallback）
      // 原 SSE 通道对应回调职责（setDeepResearchTask / setResearchTaskId /
      // setModelFallback）在执行与连接解耦后统一收敛到本处理器。
      _handleGenericStreamEvent(sessionId, message, eventType, data, seq)
      return
    }

    // stream_content_update 事件取 data.content，其他事件取 data
    const value = eventType === 'stream_content_update' ? data.content : data
    sessionStore.updateMessageFieldByBackendId(sessionId, messageId, field, value)

    if (seq) message._lastStreamEventSeq = seq
  }

  /** chatDeepResearch 桥接层模块加载缓存（惰性，避免 sync ↔ chat 业务模块静态循环依赖） */
  let chatDeepResearchModulePromise = null
  const _getChatDeepResearch = async () => {
    if (!chatDeepResearchModulePromise) {
      chatDeepResearchModulePromise = import('../chatDeepResearch')
    }
    const mod = await chatDeepResearchModulePromise
    return mod.useChatDeepResearchStore()
  }

  /** model store 模块加载缓存（惰性，避免模块加载期依赖） */
  let modelStoreModulePromise = null
  const _getModelStore = async () => {
    if (!modelStoreModulePromise) {
      modelStoreModulePromise = import('@/stores/model')
    }
    const mod = await modelStoreModulePromise
    return mod.useModelStore()
  }

  /**
   * 通用 stream_event 子类型处理（deep_research / research_task_id / model_fallback）
   *
   * 原 SSE 通道的 setDeepResearchTask / setResearchTaskId / setModelFallback 回调
   * 职责，执行与连接解耦后统一收敛到 WebSocket 通道（所有浏览器一致）：
   * - deep_research：研究任务创建 → 写入 chatDeepResearch 桥接层 + 消息 researchTaskId
   * - research_task_id：任务 ID 下发 → 同上
   * - model_fallback：模型降级提示 + 同步 modelStore 实际使用模型
   *
   * @param {string} sessionId
   * @param {Object} message - 目标消息（已定位）
   * @param {string} eventType - stream_event 子类型
   * @param {Object} data - 事件数据
   * @param {number} [seq]
   */
  const _handleGenericStreamEvent = (sessionId, message, eventType, data, seq) => {
    if (eventType === 'deep_research' || eventType === 'research_task_id') {
      const taskId = data?.taskId || data?.researchTaskId
        || (typeof data === 'string' ? data : null)
        || (data?.data?.taskId || null)
      if (taskId) {
        _getChatDeepResearch()
          .then((bridge) => {
            if (eventType === 'deep_research') {
              bridge.setChatDeepResearchTask(data)
            } else {
              bridge.setChatResearchTaskId(taskId)
            }
          })
          .catch((e) => logger.warn('[Sync] 更新 chatDeepResearch 桥接层失败:', e))
        if (!message.researchTaskId) {
          message.researchTaskId = taskId
        }
      }
    } else if (eventType === 'model_fallback' && data?.message) {
      ElNotification({
        title: '模型降级提示',
        message: data.message,
        type: 'warning',
        duration: 8000,
      })
      if (data.actualProvider && data.actualModel) {
        _getModelStore()
          .then((mStore) => {
            if (mStore.currentProviderId !== data.actualProvider || mStore.currentModelName !== data.actualModel) {
              mStore.currentProviderId = data.actualProvider
              mStore.currentModelName = data.actualModel
            }
          })
          .catch(() => {})
      }
    }
    if (seq) message._lastStreamEventSeq = seq
    logger.debug(`[Sync] stream_event 通用事件: eventType=${eventType}, seq=${seq}`)
  }

  /**
   * 应用 stream_reasoning 事件（session 频道，聊天模块深度研究模式的推理内容）
   *
   * 深度研究 worker（adapter.py）每次 LLM 节点产生 reasoning_content 时广播
   * STREAM_REASONING，payload 中携带该节点的完整推理文本。此处写入
   * ChatMessage.reasoning（duration=0 表示推理进行中，与代理模式 strategy.py
   * "duration: 0" 语义一致）；推理结束由 handleStreamCompleted 回写 duration。
   *
   * 代理模式（chat agent）深度思考：进行中事件 duration=0，完成事件由
   * strategy.on_loop_success（finalize_stream 阶段）携带实际思考秒数
   * （duration>0）发布，此处原样透传（根因修复：此前硬编码 duration:0
   * 覆盖完成态时长，AiReasoning 的 isStreaming 判定依赖 duration===0，
   * 时长恒为 0 → 完成后永远"正在思考"闪烁）。
   *
   * 与 task 频道（handleTaskEvent → researchStore.setTaskReasoning）双通道写入，
   * 分别驱动聊天消息 AiReasoning 与深度研究详情页 AiReasoning。
   *
   * 原实现位于 handleSessionEvent.js 内联 _applyStreamReasoning
   * （C5/cq-04 Task 2 下沉，行为保持）。
   *
   * @param {string} sessionId
   * @param {Object} payload - toCamelCase 后：{ source, sourceId, messageId, sessionId, taskId, data: { content, duration, source } }
   */
  const applyStreamReasoning = (sessionId, payload) => {
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
    // duration 透传：进行中事件 duration 缺失/0 → 0（"正在思考"）；
    // 完成事件（strategy.on_loop_success）携带实际思考秒数 → 原样透传。
    const duration = payload.data?.duration ?? payload.duration ?? 0
    targetMsg.reasoning = { content, duration }
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
   * 概念边界（区分"深度研究模式"与"深度思考功能"）：
   * - INTERRUPTED 仅表示"深度研究任务进行中"（模块功能状态），由深度研究卡片
   *   （ChatMessage `_isResearchRunning`）消费，与"模型是否在推理"无关。
   * - 不设置 isStreaming / 不标记思考状态：深度研究是后台异步任务，
   *   不等于模型思考（深度思考），不应触发"正在思考"动画或禁用输入。
   * - 模型推理过程（深度思考）由独立的 stream_reasoning 事件驱动 AiReasoning。
   *
   * 处理逻辑（所有浏览器统一）：
   * - 设置消息的 researchTaskId（绑定研究任务，供审批路由 / 研究卡片使用）
   * - 设置消息的 streamState=INTERRUPTED（避免 showContinueResearch 提前显示"继续研究"按钮）
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
      // 会话未加载：研究任务绑定由会话加载后的快照/后续事件补偿，无需标记思考状态
      logger.info(`[Sync] stream_interrupted 会话未加载: session=${sessionId}`)
      return
    }

    // payload 可能有两种结构（entry-point toCamelCase 已转换，均为 camelCase）：
    // 1. 嵌套：{ source, sourceId, sessionId, messageId, data: { taskId, ... } }
    // 2. 扁平：{ source, sourceId, sessionId, messageId, taskId, ... }
    const taskId = payload.taskId || payload.data?.researchTaskId || (payload.data && payload.data.taskId) || null
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
      return
    }

    // 设置 researchTaskId（绑定深度研究任务）
    if (taskId && !targetMsg.researchTaskId) {
      targetMsg.researchTaskId = taskId
      // 同步到 versions[currentVersion]
      const ver = getCurrentVersion(targetMsg)
      if (ver) ver.researchTaskId = taskId
    }

    // 同步 chatDeepResearch 桥接层的 researchTaskId（深度研究审批路由依赖：
    // 原 SSE 通道由 setDeepResearchTask/setResearchTaskId 回调写入，执行与连接
    // 解耦后 stream_interrupted 是聊天深度研究模式的权威信号，此处同步写入）。
    if (taskId) {
      _getChatDeepResearch()
        .then((bridge) => {
          if (!bridge.researchTaskId) {
            bridge.setChatResearchTaskId(taskId)
          }
        })
        .catch((e) => logger.warn('[Sync] stream_interrupted 更新 chatDeepResearch 桥接层失败:', e))
    }

    // 设置 streamState=INTERRUPTED（深度研究任务进行中，避免 showContinueResearch
    // 提前显示"继续研究"按钮）。仅在非终态时设置，避免覆盖已完成消息的状态。
    if (targetMsg.streamState !== StreamState.COMPLETED
        && targetMsg.streamState !== StreamState.ERROR) {
      targetMsg.streamState = StreamState.INTERRUPTED
      // 同步到 versions[currentVersion]
      const ver = getCurrentVersion(targetMsg)
      if (ver) ver.streamState = StreamState.INTERRUPTED
    }

    logger.info(
      `[Sync] stream_interrupted 消息进入 INTERRUPTED 状态: session=${sessionId}, ` +
      `message=${messageId || '(兜底)'}, task=${taskId || '(无)'}`
    )
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
   * 事件语义（两类 stream_completed 事件）：
   * - 聊天 SSE 完成事件（chat_executor_core / session_executor 广播）：
   *   不含 finalized 字段（后端落库先于广播，非请求浏览器可安全全量同步）。
   * - 深度研究权威完成事件（writeback.broadcast_stream_completed）：
   *   始终携带 task_id + finalized=true（深研回写专属标记），进入研究结果回写逻辑。
   *
   * @param {string} sessionId
   * @param {Object} payload
   */
  const handleStreamCompleted = (sessionId, payload) => {
    const session = getSession(sessionStore, sessionId)
    if (!session?.messages) return

    // 深度研究模式下，chat SSE 结束时发布的 stream_completed（无 finalized 字段）
    // 不应让前端误判研究完成。当消息处于 INTERRUPTED 状态时，
    // 忽略该事件（不更新消息状态，不显示"研究已完成"/"继续研究"按钮），仅由事件队列推进 seq。
    // 真正的研究完成事件由 Celery worker 通过 broadcast_stream_completed 发布
    //（finalized=true，携带 task_id/final_report），届时正常进入下方研究结果回写逻辑。
    // 普通聊天模式消息不会处于 INTERRUPTED 状态，不受此判断影响，行为与原有逻辑一致。
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
    // 与聊天 SSE 结束时的 stream_completed（无 task_id/finalized 字段）区分。
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
        guardedRequestFullSync(sessionId)
        return
      }

      if (targetMsg) {
        // 计算深度研究耗时并设置到 reasoning.duration
        // 触发浏览器: chat SSE 很快结束，message.timestamp 接近深度研究开始时间
        // 非触发浏览器: message_added 事件在 chat SSE 开始时发布，timestamp 也接近深度研究开始时间
        // 注意：mergeMessageFromBackend 的 reasoning 完成态保护（messageOperations.js）
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
        // 更新消息内容：优先主 agent 累计正文（payload.content，与工具 position
        // 基准一致），回退 finalReport。直接用 finalReport 覆盖会与 position
        // （基于 main_content 累计）错位，导致工具卡内联切段乱序。
        if (payload.success !== false) {
          if (payload.content) {
            targetMsg.content = payload.content
          } else if (payload.finalReport) {
            targetMsg.content = payload.finalReport
          }
        } else if (payload.error) {
          targetMsg.content = `深度研究执行失败：${payload.error}`
        }
        targetMsg.isStreaming = false
        targetMsg.streamState = StreamState.COMPLETED

        // 深度研究任务完成，清理所有工具审批状态
        // 防止研究结束后详情中仍残留审批状态
        // 统一清理 pending/processing/waiting 三种非终态审批为对应终态。
        // waiting 状态由批量审批场景下 _handleProcessed 设置（同批次还有 pending 时），
        // 若研究完成时仍有工具卡在 waiting，将永久显示"等待其他审批"。
        // 收敛：内联循环移入 messageIntegrity.finalizeToolCallsForResearchResult
        //（success 感知终态：审批→APPROVED/REJECTED，工具→COMPLETED/FAILED，行为与原内联一致）
        const { pendingApprovalCount, runningToolCount } =
          finalizeToolCallsForResearchResult(targetMsg, payload.success !== false)
        // 清理 pendingApprovals 中属于这个任务的审批
        let clearedApprovalCount = 0
        if (payload.taskId) {
          clearedApprovalCount = approvalStore.clearByTaskId(payload.taskId)
        }

        logger.info(`[Sync] 深度研究结果回写 - 审批状态清理: ` +
          `session=${sessionId}, task=${payload.taskId}, ` +
          `success=${payload.success !== false}, ` +
          `toolCalls总数=${targetMsg.toolCalls?.length || 0}, ` +
          `清理前pending审批数=${pendingApprovalCount}, ` +
          `清理前运行中工具数=${runningToolCount}, ` +
          `clearByTaskId清除数=${clearedApprovalCount}`
        )

        logger.info(`[Sync] 深度研究结果回写: session=${sessionId}, task=${payload.taskId}, success=${payload.success !== false}`)

        // 补写 researchTaskId（F3-B：非触发端 stream_completed 到达时确保 researchTaskId 存在）
        if (payload.taskId && !targetMsg.researchTaskId) {
          targetMsg.researchTaskId = payload.taskId
          const ver = getCurrentVersion(targetMsg)
          if (ver) ver.researchTaskId = payload.taskId
        }

        // 触发全量同步确保数据一致性（去重锁：同一会话已有进行中的同步时跳过）
        guardedRequestFullSync(sessionId)

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

    // Task 9：用户停止生成时执行服务广播 stream_completed(stopped=true)，
    // 非触发浏览器据此标记 stopped（保留已输出内容、允许固化为本轮主消息）
    if (payload.data?.stopped) {
      targetMsg.stopped = true
      logger.info(`[Sync] stream_completed 携带 stopped 标记: session=${sessionId}, message=${messageId || '(兜底)'}`)
    }
    // Task 9：重生成中断广播 incomplete，非触发浏览器同步标记当前版本禁止固化
    if (payload.data?.incomplete) {
      const activeVer = targetMsg.versions?.[targetMsg.currentVersion]
      if (activeVer) {
        activeVer.incomplete = true
        activeVer.streamState = StreamState.COMPLETED
        activeVer.isStreaming = false
      }
      logger.info(`[Sync] stream_completed 携带 incomplete 标记（重生成中断，禁止固化）: session=${sessionId}, message=${messageId || '(兜底)'}`)
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
    }

    const isRequestBrowser = streamingSessions.has(sessionId)

    if (isRequestBrowser) {
      // 仅在 onStreamEnd 已完成 PATCH（状态为 SYNCING）时才允许兜底 COMPLETED
      // FINALIZING 状态表示 PATCH 尚未完成，不应绕过
      if (targetMsg.streamState === StreamState.SYNCING) {
        targetMsg.streamState = StreamState.COMPLETED
        targetMsg.isStreaming = false
        logger.info(`[Sync] stream_completed 兜底 syncing→completed: session=${sessionId}`)

        // toolCalls 最终化统一收敛到 handleStreamFinalized（单一最终化路径）：
        // stream_completed 仅兜底消息状态，toolCalls 由 stream_finalized 幂等最终化
      } else if (targetMsg.streamState === StreamState.FINALIZING) {
        // FINALIZING 状态：PATCH 尚未完成，不兜底，等待 onStreamEnd 完成
        logger.info(`[Sync] stream_completed 收到时请求浏览器处于 finalizing，等待 PATCH 完成: session=${sessionId}`)
      } else if (targetMsg.streamState === StreamState.STREAMING
                 || targetMsg.streamState === StreamState.FINALIZING
                 || targetMsg.streamState === StreamState.SYNCING) {
        logger.info(`[Sync] stream_completed 收到时请求浏览器消息状态为 ${targetMsg.streamState}，等待本地流程: session=${sessionId}`)
      } else if (targetMsg.streamState === StreamState.COMPLETED) {
        // 消息已 COMPLETED：toolCalls 最终化由 handleStreamFinalized 兜底
        // （可能 WebSocket 事件乱序导致 toolCall 未更新，stream_finalized 幂等修正）
        logger.info(`[Sync] stream_completed 请求浏览器消息已 completed，等待 stream_finalized 最终化 toolCalls: session=${sessionId}`)
      } else {
        logger.info(`[Sync] stream_completed 收到时请求浏览器消息状态为 ${targetMsg.streamState}，不切换: session=${sessionId}`)
      }
      return
    }

    // 非请求浏览器：后端 executor 落库先于 stream_completed 广播，可安全标记 COMPLETED 并全量同步
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
      // toolCalls 最终化统一收敛到 handleStreamFinalized（单一最终化路径）：
      // 非请求浏览器最终化在 stream_finalized 同步完成后执行（含 wasCompleted 场景）
    }
    // 后端 executor 落库数据已可用（请求浏览器 PATCH 的权威内容由 stream_finalized 二次同步）
    // 非 FINALIZING/SYNCING 态均触发全量同步；同步完成后校验 tool_calls 数量和 content 长度
    if (targetMsg.streamState !== StreamState.FINALIZING
        && targetMsg.streamState !== StreamState.SYNCING) {
      const targetMessageId = messageId
      || targetMsg.backendId?.toString()
      || targetMsg.id?.toString()
      guardedRequestFullSync(sessionId).then((result) => {
        if (!result?.backendMessages) return
        verifyMessageIntegrity(sessionId, targetMessageId, result.backendMessages)
      })
    }

    logger.info(`[Sync] 流式完成: session=${sessionId}, message=${messageId || '(兜底)'}`)
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
      // 最终化 toolCalls（P3-22/P3-23 根因修复）：
      // handleStreamCompleted 可能因时序原因未调用 finalizeToolCalls（如 stream_finalized
      // 先于 stream_completed 到达，或 stream_completed 被保护态拦截）。
      // 此处幂等调用确保 toolCallsMap 中非终态 toolCall 被修正为 COMPLETED，
      // approval 非终态被修正为 TIMEOUT，避免审批按钮不消失。
      finalizeToolCalls(targetMsg, sessionId)
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
      await guardedRequestFullSync(sessionId, { allowContentMerge: true })
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
      // 非请求浏览器最终化 toolCalls（P3-22/P3-23 根因修复）：
      // 非请求浏览器依赖 WebSocket 事件同步工具状态，tool_call_completed/approval_approved
      // 事件可能丢失或乱序，导致 toolCallsMap 中 toolCall 卡在 pending/running/waiting，
      // approval 卡在 pending/processing/waiting。stream_finalized 表示流式已最终化，
      // 所有非终态工具调用应兜底为终态，避免 UI 永久显示"执行中"和审批按钮不消失。
      finalizeToolCalls(refreshedTarget, sessionId)
      logger.info(`[Sync] stream_finalized 非请求浏览器同步后标记 completed: session=${sessionId}, message=${messageId || '(兜底)'}`)
    }
  }

  return {
    handleStreamEvent,
    applyStreamReasoning,
    handleStreamInterrupted,
    handleStreamCompleted,
    handleStreamFinalized,
  }
}
