import { logger } from '@/utils/logger'

/**
 * 创建有序事件队列
 *
 * 所有模块（聊天/深度研究/学习工作流/深度研究模式）的 WebSocket 事件
 * 统一通过此队列按 seq 顺序处理，避免乱序导致状态不一致。
 *
 * 原逻辑位于 sync.js L197-329（_sessionEventQueue 与 _processSessionEventOrdered）。
 *
 * 核心策略：
 * 1. 事件按 seq 进入队列，按 seq 升序处理
 * 2. expectedSeq 在队列中：正常顺序处理
 * 3. expectedSeq 缺失但存在间隙（minSeqInQueue > expectedSeq + 1 且 expectedSeq > 0）：
 *    等待 2 秒让乱序事件到达，等待后重新检查；仍未到达则强制处理 minSeqInQueue
 * 4. 首次事件（expectedSeq=0）或连续事件（无间隙）：直接处理，不等待
 * 5. seq < expectedSeq 的事件视为已处理/过期，直接丢弃
 *
 * 设计说明：
 * - 保留队列 + processing 标志：多个 onMessage 并发时（浏览器不 await onMessage 的 Promise），
 *   processing 标志保证事件串行处理，避免并发竞态。
 * - WebSocket 事件可能乱序到达（如 seq=11 先于 seq=3-10），间隙等待避免
 *   expectedSeq 跳过中间事件导致关键事件（tool_call_input_ready/approval_pending 等）被丢弃。
 * - 每次间隙只等待一次 2 秒，避免无限等待；真正的事件丢失由 requestFullSync 兜底。
 *
 * @returns {{
 *   queue: Map<string, {expectedSeq: number, queue: Map<number, {event: Object, processor: Function}>, processing: boolean}>,
 *   process: (sessionId: string, event: Object, handler: (event: Object) => Promise<void>) => Promise<void>,
 *   reset: (sessionId: string) => void
 * }}
 */
export const createOrderedQueue = () => {
  // session 通道事件有序队列
  // 所有模块（聊天/深度研究/学习工作流/深度研究模式）的 WebSocket 事件
  // 统一通过此队列按 seq 顺序处理，避免乱序导致状态不一致。
  /** @type {Map<string, {expectedSeq: number, queue: Map<number, {event: Object, processor: Function}>, processing: boolean}>} */
  const queue = new Map() // sessionId -> { expectedSeq, queue: Map, processing: boolean }

  /**
   * 按顺序处理 session 事件（所有模块实时同步的统一底层）
   *
   * 这是所有模块实时同步的统一底层：聊天模块、深度研究模块、学习工作流、
   * 聊天模块的深度研究模式都通过此函数处理事件。
   *
   * @param {string} sessionId
   * @param {Object} event
   * @param {(event: Object) => Promise<void>} handler - 实际处理函数
   */
  const process = async (sessionId, event, handler) => {
    if (!event.seq || typeof event.seq !== 'number') {
      await handler(event)
      return
    }

    if (!queue.has(sessionId)) {
      queue.set(sessionId, { expectedSeq: 0, queue: new Map(), processing: false })
    }
    const state = queue.get(sessionId)

    // 已处理或过期的事件直接丢弃
    if (event.seq < state.expectedSeq) {
      logger.debug(`[Sync] 丢弃过期事件: session=${sessionId}, seq=${event.seq}, expected=${state.expectedSeq}`)
      return
    }

    state.queue.set(event.seq, { event, processor: handler })

    if (state.processing) return

    state.processing = true
    try {
      while (state.queue.size > 0) {
        if (state.queue.has(state.expectedSeq)) {
          // expectedSeq 在队列中：正常顺序处理
          const { event: ev, processor: proc } = state.queue.get(state.expectedSeq)
          state.queue.delete(state.expectedSeq)
          try {
            await proc(ev)
          } catch (err) {
            logger.error(`[Sync] 事件处理失败 seq=${state.expectedSeq}:`, err)
          }
          state.expectedSeq = ev.seq + 1
          continue
        }

        // expectedSeq 缺失：查找最小 seq（清理过期残留）
        let minSeqInQueue = Infinity
        for (const [seq] of state.queue) {
          if (seq < state.expectedSeq) {
            state.queue.delete(seq)
            continue
          }
          if (seq < minSeqInQueue) minSeqInQueue = seq
        }

        if (minSeqInQueue === Infinity) break

        // 间隙等待：minSeqInQueue > expectedSeq + 1 且 expectedSeq > 0（非首次事件）
        // WebSocket 事件乱序到达时（如 seq=11 先于 seq=3-10），等待 2 秒让中间事件到达
        // 避免直接处理 minSeqInQueue 导致 expectedSeq 跳过中间事件、关键事件被丢弃。
        // 首次事件（expectedSeq=0）和连续事件（minSeqInQueue == expectedSeq + 1）不等待。
        if (minSeqInQueue > state.expectedSeq + 1 && state.expectedSeq > 0) {
          const gapStartSeq = state.expectedSeq
          const awaitedMinSeq = minSeqInQueue
          logger.warn(
            `[Sync] 检测到事件间隙，等待 2 秒让乱序事件到达: session=${sessionId}, ` +
            `expected=${gapStartSeq}, minInQueue=${awaitedMinSeq}`
          )
          await new Promise(resolve => setTimeout(resolve, 2000))

          // 等待后重新检查 expectedSeq 是否在队列中（乱序事件已到达）
          if (state.queue.has(state.expectedSeq)) {
            logger.info(
              `[Sync] 间隙等待后 expectedSeq 已到达，按顺序处理: session=${sessionId}, ` +
              `expected=${state.expectedSeq}`
            )
            continue
          }

          // 等待后仍未到达：强制处理 minSeqInQueue（避免无限等待）
          // 重新计算 minSeqInQueue（等待期间可能有新事件入队）
          minSeqInQueue = Infinity
          for (const [seq] of state.queue) {
            if (seq < state.expectedSeq) {
              state.queue.delete(seq)
              continue
            }
            if (seq < minSeqInQueue) minSeqInQueue = seq
          }
          if (minSeqInQueue === Infinity) break

          logger.warn(
            `[Sync] 间隙等待 2 秒后 expectedSeq 仍未到达，强制处理 minSeq: session=${sessionId}, ` +
            `expected=${gapStartSeq}, min=${minSeqInQueue}`
          )
        }

        // 处理 minSeqInQueue，推进 expectedSeq
        const { event: ev, processor: proc } = state.queue.get(minSeqInQueue)
        state.queue.delete(minSeqInQueue)
        try {
          await proc(ev)
        } catch (err) {
          logger.error(`[Sync] 事件处理失败 seq=${minSeqInQueue}:`, err)
        }
        state.expectedSeq = minSeqInQueue + 1
      }
    } finally {
      state.processing = false
    }
  }

  /**
   * 重置指定会话的队列状态（expectedSeq=0、清空 queue、processing=false）
   *
   * 使用场景：DeepResearchView 切换任务时调用 subscribeSession(replayFromSeq=0) 触发后端回放，
   * 但 expectedSeq 不会因 replayFromSeq=0 自动重置，导致回放的低 seq 事件被当作"过期事件"丢弃。
   * 调用方在 subscribeSession 前调用此函数手动重置，确保回放事件能正常处理。
   *
   * 注意：
   * - 仅重置本会话状态，不影响其他会话。
   * - 建议在 subscribeSession(replayFromSeq=0) 之前调用，避免与正在处理的事件队列竞态。
   * - 若 processing===true 时调用，可能丢失未处理完的事件；
   *   调用方需确保调用时机不在事件处理过程中。
   *
   * @param {string} sessionId
   */
  const reset = (sessionId) => {
    const state = queue.get(sessionId)
    if (state) {
      state.expectedSeq = 0
      state.queue.clear()
      state.processing = false
    }
  }

  return {
    queue,
    process,
    reset,
  }
}
