import { logger } from '@/utils/logger'
import {
  MAX_SUBSCRIBED_SESSIONS,
  MAX_SUBSCRIBED_TASKS,
} from './constants.js'

/**
 * 惰性加载 sync store 模块（消除模块加载期循环依赖，SubTask 11.1）
 *
 * 背景：useRealtimeSync ↔ sync.js 存在模块加载期双向依赖：
 *   - 本文件原为单文件 useRealtimeSync.js，曾静态 import sync.js（useSyncStore）
 *   - sync.js 顶层静态 import 本 composable（useRealtimeSync，store setup 内调用）
 * 本模块改为函数内动态 import 后，静态依赖图中仅剩 sync.js → useRealtimeSync
 * 单向依赖，循环依赖在模块加载期消除。
 *
 * 使用点 subscribeSession 为同步函数，无法 await，故采用 fire-and-forget 的
 * .then 调用；动态 import 在 microtask 中 resolve，必然早于 WebSocket 消息的
 * 网络往返，因此 resetSessionSeq 先于 subscribe 请求生效，与原同步调用等价。
 * Promise 缓存保证模块只动态加载一次（模块级缓存，单例语义下的既有设计，非新增全局态）。
 */
/** @type {Promise<{ useSyncStore: Function }> | null} */
let syncStoreModulePromise = null
/**
 * @returns {Promise<{ useSyncStore: Function }>}
 */
const _getSyncStoreModule = () => {
  if (!syncStoreModulePromise) {
    syncStoreModulePromise = import('@/stores/sync')
  }
  return syncStoreModulePromise
}

/**
 * 消费方订阅注册子系统：user/session/task 三通道订阅与退订
 *
 * 职责（原 createRealtimeSync 内联逻辑逐行迁移）：
 * - 维护 userCallbacks / sessionCallbacks / taskCallbacks 三组回调注册表
 * - 维护 subscribedSessions / subscribedTasks 已订阅集合（重连恢复的依据）
 * - 首次订阅才发送 subscribe 请求 + 设置 replay barrier（防重复 replay 请求风暴）
 * - LRU 淘汰：超过订阅上限时移除最旧订阅
 *
 * 依赖约定：跨模块依赖一律通过 ctx 在调用时访问（禁止创建期解构）——
 * ctx.send / ctx._setReplayPending / ctx._clearReplayPending 由 connection.js /
 * replay.js 注册；工厂之间零静态 import，避免循环依赖。
 *
 * @param {Object} ctx - 编排器共享上下文
 * @returns {{
 *   userCallbacks: Set<import('@/types/realtimeEvents').RealtimeEventCallback>,
 *   sessionCallbacks: Map<string, Set<import('@/types/realtimeEvents').RealtimeEventCallback>>,
 *   subscribedSessions: Set<string>,
 *   taskCallbacks: Map<string, Set<import('@/types/realtimeEvents').RealtimeEventCallback>>,
 *   subscribedTasks: Set<string>,
 *   subscribeUserEvents: (callback: Function) => () => void,
 *   subscribeSession: (sessionId: string, callback: Function, options?: Object) => () => void,
 *   unsubscribeSession: (sessionId: string) => void,
 *   subscribeTask: (taskId: string, callback: Function, options?: Object) => () => void,
 *   unsubscribeTask: (taskId: string) => void,
 * }}
 */
export function createSubscribers(ctx) {
  /** @type {Set<import('@/types/realtimeEvents').RealtimeEventCallback>} */
  const userCallbacks = new Set()
  /** @type {Map<string, Set<import('@/types/realtimeEvents').RealtimeEventCallback>>} */
  const sessionCallbacks = new Map()
  /** @type {Set<string>} */
  const subscribedSessions = new Set()
  // task 通道订阅：taskId → Set<callback>
  /** @type {Map<string, Set<import('@/types/realtimeEvents').RealtimeEventCallback>>} */
  const taskCallbacks = new Map()
  // 已订阅的 taskId 集合
  /** @type {Set<string>} */
  const subscribedTasks = new Set()

  /**
   * 订阅 user 通道事件
   * @param {import('@/types/realtimeEvents').RealtimeEventCallback} callback
   * @returns {() => void}
   */
  const subscribeUserEvents = (callback) => {
    userCallbacks.add(callback)
    return () => {
      userCallbacks.delete(callback)
    }
  }

  /**
   * 订阅指定 session 事件
   *
   * 统一底层设计：
   * 始终从 lastSeq Map 解析 last_seq 并发送给后端，与 onConnectionOpen 重连路径
   * 行为对齐。三条路径统一走同一逻辑：
   *   1. 首次加载（页面刷新后挂载）→ 从 lastSeq Map 取 seq（无记录则 0）→ 触发 replay
   *   2. 重连（onConnectionOpen）→ 已是此逻辑，无变化
   *   3. 新会话创建（session_created 事件 replayFromSeq=0）→ options 覆盖，无变化
   *
   * chat / deep_research / learning 三模块零侵入受益，符合"实时同步是统一通用功能"原则。
   *
   * @param {string} sessionId
   * @param {import('@/types/realtimeEvents').RealtimeEventCallback} callback
   * @param {Object} [options]
   * @param {number} [options.replayFromSeq] - 显式覆盖 last_seq（如新会话创建传 0）
   * @returns {() => void}
   */
  const subscribeSession = (sessionId, callback, options = {}) => {
    if (!sessionId) return () => {}

    if (!sessionCallbacks.has(sessionId)) {
      sessionCallbacks.set(sessionId, new Set())
    }
    const callbacks = sessionCallbacks.get(sessionId)
    // 首次订阅判断：callbacks 为空表示该 session 尚无订阅者。
    // 仅首次订阅才发送 subscribe 请求 + 设置 replay barrier，避免重复订阅触发后端多次 replay
    // （后端 group_add 去重但 replay 未去重，多个 replay 流并发交错会导致 barrier 状态混乱、
    // 部分实时事件被缓冲后丢失）。
    // 注意：重连时 onConnectionOpen 独立遍历 subscribedSessions 发送 subscribe，不经过此函数。
    const isFirstSubscription = callbacks.size === 0
    callbacks.add(callback)
    subscribedSessions.add(sessionId)

    // LRU 淘汰：超过订阅上限时移除最旧的 session 订阅
    // 防止重连时 N 个已订阅 session 触发 N 次 subscribe + replay 请求风暴
    if (isFirstSubscription && subscribedSessions.size > MAX_SUBSCRIBED_SESSIONS) {
      const oldest = subscribedSessions.values().next().value
      if (oldest && oldest !== sessionId) {
        logger.warn(
          `[Realtime] 订阅数超过上限 ${MAX_SUBSCRIBED_SESSIONS}，按 LRU 淘汰最旧 session: ${oldest}`
        )
        unsubscribeSession(oldest)
      }
    }

    if (isFirstSubscription && ctx.ws?.readyState === WebSocket.OPEN) {
      const key = `session_${sessionId}`
      // 始终从 lastSeq Map 解析 last_seq，与 onConnectionOpen 行为对齐
      // options.replayFromSeq 优先（新会话创建场景显式传 0），否则用持久化的 lastSeq
      const storedSeq = ctx.lastSeq.has(key) ? ctx.lastSeq.get(key) : 0
      const seq = options.replayFromSeq !== undefined ? options.replayFromSeq : storedSeq

      // replayFromSeq=0 表示全量回放（新会话创建 / 任务切换等场景）。
      // syncStore 的 lastSeenSeq 和 _sessionEventQueue.expectedSeq 不会因 replayFromSeq=0 自动重置，
      // 导致回放的低 seq 事件被 applySessionEvent 的去重逻辑跳过（event.seq <= prevSeq），
      // 或被 _processSessionEventOrdered 当作"过期事件"丢弃（event.seq < expectedSeq）。
      // 必须在发送 subscribe 请求之前重置，确保回放事件到达时去重基线已清零。
      // 注意：仅重置该 session 的状态，不影响其他会话；不清理 streamingSessions。
      if (options.replayFromSeq === 0) {
        // 动态 import（microtask resolve）早于 WebSocket 消息网络往返执行，
        // 时序与原同步调用等价（reset 先于 subscribe 请求生效）
        _getSyncStoreModule()
          .then(({ useSyncStore: getSyncStore }) => {
            try {
              getSyncStore().resetSessionSeq(sessionId)
            } catch (err) {
              logger.warn(`[Realtime] resetSessionSeq 失败: session=${sessionId}`, err)
            }
          })
          .catch((err) => {
            logger.warn(`[Realtime] resetSessionSeq 加载失败: session=${sessionId}`, err)
          })
      }

      // Replay barrier：replay 期间缓冲实时事件，避免污染 lastSeq
      ctx._setReplayPending(key)
      ctx.send({ action: 'subscribe_session', payload: { sessionId, lastSeq: seq } })
    }

    return () => {
      callbacks.delete(callback)
      if (callbacks.size === 0) {
        unsubscribeSession(sessionId)
      }
    }
  }

  /**
   * 取消订阅指定 session
   * @param {string} sessionId
   */
  const unsubscribeSession = (sessionId) => {
    if (!sessionId) return
    sessionCallbacks.delete(sessionId)
    subscribedSessions.delete(sessionId)
    // 清理 replay barrier 状态，避免缓冲事件泄漏
    ctx._clearReplayPending(`session_${sessionId}`)

    if (ctx.ws?.readyState === WebSocket.OPEN) {
      ctx.send({ action: 'unsubscribe_session', payload: { sessionId } })
    }
  }

  /**
   * 订阅指定 task 事件
   *
   * 统一底层设计：
   * 与 subscribeSession 对称，始终从 lastSeq Map 解析 last_seq 并发送给后端，
   * 与 onConnectionOpen 重连路径行为对齐。
   *
   * @param {string} taskId
   * @param {import('@/types/realtimeEvents').RealtimeEventCallback} callback
   * @param {Object} [options]
   * @param {number} [options.replayFromSeq] - 显式覆盖 last_seq
   * @returns {() => void}
   */
  const subscribeTask = (taskId, callback, options = {}) => {
    if (!taskId) return () => {}

    if (!taskCallbacks.has(taskId)) {
      taskCallbacks.set(taskId, new Set())
    }
    const callbacks = taskCallbacks.get(taskId)
    // 首次订阅判断：与 subscribeSession 对齐，仅首次订阅才发送 subscribe 请求 + 设置 barrier，
    // 避免重复订阅触发后端多次 replay，导致 barrier 状态混乱和事件丢失。
    // 注意：重连时 onConnectionOpen 独立遍历 subscribedTasks 发送 subscribe，不经过此函数。
    const isFirstSubscription = callbacks.size === 0
    callbacks.add(callback)
    subscribedTasks.add(taskId)

    // LRU 淘汰：与 subscribeSession 对称，超过上限时移除最旧 task 订阅
    if (isFirstSubscription && subscribedTasks.size > MAX_SUBSCRIBED_TASKS) {
      const oldest = subscribedTasks.values().next().value
      if (oldest && oldest !== taskId) {
        logger.warn(
          `[Realtime] 订阅数超过上限 ${MAX_SUBSCRIBED_TASKS}，按 LRU 淘汰最旧 task: ${oldest}`
        )
        unsubscribeTask(oldest)
      }
    }

    if (isFirstSubscription && ctx.ws?.readyState === WebSocket.OPEN) {
      const key = `task_${taskId}`
      // 始终从 lastSeq Map 解析 last_seq，与 onConnectionOpen 行为对齐
      const storedSeq = ctx.lastSeq.has(key) ? ctx.lastSeq.get(key) : 0
      const seq = options.replayFromSeq !== undefined ? options.replayFromSeq : storedSeq
      // Replay barrier：replay 期间缓冲实时事件，避免污染 lastSeq
      ctx._setReplayPending(key)
      ctx.send({ action: 'subscribe_task', payload: { taskId, lastSeq: seq } })
    }

    // 返回取消函数（与 subscribeSession 一致）
    return () => {
      callbacks.delete(callback)
      if (callbacks.size === 0) {
        unsubscribeTask(taskId)
      }
    }
  }

  /**
   * 取消订阅指定 task
   * @param {string} taskId
   */
  const unsubscribeTask = (taskId) => {
    if (!taskId) return
    taskCallbacks.delete(taskId)
    subscribedTasks.delete(taskId)
    // 清理 replay barrier 状态，避免缓冲事件泄漏
    ctx._clearReplayPending(`task_${taskId}`)

    if (ctx.ws?.readyState === WebSocket.OPEN) {
      ctx.send({ action: 'unsubscribe_task', payload: { taskId } })
    }
  }

  return {
    userCallbacks,
    sessionCallbacks,
    subscribedSessions,
    taskCallbacks,
    subscribedTasks,
    subscribeUserEvents,
    subscribeSession,
    unsubscribeSession,
    subscribeTask,
    unsubscribeTask,
  }
}
