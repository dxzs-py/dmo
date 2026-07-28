import { logger } from '@/utils/logger'

/**
 * 创建 seq 去重器
 *
 * 维护每个 session 最后处理的 seq，用于跳号检测和事件去重。
 * 纯函数式：所有状态都封装在返回对象内，不依赖外部闭包。
 *
 * 原逻辑位于 sync.js L514-536（applySessionEvent 内）与 L491-501（resetSessionSeq 内）。
 *
 * @returns {{
 *   lastSeenSeq: Map<string, number>,
 *   getPrevSeq: (sessionId: string) => number,
 *   setSeenSeq: (sessionId: string, seq: number) => void,
 *   resetSeq: (sessionId: string) => void,
 *   shouldSkip: (event: { seq?: number, _isReplay?: boolean }, sessionId: string, isReplay?: boolean) => { skip: boolean, resetToZero: boolean }
 * }}
 */
export const createSeqDedup = () => {
  /** 每个 session 最后处理的 seq，用于跳号检测 */
  /** @type {Map<string, number>} */
  const lastSeenSeq = new Map()

  const getPrevSeq = (sessionId) => lastSeenSeq.get(sessionId) || 0

  const setSeenSeq = (sessionId, seq) => {
    lastSeenSeq.set(sessionId, seq)
  }

  const resetSeq = (sessionId) => {
    lastSeenSeq.delete(sessionId)
  }

  /**
   * 判断事件是否应被跳过（seq 去重）
   *
   * 原逻辑（sync.js L514-536）：
   * - seq <= prevSeq 且非 replay → 跳过（debug 日志）
   * - seq <= prevSeq 且 replay → 重置基线为 0 后继续处理（info 日志，调用方负责 setSeenSeq(sessionId, 0)）
   * - seq > prevSeq → 正常处理
   *
   * @param {{ seq?: number, _isReplay?: boolean }} event
   * @param {string} sessionId
   * @param {boolean} [isReplay] - 是否为 replay 事件（默认读取 event._isReplay）
   * @returns {{ skip: boolean, resetToZero: boolean }}
   *   - skip: true 表示跳过该事件
   *   - resetToZero: true 表示应将基线重置为 0 后继续处理
   */
  const shouldSkip = (event, sessionId, isReplay) => {
    if (typeof event.seq !== 'number') {
      return { skip: false, resetToZero: false }
    }
    const prevSeq = getPrevSeq(sessionId)
    const replay = isReplay === true || event._isReplay === true
    if (event.seq <= prevSeq) {
      if (replay) {
        // replay 事件特殊处理：
        // 调用方通过 subscribeSession(replayFromSeq=0) 触发后端回放历史事件时，
        // 这些事件的 seq 可能远小于本地 lastSeenSeq（如切换任务后回放 seq=1,2,3，
        // 但本地 lastSeenSeq=50）。若按普通逻辑跳过，回放将完全失效，状态无法重建。
        // 此时重置 lastSeenSeq 为 0，让该事件及后续低 seq 事件能正常通过去重检查。
        // 注意：调用方应优先在 subscribeSession 前调用 resetSessionSeq 显式重置，
        // 此分支作为兜底保护，防止遗漏调用导致回放失效。
        logger.info(
          `[Sync] replay 事件 seq<=lastSeq，重置基线后继续处理: session=${sessionId}, ` +
          `seq=${event.seq}, lastSeq=${prevSeq}`
        )
        return { skip: false, resetToZero: true }
      }
      logger.debug(`[Sync] 跳过已处理事件: session=${sessionId}, seq=${event.seq}, lastSeq=${prevSeq}`)
      return { skip: true, resetToZero: false }
    }
    return { skip: false, resetToZero: false }
  }

  return {
    lastSeenSeq,
    getPrevSeq,
    setSeenSeq,
    resetSeq,
    shouldSkip,
  }
}
