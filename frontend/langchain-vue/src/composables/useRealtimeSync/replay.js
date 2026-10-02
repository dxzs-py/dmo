import { logger } from '@/utils/logger'
import { REPLAY_BARRIER_TIMEOUT_MS } from './constants.js'

/**
 * replay 子系统：快照回放 + Replay Barrier
 *
 * 职责（原 createRealtimeSync 内联逻辑逐行迁移）：
 * - replayChannel：按通道发送 replay 请求（lastSeq 无记录回退 0，保证首次连接能回放历史）
 * - Replay Barrier（统一底层设计）：
 *     1) 后端 replay 已分块发送，但前端在 replay 期间仍可能收到实时事件
 *     2) 实时事件 seq 可能小于 replay 中最大 seq（replay 历史包含更早事件）
 *     3) 若实时事件先于 replay 被处理，会污染 lastSeq，导致 replay 部分事件被 dedup 跳过
 *   解决方案：replay 期间缓冲实时事件，replay 完成后按 seq 顺序 flush
 *
 * 依赖约定：跨模块依赖一律通过 ctx 在调用时访问（禁止创建期解构）——
 * ctx.dispatchEvent 由 dispatcher.js 在本工厂之后注册，调用时必然已就位；
 * 工厂之间零静态 import，避免循环依赖。
 *
 * @param {Object} ctx - 编排器共享上下文（含 userStore/ws 等跨子系统状态）
 * @returns {{
 *   replayPendingChannels: Set<string>,
 *   bufferedEvents: Map<string, import('@/types/realtimeEvents').RealtimeEvent[]>,
 *   _channelKeyFromParts: (channelType: string, channelId: string) => string,
 *   _setReplayPending: (channelKey: string) => void,
 *   _clearReplayPending: (channelKey: string) => void,
 *   _flushBufferedEvents: (channelKey: string) => Promise<void>,
 *   replayChannel: (channelType: string, channelId: string) => void,
 * }}
 */
export function createReplay(ctx) {
  /** @type {Set<string>} */ // 等待 replay 完成的 channelKey 集合
  const replayPendingChannels = new Set()
  /** @type {Map<string, import('@/types/realtimeEvents').RealtimeEvent[]>} */ // 缓冲的实时事件，按 channelKey 分组
  const bufferedEvents = new Map()
  /** @type {Map<string, number>} */ // safety timeout timer，防止 replay 永不到达
  const replayBarrierTimers = new Map()

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
    const seq = ctx.lastSeq.has(key) ? ctx.lastSeq.get(key) : 0

    // Replay barrier：replay 期间缓冲实时事件
    _setReplayPending(key)

    ctx.send({
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
        await ctx.dispatchEvent(evt, false)
      } catch (err) {
        logger.error('[Realtime] flush 缓冲事件失败:', err)
      }
    }
  }

  return {
    replayPendingChannels,
    bufferedEvents,
    _channelKeyFromParts,
    _setReplayPending,
    _clearReplayPending,
    _flushBufferedEvents,
    replayChannel,
  }
}
