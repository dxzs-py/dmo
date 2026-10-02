import { useSnapshotSync, useSnapshotSyncByTask } from '@/composables/useSnapshotSync'
import { parseProtocolEvent } from '@/utils/sse.js'
import { logger } from '@/utils/logger'
import { SNAPSHOT_TRIGGER_EVENTS } from '@/types/realtimeEvents'

/**
 * @typedef {import('@/types/realtimeEvents').RealtimeEvent} RealtimeEvent
 */

/**
 * 事件路由分发子系统：WS onMessage 入口 + 双阶段事件分发
 *
 * 职责（原 createRealtimeSync 内联逻辑逐行迁移）：
 * - onMessage：WS 消息主入口（JSON 解析 / pong 短路 / 命名边界转换 / replay 分块回包 / 实时事件）
 * - dispatchEvent：双阶段处理（阶段一订阅者回调 → 推进 lastSeq；阶段二关键事件触发快照校对）
 * - getChannelKey：事件所属通道解析（user / session_<id> / task_<id>）
 *
 * 依赖约定：跨模块依赖一律通过 ctx 在调用时访问（禁止创建期解构）——
 * userCallbacks/sessionCallbacks/taskCallbacks 由 subscribers.js 注册（本工厂之后），
 * 调用时必然已就位；工厂之间零静态 import，避免循环依赖。
 *
 * @param {Object} ctx - 编排器共享上下文
 * @returns {{
 *   getChannelKey: (event: RealtimeEvent) => string | null,
 *   dispatchEvent: (event: RealtimeEvent, isReplay?: boolean) => Promise<void>,
 *   onMessage: (event: MessageEvent) => Promise<void>,
 * }}
 */
export function createDispatcher(ctx) {
  /**
   * 获取事件所属通道 key
   *
   * sessionId 解析优先级：
   *   1. payload.sessionId（message_added / approval_* 等事件显式携带）
   *   2. event.sessionId（顶层字段，由后端 _publish_to_session_async 注入）
   *
   * taskId 解析优先级：
   *   1. payload.taskId
   *   2. event.taskId（顶层字段，由后端 _publish_to_task_async 注入）
   *
   * task 通道兜底：当 payload 仅携带 task_id 时（独立深度研究场景），路由到 task 通道。
   *
   * @param {RealtimeEvent} event
   * @returns {string | null}
   */
  const getChannelKey = (event) => {
    if (['session_created', 'session_deleted', 'session_updated'].includes(event.type)) {
      return 'user'
    }
    // sessionId 为路由字段：优先 payload，其次 event 顶层（后端 _publish_to_session_async 注入）
    const sessionId = event.payload?.sessionId
      || event.sessionId
    if (sessionId) return `session_${sessionId}`
    // task_id 为路由字段：优先 payload，其次 event 顶层（后端 _publish_to_task_async 注入）
    const taskId = event.payload?.taskId || event.taskId
    if (taskId) return `task_${taskId}`
    return null
  }

  /**
   * 触发指定会话的快照校对（关键事件后调用）
   *
   * 双阶段事件处理的阶段二：
   *   阶段一：事件分发到订阅者（更新 sessionStore 本地状态）—— 由 sync.js 等订阅者完成
   *   阶段二：关键事件触发快照校对 —— 此处完成
   *
   * 失败时仅记录日志，不阻塞后续事件处理。
   *
   * @param {string} sessionId
   * @param {string} eventType
   */
  const _triggerSnapshotSync = (sessionId, eventType) => {
    if (!sessionId) return
    try {
      const { syncFromSnapshot } = useSnapshotSync(sessionId)
      // 异步触发，不阻塞事件分发；debounce 在 useSnapshotSync 内部处理
      syncFromSnapshot().catch(err => {
        logger.warn(`[Realtime] 快照校对失败: session=${sessionId}, event=${eventType}, error=${err?.message || err}`)
      })
    } catch (err) {
      logger.warn(`[Realtime] 触发快照校对异常: session=${sessionId}, event=${eventType}, error=${err?.message || err}`)
    }
  }

  /**
   * 触发深度研究任务的快照校对（v5 M19-d 新增）
   *
   * 用于深度研究模块跨浏览器同步时，按 taskId 拉取最新快照。
   * 失败时仅记录日志，不阻塞后续事件处理。
   *
   * @param {string} taskId
   * @param {string} eventType
   */
  const _triggerSnapshotSyncByTask = (taskId, eventType) => {
    if (!taskId) return
    try {
      const { syncFromSnapshot } = useSnapshotSyncByTask(taskId)
      syncFromSnapshot().catch(err => {
        logger.warn(`[Realtime] task 快照校对失败: task=${taskId}, event=${eventType}, error=${err?.message || err}`)
      })
    } catch (err) {
      logger.warn(`[Realtime] 触发 task 快照校对异常: task=${taskId}, event=${eventType}, error=${err?.message || err}`)
    }
  }

  /**
   * 分发事件到订阅者（双阶段处理）
   *
   * 阶段一：先执行订阅者回调，更新 sessionStore 本地状态；
   *         全部成功后才更新 lastSeq，任一回调失败则保持原 lastSeq，
   *         确保重连 replay 能补发失败事件。
   * 阶段二：关键事件（stream_finalized / tool_call_completed / approval_approved）
   *         分发完成后触发快照校对，修复可能丢失或乱序的中间事件。
   *
   * Replay barrier：
   *   - 实时事件（isReplay=false）在 channel pending 期间被缓冲，不立即处理
   *   - replay 事件（isReplay=true）直接处理，并更新 lastSeq
   *   - replay 完成后由 _flushBufferedEvents 统一 flush 缓冲事件
   *
   * @param {RealtimeEvent} event
   * @param {boolean} [isReplay=false] - 是否为 replay 事件（bypass barrier）
   */
  const dispatchEvent = async (event, isReplay = false) => {
    const channelKey = getChannelKey(event)

    // 注入 isReplay 标记，让回调（如 sync.js 的 applyUserEvent）能区分：
    //   - 实时事件（isReplay=false）：来自后端实时推送，需要响应副作用
    //   - replay 事件（isReplay=true）：来自历史回放，仅用于状态重建，不应触发
    //     自动订阅、自动切换会话等副作用（否则 N 条历史 session_created 会触发
    //     N 次 subscribeSession + N 次快照请求，造成请求风暴）
    // 标记挂在 event 上而非第二参数，避免修改所有回调签名
    // Task 7.1：_isReplay → isReplay（内部标记，不参与网络传输）
    if (isReplay) {
      event.isReplay = true
    }

    // Replay barrier：实时事件在 channel pending 期间缓冲
    if (!isReplay && channelKey && ctx.replayPendingChannels.has(channelKey)) {
      if (!ctx.bufferedEvents.has(channelKey)) {
        ctx.bufferedEvents.set(channelKey, [])
      }
      ctx.bufferedEvents.get(channelKey).push(event)
      logger.debug(`[Realtime] replay barrier 缓冲事件: ${channelKey}, type=${event.type}, seq=${event.seq}`)
      return
    }

    let hasError = false

    try {
      // user 频道事件统一分发到 userCallbacks：
      // session_created/deleted/updated（会话级用户通知）与 task_created /
      // task_status_changed / task_deleted（任务创建/终态/删除用户通知，
      // 深度研究模块据此自动刷新任务列表，P8 根因修复）。这些事件后端仅
      // 发布到 user 频道（无 session_id/task_id 顶层路由字段），若不在此分发，
      // subscribeUserEvents 订阅者永远收不到。
      if (['session_created', 'session_deleted', 'session_updated', 'task_created', 'task_status_changed', 'task_deleted'].includes(event.type)) {
        for (const cb of ctx.userCallbacks) {
          try {
            await cb(event)
          } catch (error) {
            hasError = true
            logger.error('[Realtime] user 事件回调异常:', error)
          }
        }
      } else {
        // sessionId 为路由字段：优先 payload，其次 event 顶层（后端 _publish_to_session_async 注入）
        const sessionId = event.payload?.sessionId
          || event.sessionId
        if (sessionId) {
          const callbacks = ctx.sessionCallbacks.get(sessionId)
          if (callbacks) {
            for (const cb of callbacks) {
              try {
                await cb(event)
              } catch (error) {
                hasError = true
                logger.error('[Realtime] session 事件回调异常:', error)
              }
            }
          }
        }

        // task 通道分发：事件可能同时携带 session_id 和 task_id，两个通道都应收到
        // task_id 为路由字段：优先 payload，其次 event 顶层（后端 _publish_to_task_async 注入）
        const taskId = event.payload?.taskId || event.taskId
        if (taskId) {
          const callbacks = ctx.taskCallbacks.get(taskId)
          if (callbacks) {
            for (const cb of callbacks) {
              try {
                await cb(event)
              } catch (error) {
                hasError = true
                logger.error('[Realtime] task 事件回调异常:', error)
              }
            }
          }
        }
      }

      // 回调全部成功后才更新 lastSeq，失败时保持原 lastSeq
      // 仅对携带有效 seq 的事件更新（合成事件无 seq 时不污染 lastSeq）
      // 统一走 advanceLastSeq（取 max 防回退 + debounce 持久化，单一权威唯一写入入口）
      if (!hasError && channelKey && typeof event.seq === 'number') {
        ctx.advanceLastSeq(channelKey, event.seq)
      }
    } catch (err) {
      logger.error('[RealtimeSync] 事件回调失败，保持 lastSeq 不变:', err)
      // 不更新 lastSeq，重连后 replay 会重新发送该事件
      // hasError 已在 try 块内的 catch 分支设置，此处无需重复赋值
    }

    // 阶段二：关键事件触发快照校对（无论回调是否成功，只要事件类型匹配就触发）
    // 失败时仅记录日志，不阻塞后续事件处理
    if (SNAPSHOT_TRIGGER_EVENTS.has(event.type)) {
      const sessionId = event.payload?.sessionId || event.sessionId
      if (sessionId) {
        _triggerSnapshotSync(sessionId, event.type)
      }
      // M19-d：task 频道关键事件触发 task 快照校对（深度研究模块跨浏览器同步）
      // task_id 优先 payload，其次 event 顶层（后端 _publish_to_task_async 注入）
      const taskId = event.payload?.taskId || event.taskId
      if (taskId) {
        _triggerSnapshotSyncByTask(taskId, event.type)
      }
    }
  }

  /**
   * onMessage：接收 WebSocket 消息的主入口
   *
   * 命名边界：对 rawData 整体调用 toCamelCase 递归转换（snake_case → camelCase），
   * 然后将 type 还原为后端原始 snake_case（协议路由标识符，非业务数据）。
   * 其余字段均为前端 camelCase。
   *
   * replay 事件使用 async + for...of + await 处理，确保所有 replay 事件处理完成后再 flush。
   * 避免 _flushBufferedEvents 在 replay 事件尚未完成时就被调用，
   * 导致 replay barrier 被提前清除，实时事件在 replay 事件处理完成前进入。
   *
   * @param {MessageEvent} event
   */
  const onMessage = async (event) => {
    let rawData
    try {
      rawData = JSON.parse(event.data)
    } catch {
      logger.warn('[Realtime] 收到非 JSON 消息:', event.data)
      return
    }

    // 处理服务端心跳/ pong（不需要转换）
    if (rawData.type === 'pong' || rawData.action === 'pong') {
      logger.debug('[Realtime] 收到 pong')
      return
    }

    // === 命名边界：snake_case → camelCase ===
    // parseProtocolEvent 统一入口（utils/sse.js）：整体 toCamelCase 递归转换后，
    // 还原 type 与顶层 subagent_thread_id 为原始 snake_case（协议路由标识符，
    // 如 "session_created"、"tool_result" 非业务数据；subagent_thread_id 为
    // 子代理 SSE 定向推送路由字段，不参与转换）
    const data = parseProtocolEvent(rawData)

    // 处理服务端历史事件回放回包（支持分块）
    if (data.type === 'replay' && Array.isArray(data.events)) {
      const channelKey = ctx._channelKeyFromParts(data.channelType, data.channelId)
      const chunkIdx = typeof data.chunkIndex === 'number' ? data.chunkIndex : 0
      const chunkCount = typeof data.chunkCount === 'number' ? data.chunkCount : 1
      logger.info(
        `[Realtime] 收到 replay 回包: channel=${data.channelType}:${data.channelId}, `
        + `count=${data.count}, chunk=${chunkIdx + 1}/${chunkCount}`
      )
      // replay 事件 bypass barrier，直接处理（isReplay=true）
      // 每个 evt 已在 data.events 的 toCamelCase 中转换，但需还原其 type 与
      // subagent_thread_id（协议路由标识符，snake_case，按原始 events 索引还原）
      const rawEvents = Array.isArray(rawData.events) ? rawData.events : []
      for (let i = 0; i < data.events.length; i++) {
        const evt = data.events[i]
        if (rawEvents[i]?.subagent_thread_id) {
          evt.subagent_thread_id = rawEvents[i].subagent_thread_id
        }
        if (evt.type && typeof evt.seq === 'number') {
          await ctx.dispatchEvent(evt, true)
        }
      }
      // 最后一块到达后 flush 缓冲的实时事件
      if (chunkIdx + 1 >= chunkCount) {
        await ctx._flushBufferedEvents(channelKey)
      }
      return
    }

    if (data.type && typeof data.seq === 'number') {
      // 与 replay 分支对齐：await dispatchEvent。
      // 虽然浏览器不会 await onMessage 的 Promise，无法串行化多个 onMessage 调用，
      // 但 await 可确保当前 onMessage 内部 dispatchEvent 完整执行（含 lastSeq 更新），
      // 避免 unhandled rejection，与 replay 分支行为一致。
      await ctx.dispatchEvent(data)
    }
  }

  return {
    getChannelKey,
    dispatchEvent,
    onMessage,
  }
}
