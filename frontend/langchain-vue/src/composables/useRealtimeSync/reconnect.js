import { logger } from '@/utils/logger'
import { RealtimeConnectionStatus } from '@/types/realtimeEvents'
import {
  RECONNECT_BASE_MS,
  RECONNECT_MAX_MS,
  RECONNECT_MAX_ATTEMPTS,
  PING_INTERVAL_MS,
} from './constants.js'

/**
 * 重连与心跳子系统：指数退避重连计划 + 心跳保活
 *
 * 职责（原 createRealtimeSync 内联逻辑逐行迁移）：
 * - scheduleReconnect：指数退避（1s → 2s → 4s → 8s → 10s，max 5 次），
 *   5 次失败后停止重连（SSE 活跃时不置 disconnected），页面重新可见时由编排器重置计数
 * - startPing：启动心跳定时器（PING_INTERVAL_MS 间隔发送 ping）
 * - cancelReconnect / stopPing：供 connection.js 的 disconnect 复用的原句清理逻辑
 *   （原 disconnect 内联的 `if (reconnectTimer) {...}` / `if (pingTimer) {...}` 等价抽取）
 *
 * 依赖约定：跨模块依赖一律通过 ctx 在调用时访问（禁止创建期解构）——
 * ctx.connect（index.js 编排）在 watch 注册后才被调用，调用时必然已就位；
 * 工厂之间零静态 import，避免循环依赖。
 *
 * @param {Object} ctx - 编排器共享上下文
 * @returns {{
 *   scheduleReconnect: () => void,
 *   startPing: () => void,
 *   cancelReconnect: () => void,
 *   stopPing: () => void,
 * }}
 */
export function createReconnect(ctx) {
  /** @type {number | null} */
  let reconnectTimer = null
  /** @type {number | null} */
  let pingTimer = null

  /**
   * 计划重连（指数退避：1s → 2s → 4s → 8s → 10s，max 5 次）
   *
   * 5 次失败后停止重连，connectionStatus 设为 'disconnected'，
   * UI 显示"连接断开"提示。页面重新可见时会再次尝试重连。
   */
  const scheduleReconnect = () => {
    if (ctx.intentionalClose) return
    if (reconnectTimer) return
    if (ctx.reconnectAttempt >= RECONNECT_MAX_ATTEMPTS) {
      // 达到最大重连次数，停止重连，显示"连接断开"
      if (!ctx.exhaustedReconnect) {
        ctx.exhaustedReconnect = true
        logger.warn(`[Realtime] 已达到最大重连次数 (${RECONNECT_MAX_ATTEMPTS})，停止重连`)
        // SSE 活跃时不设置 disconnected（请求浏览器仍可通过 SSE 获取数据）
        if (ctx.streamingActiveCount.value === 0) {
          ctx.connectionStatus.value = RealtimeConnectionStatus.DISCONNECTED
        }
      }
      return
    }

    ctx.connectionStatus.value = RealtimeConnectionStatus.RECONNECTING
    ctx.reconnectAttempt++
    const delay = Math.min(RECONNECT_BASE_MS * Math.pow(2, ctx.reconnectAttempt - 1), RECONNECT_MAX_MS)
    logger.info(`[Realtime] 计划重连: attempt=${ctx.reconnectAttempt}/${RECONNECT_MAX_ATTEMPTS}, delay=${delay}ms`)

    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null
      ctx.connect()
    }, delay)
  }

  /**
   * 取消计划中的重连定时器（原 disconnect 内联语句的等价抽取）
   */
  const cancelReconnect = () => {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  /**
   * 启动心跳
   */
  const startPing = () => {
    if (pingTimer) return
    pingTimer = window.setInterval(() => {
      ctx.send({ action: 'ping' })
    }, PING_INTERVAL_MS)
  }

  /**
   * 停止心跳定时器（原 disconnect 内联语句的等价抽取）
   */
  const stopPing = () => {
    if (pingTimer) {
      clearInterval(pingTimer)
      pingTimer = null
    }
  }

  return {
    scheduleReconnect,
    startPing,
    cancelReconnect,
    stopPing,
  }
}
