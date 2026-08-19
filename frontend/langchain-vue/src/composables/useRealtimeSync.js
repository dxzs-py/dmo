import { ref, watch } from 'vue'
import { useDebounceFn } from '@vueuse/core'
import { useUserStore } from '@/stores/user'
import settings from '@/config/settings'
import { logger } from '@/utils/logger'
import { useSnapshotSync, useSnapshotSyncByTask } from '@/composables/useSnapshotSync'
import { toSnakeCase } from '@/utils/sessionTransformers.js'
import { parseProtocolEvent } from '@/utils/sse.js'
import {
  SNAPSHOT_TRIGGER_EVENTS,
  RealtimeConnectionStatus,
} from '@/types/realtimeEvents'

/**
 * 惰性加载 sync store 模块（消除模块加载期循环依赖，SubTask 11.1）
 *
 * 背景：useRealtimeSync.js ↔ sync.js 存在模块加载期双向依赖：
 *   - 本文件原 L6 静态 import sync.js（useSyncStore）
 *   - sync.js 顶层静态 import 本文件（useRealtimeSync，store setup 内调用）
 * 本文件改为函数内动态 import 后，静态依赖图中仅剩 sync.js → useRealtimeSync.js
 * 单向依赖，循环依赖在模块加载期消除。
 *
 * 使用点 subscribeSession 为同步函数，无法 await，故采用 fire-and-forget 的
 * .then 调用；动态 import 在 microtask 中 resolve，必然早于 WebSocket 消息的
 * 网络往返，因此 resetSessionSeq 先于 subscribe 请求生效，与原同步调用等价。
 * Promise 缓存保证模块只动态加载一次。
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
 * @typedef {import('@/types/realtimeEvents').RealtimeEvent} RealtimeEvent
 */

/**
 * @typedef {import('@/types/realtimeEvents').RealtimeEventCallback} RealtimeEventCallback
 */

const WS_PATH = '/ws/realtime/'
const PING_INTERVAL_MS = 15000
// 指数退避重连参数：1s → 2s → 4s → 8s → 10s（max 5 次）
const RECONNECT_BASE_MS = 1000
const RECONNECT_MAX_MS = 10000
const RECONNECT_MAX_ATTEMPTS = 5
const LAST_SEQ_KEY_PREFIX = 'realtime:last_seq'
// disconnected 状态显示防抖（ms）：网络抖动时延迟显示，避免频繁闪烁
const DISCONNECT_DEBOUNCE_MS = 500
// 订阅上限：防止重连时 N 个已订阅 session 触发 N 次 subscribe + replay 请求风暴
// 超过上限时按 LRU 淘汰最旧的订阅（用户极少同时关注超过 10 个会话）
const MAX_SUBSCRIBED_SESSIONS = 10
const MAX_SUBSCRIBED_TASKS = 10

/** @type {ReturnType<typeof createRealtimeSync> | null} */
let instance = null

/**
 * 根据 API 基础地址构造 WebSocket URL
 * @param {string} token
 * @returns {string}
 */
function buildRealtimeUrl(token) {
  const apiUrl = settings.apiBaseUrl || ''
  let wsOrigin

  if (apiUrl.startsWith('http://') || apiUrl.startsWith('https://')) {
    const url = new URL(apiUrl)
    wsOrigin = `${url.protocol === 'https:' ? 'wss:' : 'ws:'}//${url.host}`
  } else {
    wsOrigin = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`
  }

  return `${wsOrigin}${WS_PATH}?token=${encodeURIComponent(token)}`
}

/**
 * 解析 JWT access token 的过期时间戳（秒）
 * WebSocket 连接前用于预判过期，避免用过期 token 连接被拒后重连风暴。
 * 与 axios.js 中的 getJwtExp 保持一致逻辑。
 * @param {string} token
 * @returns {number} exp 时间戳（秒），0 表示无法解析
 */
function _getJwtExp(token) {
  if (!token || typeof token !== 'string') return 0
  const parts = token.split('.')
  if (parts.length !== 3) return 0
  try {
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const padded = b64 + '='.repeat((4 - b64.length % 4) % 4)
    const json = decodeURIComponent(
      atob(padded)
        .split('')
        .map(c => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
        .join('')
    )
    const payload = JSON.parse(json)
    return typeof payload.exp === 'number' ? payload.exp : 0
  } catch {
    return 0
  }
}

function createRealtimeSync() {
  const userStore = useUserStore()

  /** @type {WebSocket | null} */
  let ws = null
  /** @type {number | null} */
  let reconnectTimer = null
  /** @type {number | null} */
  let pingTimer = null
  // disconnected 显示防抖定时器
  /** @type {number | null} */
  let disconnectDebounceTimer = null
  let reconnectAttempt = 0
  let intentionalClose = false
  // 标记是否已达到最大重连次数（用于 UI 显示"连接断开"提示）
  let exhaustedReconnect = false

  /** @type {import('vue').Ref<RealtimeConnectionStatus>} */
  const connectionStatus = ref(RealtimeConnectionStatus.DISCONNECTED)

  /**
   * 当前活跃的 SSE 流式会话计数。
   * 当 streamingActiveCount > 0 且 WebSocket 断开时，不显示"已断开"，
   * 因为请求浏览器仍可通过 SSE 获取数据。
   * 由 sync.js 的 startStreaming/stopStreaming 调用 increment/decrement 维护。
   */
  const streamingActiveCount = ref(0)

  /**
   * 标记一个 SSE 流开始（由 sync.js startStreaming 调用）
   */
  const incrementStreaming = () => {
    streamingActiveCount.value++
  }

  /**
   * 标记一个 SSE 流结束（由 sync.js stopStreaming 调用）
   */
  const decrementStreaming = () => {
    if (streamingActiveCount.value > 0) {
      streamingActiveCount.value--
    }
  }

  /**
   * 每个通道最近收到的 seq：key 为 'user' 或 'session_<id>' 或 'task_<id>'
   *
   * Task 4：本 Map 是**事件级去重的唯一权威**（channel + seq 单调）：
   * - subscribe/replay 请求的 last_seq 起点由本 Map 决定（已处理事件不会被重复回放）
   * - dispatchEvent 在回调全部成功后推进本 Map（取 max 防并发回退）
   * - sync.js 的 advanceBaseline（有序队列丢弃联动）也会推进本 Map，保持基线收敛
   * 其他 seq 跟踪器（seqDedup 仅做跳号检测、orderedQueue 仅排序/间隙等待）均不得
   * 独立承担"事件已见"去重职责。
   *
   * @type {Map<string, number>}
   */
  const lastSeq = new Map()

  /**
   * 获取当前用户的 lastSeq localStorage key
   * @returns {string | null}
   */
  const getLastSeqKey = () => {
    const userId = userStore.userInfo?.id
    return userId ? `${LAST_SEQ_KEY_PREFIX}:${userId}` : null
  }

  /**
   * 从 localStorage 恢复 lastSeq
   */
  const loadLastSeq = () => {
    const key = getLastSeqKey()
    if (!key) return
    try {
      const raw = localStorage.getItem(key)
      if (!raw) return
      const entries = JSON.parse(raw)
      if (Array.isArray(entries)) {
        entries.forEach(([k, v]) => {
          if (typeof v === 'number') {
            lastSeq.set(k, v)
          }
        })
        logger.info('[Realtime] 已从 localStorage 恢复 lastSeq:', entries.length, '条通道')
      }
    } catch (error) {
      logger.error('[Realtime] 解析 lastSeq 失败:', error)
    }
  }

  /**
   * 将 lastSeq 持久化到 localStorage
   *
   * Task 4 跨标签页防互踩：写入前读取旧值，逐 channel 取最大值后合并写回。
   * 多标签页（同一用户）各自维护 lastSeq Map，直接覆盖会令较新标签页的
   * 已处理 seq 基线被较旧标签页覆盖（陈旧 lastSeq 使重连 replay 重复投递已处理事件）。
   * 取 max 合并保证各标签页写入单调不减。
   */
  const persistLastSeq = () => {
    const key = getLastSeqKey()
    if (!key) return
    try {
      // 读取旧值，构建合并基线（旧值损坏时忽略，用当前值覆盖）
      /** @type {Map<string, number>} */
      const merged = new Map()
      const raw = localStorage.getItem(key)
      if (raw) {
        try {
          const entries = JSON.parse(raw)
          if (Array.isArray(entries)) {
            entries.forEach(([k, v]) => {
              if (typeof v === 'number') merged.set(k, v)
            })
          }
        } catch {
          logger.warn('[Realtime] 解析 localStorage lastSeq 旧值失败，以当前值覆盖')
        }
      }
      // 当前内存值取 max 合并
      for (const [k, v] of lastSeq.entries()) {
        const prev = merged.get(k) || 0
        if (v > prev) merged.set(k, v)
      }
      localStorage.setItem(key, JSON.stringify(Array.from(merged.entries())))
    } catch (error) {
      logger.error('[Realtime] 保存 lastSeq 失败:', error)
    }
  }

  // rd-08：防抖统一 @vueuse useDebounceFn（trailing 语义与原内联 debounce 等价）
  const saveLastSeqDebounced = useDebounceFn(persistLastSeq, 200)

  /**
   * 推进指定通道的 lastSeq（事件级去重单一权威的唯一写入入口）
   *
   * Task 4：
   * - dispatchEvent（回调全部成功后）与 sync.js 的 advanceBaseline（有序队列丢弃联动）
   *   均通过本方法推进 lastSeq，保证多路径基线收敛，避免双基线发散。
   * - 取 max 防回退：多个 dispatchEvent 并发时（浏览器不 await onMessage 的 Promise），
   *   回调完成顺序可能与事件到达顺序不一致，直接 set 会导致 lastSeq 回退，
   *   重连时 replay 从更早 seq 开始，重复处理已处理事件。
   *
   * @param {string} channelKey - 'user' / 'session_<id>' / 'task_<id>'
   * @param {number} seq
   */
  const advanceLastSeq = (channelKey, seq) => {
    if (!channelKey || typeof seq !== 'number') return
    const current = lastSeq.get(channelKey) || 0
    if (seq > current) {
      lastSeq.set(channelKey, seq)
      saveLastSeqDebounced()
    }
  }

  /**
   * 清除当前用户的 lastSeq（登出或切换用户时）
   */
  const clearLastSeq = () => {
    const key = getLastSeqKey()
    if (key) {
      localStorage.removeItem(key)
    }
    lastSeq.clear()
  }

  // 初始化时立即恢复
  loadLastSeq()

  /** @type {Set<RealtimeEventCallback>} */
  const userCallbacks = new Set()
  /** @type {Map<string, Set<RealtimeEventCallback>>} */
  const sessionCallbacks = new Map()
  /** @type {Set<string>} */
  const subscribedSessions = new Set()
  // task 通道订阅：taskId → Set<callback>
  /** @type {Map<string, Set<RealtimeEventCallback>>} */
  const taskCallbacks = new Map()
  // 已订阅的 taskId 集合
  /** @type {Set<string>} */
  const subscribedTasks = new Set()

  // ==================== Replay Barrier（统一底层设计） ====================
  // Replay Barrier 设计原因：
  //   1) 后端 replay 已分块发送，但前端在 replay 期间仍可能收到实时事件
  //   2) 实时事件 seq 可能小于 replay 中最大 seq（replay 历史包含更早事件）
  //   3) 若实时事件先于 replay 被处理，会污染 lastSeq，导致 replay 部分事件被 dedup 跳过
  // 解决方案：replay 期间缓冲实时事件，replay 完成后按 seq 顺序 flush
  /** @type {Set<string>} */ // 等待 replay 完成的 channelKey 集合
  const replayPendingChannels = new Set()
  /** @type {Map<string, RealtimeEvent[]>} */ // 缓冲的实时事件，按 channelKey 分组
  const bufferedEvents = new Map()
  /** @type {Map<string, number>} */ // safety timeout timer，防止 replay 永不到达
  const replayBarrierTimers = new Map()
  // Replay barrier 安全超时：10s 内 replay 未到达则强制 flush
  const REPLAY_BARRIER_TIMEOUT_MS = 10000

  /**
   * 发送原始 JSON 消息
   * @param {Object} message
   */
  const send = (message) => {
    if (ws?.readyState === WebSocket.OPEN) {
      try {
        // 对 payload 字段递归转换为 snake_case（action 作为路由标识符不转换）
        const action = message.action
        const converted = toSnakeCase(message)
        converted.action = action
        ws.send(JSON.stringify(converted))
      } catch (error) {
        logger.error('[Realtime] 发送消息失败:', error)
      }
    }
  }

  /**
   * 订阅 user 通道事件
   * @param {RealtimeEventCallback} callback
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
   * @param {RealtimeEventCallback} callback
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

    if (isFirstSubscription && ws?.readyState === WebSocket.OPEN) {
      const key = `session_${sessionId}`
      // 始终从 lastSeq Map 解析 last_seq，与 onConnectionOpen 行为对齐
      // options.replayFromSeq 优先（新会话创建场景显式传 0），否则用持久化的 lastSeq
      const storedSeq = lastSeq.has(key) ? lastSeq.get(key) : 0
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
      _setReplayPending(key)
      send({ action: 'subscribe_session', payload: { sessionId, lastSeq: seq } })
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
    _clearReplayPending(`session_${sessionId}`)

    if (ws?.readyState === WebSocket.OPEN) {
      send({ action: 'unsubscribe_session', payload: { sessionId } })
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
   * @param {RealtimeEventCallback} callback
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

    if (isFirstSubscription && ws?.readyState === WebSocket.OPEN) {
      const key = `task_${taskId}`
      // 始终从 lastSeq Map 解析 last_seq，与 onConnectionOpen 行为对齐
      const storedSeq = lastSeq.has(key) ? lastSeq.get(key) : 0
      const seq = options.replayFromSeq !== undefined ? options.replayFromSeq : storedSeq
      // Replay barrier：replay 期间缓冲实时事件，避免污染 lastSeq
      _setReplayPending(key)
      send({ action: 'subscribe_task', payload: { taskId, lastSeq: seq } })
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
    _clearReplayPending(`task_${taskId}`)

    if (ws?.readyState === WebSocket.OPEN) {
      send({ action: 'unsubscribe_task', payload: { taskId } })
    }
  }

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
    if (!isReplay && channelKey && replayPendingChannels.has(channelKey)) {
      if (!bufferedEvents.has(channelKey)) {
        bufferedEvents.set(channelKey, [])
      }
      bufferedEvents.get(channelKey).push(event)
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
        for (const cb of userCallbacks) {
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
          const callbacks = sessionCallbacks.get(sessionId)
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
          const callbacks = taskCallbacks.get(taskId)
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
        advanceLastSeq(channelKey, event.seq)
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
   * 重放指定通道
   *
   * 统一底层设计：
   * 使用 _channelKeyFromParts 计算 lastSeq 的 key，确保与 getChannelKey / dispatchEvent
   * 中 lastSeq.set 使用的 key 完全一致。
   *
   * lastSeq 无记录时回退到 0，确保首次连接能回放所有历史事件，
   * 非触发浏览器能收到 session_created 等关键历史事件自动订阅新 session 频道。
   *
   * @param {string} channelType - 'user' / 'session' / 'task'
   * @param {string} channelId
   */
  const replayChannel = (channelType, channelId) => {
    // key 计算：与 getChannelKey / dispatchEvent / _setReplayPending 保持一致
    const key = _channelKeyFromParts(channelType, channelId)
    // lastSeq 无记录时回退到 0，确保首次连接能回放历史事件
    const seq = lastSeq.has(key) ? lastSeq.get(key) : 0

    // Replay barrier：replay 期间缓冲实时事件
    _setReplayPending(key)

    send({
      action: 'replay',
      payload: {
        channelType,
        channelId,
        lastSeq: seq,
      },
    })
  }

  // ==================== Replay Barrier 辅助函数 ====================

  /**
   * 根据 channelType/channelId 计算 channelKey（与 getChannelKey 保持一致）
   * @param {string} channelType - 'user' / 'session' / 'task'
   * @param {string} channelId
   * @returns {string}
   */
  const _channelKeyFromParts = (channelType, channelId) => {
    if (channelType === 'user') return 'user'
    return `${channelType}_${channelId}`
  }

  /**
   * 标记 channel 为 replay 待定状态，缓冲后续实时事件
   * @param {string} channelKey
   */
  const _setReplayPending = (channelKey) => {
    if (!channelKey) return
    replayPendingChannels.add(channelKey)
    // 重置 safety timer
    if (replayBarrierTimers.has(channelKey)) {
      clearTimeout(replayBarrierTimers.get(channelKey))
    }
    const timer = window.setTimeout(() => {
      logger.warn(`[Realtime] replay barrier 超时，强制 flush: ${channelKey}`)
      _flushBufferedEvents(channelKey).catch(err => {
        logger.error('[Realtime] replay barrier 超时 flush 失败:', err)
      })
    }, REPLAY_BARRIER_TIMEOUT_MS)
    replayBarrierTimers.set(channelKey, timer)
    logger.debug(`[Realtime] replay barrier 设置 pending: ${channelKey}`)
  }

  /**
   * 清除 replay pending 状态（不 flush 缓冲事件）
   * 用于 unsubscribe 等场景，避免缓冲事件永远无法处理
   * @param {string} channelKey
   */
  const _clearReplayPending = (channelKey) => {
    replayPendingChannels.delete(channelKey)
    bufferedEvents.delete(channelKey)
    const timer = replayBarrierTimers.get(channelKey)
    if (timer) {
      clearTimeout(timer)
      replayBarrierTimers.delete(channelKey)
    }
  }

  /**
   * Flush 缓冲的实时事件（replay 完成后调用）
   * 按 seq 升序排序后逐个 dispatch（bypass barrier）
   *
   * 使用 for...of + await 确保严格顺序执行，避免并发处理导致状态覆盖。
   *
   * @param {string} channelKey
   */
  const _flushBufferedEvents = async (channelKey) => {
    if (!channelKey) return
    replayPendingChannels.delete(channelKey)
    const timer = replayBarrierTimers.get(channelKey)
    if (timer) {
      clearTimeout(timer)
      replayBarrierTimers.delete(channelKey)
    }
    const buffered = bufferedEvents.get(channelKey)
    if (!buffered || buffered.length === 0) {
      bufferedEvents.delete(channelKey)
      logger.debug(`[Realtime] replay barrier flush 无缓冲事件: ${channelKey}`)
      return
    }
    // 按 seq 升序排序，确保事件顺序正确
    buffered.sort((a, b) => {
      const seqA = typeof a.seq === 'number' ? a.seq : Number.MAX_SAFE_INTEGER
      const seqB = typeof b.seq === 'number' ? b.seq : Number.MAX_SAFE_INTEGER
      return seqA - seqB
    })
    logger.info(`[Realtime] replay barrier flush: ${channelKey}, count=${buffered.length}`)
    bufferedEvents.delete(channelKey)
    // 逐个 dispatch（bypass barrier：replayPendingChannels 已清除，不会再缓冲）
    // 注意：此处必须传 isReplay=false —— 缓冲的是 replay 期间到达的"实时"事件，
    // 不应被打上 isReplay 标记（否则实时审批超时会被当作历史回放而抑制提示）
    for (const evt of buffered) {
      try {
        await dispatchEvent(evt, false)
      } catch (err) {
        logger.error('[Realtime] flush 缓冲事件失败:', err)
      }
    }
  }

  /**
   * 连接成功后恢复订阅并重放
   *
   * 统一底层设计：
   * 将 session 和 task 通道的 subscribe + replay 合并为单次请求，
   * 后端 handle_subscribe_session / handle_subscribe_task 在 group_add 后
   * 立即触发 replay（_send_replay_chunked），前端在发送前设置 barrier，
   * 消除"subscribe 后 barrier 未设置"的时间窗口。
   *
   * 所有通道（user/session/task）统一走"subscribe + last_seq"单次请求，
   * 后端在同一个 handler 中完成 group_add + replay，确保原子性。
   *
   * 重连后还会对所有已订阅的 session/task 触发快照校对，
   * 修复断线期间可能丢失的事件。
   */
  const onConnectionOpen = () => {
    // 清除断开防抖定时器：500ms 内恢复则保持 connected，不显示 disconnected
    if (disconnectDebounceTimer) {
      clearTimeout(disconnectDebounceTimer)
      disconnectDebounceTimer = null
      logger.info('[Realtime] WebSocket 在防抖窗口内恢复，取消 disconnected 显示')
    }
    connectionStatus.value = RealtimeConnectionStatus.CONNECTED
    reconnectAttempt = 0
    exhaustedReconnect = false
    logger.info('[Realtime] WebSocket 已连接')

    // 重连后重启心跳
    startPing()

    // 重放 user 通道
    const userId = userStore.userInfo?.id
    if (userId) {
      replayChannel('user', userId)
    }

    // 统一 session 重连：subscribe + replay 合并为单次请求
    // 设置 barrier 在 send 之前，消除"subscribe 后 barrier 未设置"的时间窗口
    // 注意：不在此处触发快照校对。后端 subscribe_session 会自动 replay 缺失事件
    // （携带 last_seq），replay 中的关键事件（stream_finalized 等）会在
    // dispatchEvent 中按需触发快照校对。若在此处对每个 session 都触发快照，
    // N 个已订阅 session = N 次 HTTP 请求，与请求风暴同病。
    subscribedSessions.forEach(sessionId => {
      const key = `session_${sessionId}`
      const seq = lastSeq.has(key) ? lastSeq.get(key) : 0
      _setReplayPending(key)
      send({ action: 'subscribe_session', payload: { sessionId, lastSeq: seq } })
    })

    // 统一 task 重连：subscribe + replay 合并为单次请求
    // 同上，不在此处触发快照校对，依赖 replay 补发事件
    subscribedTasks.forEach(taskId => {
      const key = `task_${taskId}`
      const seq = lastSeq.has(key) ? lastSeq.get(key) : 0
      _setReplayPending(key)
      send({ action: 'subscribe_task', payload: { taskId, lastSeq: seq } })
    })
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
      const channelKey = _channelKeyFromParts(data.channelType, data.channelId)
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
          await dispatchEvent(evt, true)
        }
      }
      // 最后一块到达后 flush 缓冲的实时事件
      if (chunkIdx + 1 >= chunkCount) {
        await _flushBufferedEvents(channelKey)
      }
      return
    }

    if (data.type && typeof data.seq === 'number') {
      // 与 replay 分支（第 790 行）对齐：await dispatchEvent。
      // 虽然浏览器不会 await onMessage 的 Promise，无法串行化多个 onMessage 调用，
      // 但 await 可确保当前 onMessage 内部 dispatchEvent 完整执行（含 lastSeq 更新），
      // 避免 unhandled rejection，与 replay 分支行为一致。
      await dispatchEvent(data)
    }
  }

  /**
   * 计划重连（指数退避：1s → 2s → 4s → 8s → 10s，max 5 次）
   *
   * 5 次失败后停止重连，connectionStatus 设为 'disconnected'，
   * UI 显示"连接断开"提示。页面重新可见时会再次尝试重连。
   */
  const scheduleReconnect = () => {
    if (intentionalClose) return
    if (reconnectTimer) return
    if (reconnectAttempt >= RECONNECT_MAX_ATTEMPTS) {
      // 达到最大重连次数，停止重连，显示"连接断开"
      if (!exhaustedReconnect) {
        exhaustedReconnect = true
        logger.warn(`[Realtime] 已达到最大重连次数 (${RECONNECT_MAX_ATTEMPTS})，停止重连`)
        // SSE 活跃时不设置 disconnected（请求浏览器仍可通过 SSE 获取数据）
        if (streamingActiveCount.value === 0) {
          connectionStatus.value = RealtimeConnectionStatus.DISCONNECTED
        }
      }
      return
    }

    connectionStatus.value = RealtimeConnectionStatus.RECONNECTING
    reconnectAttempt++
    const delay = Math.min(RECONNECT_BASE_MS * Math.pow(2, reconnectAttempt - 1), RECONNECT_MAX_MS)
    logger.info(`[Realtime] 计划重连: attempt=${reconnectAttempt}/${RECONNECT_MAX_ATTEMPTS}, delay=${delay}ms`)

    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null
      connect()
    }, delay)
  }

  /**
   * 建立 WebSocket 连接
   */
  const connect = async () => {
    // 守卫：已有连接或正在连接/关闭中，不重复创建
    if (ws && ws.readyState !== WebSocket.CLOSED) return

    let token = userStore.token
    if (!token) {
      logger.debug('[Realtime] 无 token，跳过连接')
      return
    }

    // Token 预检：WebSocket 不经过 axios 拦截器，需在此处单独检查。
    // 若 token 即将过期（剩余 < 30s）或已过期，先刷新再连接，
    // 避免 WebSocket 用过期 token 被拒绝后每 2s 重连（日志中的重连风暴）。
    const exp = _getJwtExp(token)
    const nowSec = Math.floor(Date.now() / 1000)
    if (exp > 0 && exp - nowSec < 30) {
      try {
        const ok = await userStore.refreshAccessToken()
        if (ok) {
          token = userStore.token
          logger.info('[Realtime] WebSocket 连接前刷新 token 成功')
        }
      } catch (e) {
        logger.warn('[Realtime] WebSocket 连接前刷新 token 失败:', e)
        // 刷新失败继续用旧 token，让后端拒绝后走重连流程
      }
    }

    intentionalClose = false
    connectionStatus.value = RealtimeConnectionStatus.CONNECTING

    try {
      ws = new WebSocket(buildRealtimeUrl(token))
    } catch (error) {
      logger.error('[Realtime] 创建 WebSocket 失败:', error)
      scheduleReconnect()
      return
    }

    ws.onopen = onConnectionOpen

    ws.onmessage = onMessage

    ws.onerror = (_error) => {
      logger.error('[Realtime] WebSocket 错误:', _error)
    }

    ws.onclose = (event) => {
      ws = null
      logger.warn(`[Realtime] WebSocket 关闭: code=${event.code}, reason=${event.reason}`)
      // 立即开始重连（scheduleReconnect 会设置 reconnecting），重连不受防抖影响
      scheduleReconnect()
      // disconnected 状态延迟 500ms 显示，避免网络抖动导致频繁闪烁
      // 如果 500ms 内 onConnectionOpen 被调用，定时器会被清除，保持 connected
      if (disconnectDebounceTimer) {
        clearTimeout(disconnectDebounceTimer)
      }
      disconnectDebounceTimer = window.setTimeout(() => {
        disconnectDebounceTimer = null
        // SSE 活跃时不显示"已断开"（请求浏览器仍可通过 SSE 获取数据）
        if (streamingActiveCount.value > 0) {
          logger.info('[Realtime] 防抖结束但 SSE 仍活跃，不设置 disconnected')
          return
        }
        // 已恢复、正在重连时不覆盖
        // 避免将 reconnecting 错误覆盖为 disconnected，导致 UI 误显示"已断开"
        if ([RealtimeConnectionStatus.CONNECTED, RealtimeConnectionStatus.RECONNECTING].includes(connectionStatus.value)) {
          return
        }
        logger.info('[Realtime] 防抖结束，WebSocket 仍未恢复，设置 disconnected')
        connectionStatus.value = RealtimeConnectionStatus.DISCONNECTED
      }, DISCONNECT_DEBOUNCE_MS)
    }
  }

  /**
   * 断开连接
   */
  const disconnect = () => {
    intentionalClose = true
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (disconnectDebounceTimer) {
      clearTimeout(disconnectDebounceTimer)
      disconnectDebounceTimer = null
    }
    if (pingTimer) {
      clearInterval(pingTimer)
      pingTimer = null
    }
    if (ws) {
      // 移除 onclose 回调，避免 disconnect 触发 scheduleReconnect
      ws.onclose = null
      ws.close()
      ws = null
    }
    connectionStatus.value = RealtimeConnectionStatus.DISCONNECTED
    reconnectAttempt = 0
    exhaustedReconnect = false
  }

  /**
   * 启动心跳
   */
  const startPing = () => {
    if (pingTimer) return
    pingTimer = window.setInterval(() => {
      send({ action: 'ping' })
    }, PING_INTERVAL_MS)
  }

  // 监听登录状态，自动连接/断开
  watch(
    () => userStore.isLoggedIn,
    (loggedIn) => {
      if (loggedIn) {
        connect()
      } else {
        clearLastSeq()
        disconnect()
      }
    },
    { immediate: true }
  )

  // token 变更时（如刷新）主动重连以使用新 token
  // 注意：登录时 isLoggedIn 和 token 同时变化，isLoggedIn watch 已触发 connect，
  // 此处仅在已有连接时才 disconnect+reconnect，避免重复连接
  watch(
    () => userStore.token,
    (newToken, oldToken) => {
      if (newToken && newToken !== oldToken && userStore.isLoggedIn) {
        if (ws) {
          disconnect()
        }
        connect()
      }
    }
  )

  // 用户切换时清理/恢复 lastSeq
  watch(
    () => userStore.userInfo?.id,
    (newUserId, oldUserId) => {
      if (newUserId && oldUserId && newUserId !== oldUserId) {
        logger.info(`[Realtime] 检测到用户切换: ${oldUserId} -> ${newUserId}，重置 lastSeq`)
        clearLastSeq()
        connect()
      } else if (newUserId && !oldUserId) {
        loadLastSeq()
      }
    }
  )

  // 页面从隐藏切回可见时，若已断线则尝试重连
  // 重置 exhaustedReconnect 标志，给用户手动恢复的机会
  const handleVisibilityChange = () => {
    if (document.visibilityState === 'visible' &&
      connectionStatus.value === RealtimeConnectionStatus.DISCONNECTED &&
      userStore.isLoggedIn) {
      logger.info('[Realtime] 页面重新可见，重置重连计数并尝试恢复 WebSocket 连接')
      exhaustedReconnect = false
      reconnectAttempt = 0
      connect()
    }
  }
  document.addEventListener('visibilitychange', handleVisibilityChange)

  return {
    connectionStatus,
    streamingActiveCount,
    lastSeq,
    advanceLastSeq,
    subscribeUserEvents,
    subscribeSession,
    unsubscribeSession,
    subscribeTask,
    unsubscribeTask,
    incrementStreaming,
    decrementStreaming,
    connect,
    disconnect,
  }
}

/**
 * 获取全局唯一的实时同步管理器
 * @returns {ReturnType<typeof createRealtimeSync>}
 */
export function useRealtimeSync() {
  if (!instance) {
    instance = createRealtimeSync()
  }
  return instance
}
