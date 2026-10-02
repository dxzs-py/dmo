import { ref } from 'vue'
import settings from '@/config/settings'
import { logger } from '@/utils/logger'
import { toSnakeCase } from '@/utils/sessionTransformers.js'
import { RealtimeConnectionStatus } from '@/types/realtimeEvents'
import {
  WS_PATH,
  DISCONNECT_DEBOUNCE_MS,
} from './constants.js'

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

/**
 * WS 连接子系统：建连 / 鉴权 / 生命周期 / 原始发送
 *
 * 职责（原 createRealtimeSync 内联逻辑逐行迁移）：
 * - connect/disconnect：WebSocket 建连（token 预检 + 过期刷新）与主动断开
 * - onConnectionOpen：连接成功后恢复订阅并重放（subscribe + last_seq 单次请求）
 * - send：原始 JSON 消息发送（payload 递归 snake_case，action 作为路由标识符不转换）
 * - connectionStatus / streamingActiveCount：对外可见的连接状态与 SSE 活跃计数
 *
 * 依赖约定：跨模块依赖一律通过 ctx 在调用时访问（禁止创建期解构）——
 * ctx.scheduleReconnect / ctx.startPing（reconnect.js）、ctx.replayChannel（replay.js）、
 * ctx.onMessage（dispatcher.js）、ctx.subscribedSessions / ctx.subscribedTasks
 * （subscribers.js）均在调用时（watch 注册之后）必然已就位；
 * 工厂之间零静态 import，避免循环依赖。
 *
 * @param {Object} ctx - 编排器共享上下文
 * @returns {{
 *   connectionStatus: import('vue').Ref<string>,
 *   streamingActiveCount: import('vue').Ref<number>,
 *   incrementStreaming: () => void,
 *   decrementStreaming: () => void,
 *   send: (message: Object) => void,
 *   connect: () => Promise<void>,
 *   disconnect: () => void,
 * }}
 */
export function createConnection(ctx) {
  // disconnected 显示防抖定时器
  /** @type {number | null} */
  let disconnectDebounceTimer = null

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
   * 发送原始 JSON 消息
   * @param {Object} message
   */
  const send = (message) => {
    if (ctx.ws?.readyState === WebSocket.OPEN) {
      try {
        // 对 payload 字段递归转换为 snake_case（action 作为路由标识符不转换）
        const action = message.action
        const converted = toSnakeCase(message)
        converted.action = action
        ctx.ws.send(JSON.stringify(converted))
      } catch (error) {
        logger.error('[Realtime] 发送消息失败:', error)
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
    ctx.reconnectAttempt = 0
    ctx.exhaustedReconnect = false
    logger.info('[Realtime] WebSocket 已连接')

    // 重连后重启心跳
    ctx.startPing()

    // 重放 user 通道
    const userId = ctx.userStore.userInfo?.id
    if (userId) {
      ctx.replayChannel('user', userId)
    }

    // 统一 session 重连：subscribe + replay 合并为单次请求
    // 设置 barrier 在 send 之前，消除"subscribe 后 barrier 未设置"的时间窗口
    // 注意：不在此处触发快照校对。后端 subscribe_session 会自动 replay 缺失事件
    // （携带 last_seq），replay 中的关键事件（stream_finalized 等）会在
    // dispatchEvent 中按需触发快照校对。若在此处对每个 session 都触发快照，
    // N 个已订阅 session = N 次 HTTP 请求，与请求风暴同病。
    ctx.subscribedSessions.forEach(sessionId => {
      const key = `session_${sessionId}`
      const seq = ctx.lastSeq.has(key) ? ctx.lastSeq.get(key) : 0
      ctx._setReplayPending(key)
      send({ action: 'subscribe_session', payload: { sessionId, lastSeq: seq } })
    })

    // 统一 task 重连：subscribe + replay 合并为单次请求
    // 同上，不在此处触发快照校对，依赖 replay 补发事件
    ctx.subscribedTasks.forEach(taskId => {
      const key = `task_${taskId}`
      const seq = ctx.lastSeq.has(key) ? ctx.lastSeq.get(key) : 0
      ctx._setReplayPending(key)
      send({ action: 'subscribe_task', payload: { taskId, lastSeq: seq } })
    })
  }

  /**
   * 建立 WebSocket 连接
   */
  const connect = async () => {
    // 守卫：已有连接或正在连接/关闭中，不重复创建
    if (ctx.ws && ctx.ws.readyState !== WebSocket.CLOSED) return

    let token = ctx.userStore.token
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
        const ok = await ctx.userStore.refreshAccessToken()
        if (ok) {
          token = ctx.userStore.token
          logger.info('[Realtime] WebSocket 连接前刷新 token 成功')
        }
      } catch (e) {
        logger.warn('[Realtime] WebSocket 连接前刷新 token 失败:', e)
        // 刷新失败继续用旧 token，让后端拒绝后走重连流程
      }
    }

    ctx.intentionalClose = false
    connectionStatus.value = RealtimeConnectionStatus.CONNECTING

    try {
      ctx.ws = new WebSocket(buildRealtimeUrl(token))
    } catch (error) {
      logger.error('[Realtime] 创建 WebSocket 失败:', error)
      ctx.scheduleReconnect()
      return
    }

    ctx.ws.onopen = onConnectionOpen

    ctx.ws.onmessage = ctx.onMessage

    ctx.ws.onerror = (_error) => {
      logger.error('[Realtime] WebSocket 错误:', _error)
    }

    ctx.ws.onclose = (event) => {
      ctx.ws = null
      logger.warn(`[Realtime] WebSocket 关闭: code=${event.code}, reason=${event.reason}`)
      // 立即开始重连（scheduleReconnect 会设置 reconnecting），重连不受防抖影响
      ctx.scheduleReconnect()
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
    ctx.intentionalClose = true
    ctx.cancelReconnect()
    if (disconnectDebounceTimer) {
      clearTimeout(disconnectDebounceTimer)
      disconnectDebounceTimer = null
    }
    ctx.stopPing()
    if (ctx.ws) {
      // 移除 onclose 回调，避免 disconnect 触发 scheduleReconnect
      ctx.ws.onclose = null
      ctx.ws.close()
      ctx.ws = null
    }
    connectionStatus.value = RealtimeConnectionStatus.DISCONNECTED
    ctx.reconnectAttempt = 0
    ctx.exhaustedReconnect = false
  }

  return {
    connectionStatus,
    streamingActiveCount,
    incrementStreaming,
    decrementStreaming,
    send,
    connect,
    disconnect,
  }
}
