/**
 * 创建 seq 跳号检测基线（Task 4：seq 单一权威）
 *
 * 职责定位（Task 4 单一权威收敛后）：
 * - 事件级去重的**唯一权威**是 useRealtimeSync.lastSeq（channel 维度，单调推进，
 *   决定 replay 起点并持久化）；"事件已见"不再由本模块判断。
 * - 本模块仅维护 per-session 的**跳号检测基线**（lastSeenSeq），
 *   供 handleSessionEvent.js 的跳号检测（seq > prevSeq + 1 → 触发 requestFullSync）使用。
 * - 事件丢弃（seq < orderedQueue.expectedSeq）时，由 sync.js 的 advanceBaseline
 *   联动推进本基线，保证基线不落后于有序队列，避免误判跳号（双基线发散修复）。
 *
 * 原逻辑位于 sync.js L514-536（applySessionEvent 内）与 L491-501（resetSessionSeq 内）。
 * shouldSkip 的"事件已见去重 / replay 基线重置"逻辑已删除（职责移交 lastSeq + orderedQueue）。
 *
 * @returns {{
 *   lastSeenSeq: Map<string, number>,
 *   getPrevSeq: (sessionId: string) => number,
 *   setSeenSeq: (sessionId: string, seq: number) => void,
 *   resetSeq: (sessionId: string) => void
 * }}
 */
export const createSeqDedup = () => {
  /** 每个 session 最后处理的 seq，仅用于跳号检测（非去重） */
  /** @type {Map<string, number>} */
  const lastSeenSeq = new Map()

  /**
   * 获取指定 session 的跳号检测基线（无记录时返回 0）
   * @param {string} sessionId
   * @returns {number}
   */
  const getPrevSeq = (sessionId) => lastSeenSeq.get(sessionId) || 0

  /**
   * 推进跳号检测基线（取 max 防回退：replay/乱序事件可能携带更小 seq，
   * 直接覆盖会令后续事件被误判为跳号）
   * @param {string} sessionId
   * @param {number} seq
   */
  const setSeenSeq = (sessionId, seq) => {
    const prev = lastSeenSeq.get(sessionId) || 0
    if (seq > prev) {
      lastSeenSeq.set(sessionId, seq)
    }
  }

  /**
   * 重置指定 session 的跳号检测基线（replayFromSeq=0 全量回放前调用）
   * @param {string} sessionId
   */
  const resetSeq = (sessionId) => {
    lastSeenSeq.delete(sessionId)
  }

  return {
    lastSeenSeq,
    getPrevSeq,
    setSeenSeq,
    resetSeq,
  }
}
