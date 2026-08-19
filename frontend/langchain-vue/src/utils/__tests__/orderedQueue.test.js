/**
 * orderedQueue 单元测试（事件序列跳号根治）
 *
 * 覆盖：
 * 1. 正常顺序处理 + onProcessed 统一推进基线；
 * 2. 精确缺 1 个 seq 且下一个连续（expected=9, got=10）→ 触发间隙等待 +
 *    onGapStalled 推进跳号基线，处理 minSeq 不误判；
 * 3. 乱序到达（10 先于 9）→ 等待后按序处理；
 * 4. 过期事件（seq < expectedSeq）→ onDropped 联动；
 * 5. 无 seq 事件 → 直接处理、不推进基线。
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest'
import { createOrderedQueue } from '../../stores/sync/orderedQueue.js'

// 抑制被测模块经 logger 输出的间隙等待日志，保持测试输出整洁
// （vitest 下 import.meta.env 可用，logger.js 可直接加载，无需测试桩）
const consoleSpies = []

beforeAll(() => {
  for (const method of ['log', 'info', 'warn', 'error', 'debug']) {
    consoleSpies.push(vi.spyOn(console, method).mockImplementation(() => {}))
  }
})

afterAll(() => {
  consoleSpies.forEach(spy => spy.mockRestore())
})

const sleep = (ms) => new Promise(r => setTimeout(r, ms))

describe('orderedQueue', () => {
  // ── 1. 正常顺序处理 + onProcessed 推进 ──────────────────────────────────────
  it('正常顺序处理并联动 onProcessed 推进基线', async () => {
    const processed = []
    const baseline = []
    const queue = createOrderedQueue({
      onProcessed: (sessionId, seq) => baseline.push([sessionId, seq]),
    })
    await queue.process('s1', { seq: 1 }, async () => { processed.push(1) })
    await queue.process('s1', { seq: 2 }, async () => { processed.push(2) })
    await queue.process('s1', { seq: 3 }, async () => { processed.push(3) })
    expect(processed).toStrictEqual([1, 2, 3])
    expect(baseline).toStrictEqual([['s1', 1], ['s1', 2], ['s1', 3]])
  })

  // ── 2. 精确缺 1 个 seq（expected=9, got=10）→ 间隙停滞联动 ──────────────────
  it('缺 1 个 seq 且下一个连续：触发 onGapStalled 而非直接处理', async () => {
    const processed = []
    let gapStalled = null
    const queue = createOrderedQueue({
      onGapStalled: (sessionId, expectedSeq, minSeqInQueue) => {
        gapStalled = { sessionId, expectedSeq, minSeqInQueue }
      },
      onProcessed: (_sessionId, _seq) => { /* 基线推进由调用方注入，此处仅断言触发 */ },
    })
    // 先正常处理 1-8，推进 expectedSeq=9
    for (let i = 1; i <= 8; i++) {
      await queue.process('s1', { seq: i }, async () => { processed.push(i) })
    }
    // 收到 10（9 缺失）：应进入间隙等待而非当作连续事件直接处理
    const p = queue.process('s1', { seq: 10 }, async () => { processed.push(10) })
    await sleep(600) // 超过 GAP_WAIT_MS(400)
    await p
    expect(gapStalled, '缺 1 seq 必须触发 onGapStalled').toBeTruthy()
    expect(gapStalled.expectedSeq).toBe(9)
    expect(gapStalled.minSeqInQueue).toBe(10)
    expect(processed).toStrictEqual([1, 2, 3, 4, 5, 6, 7, 8, 10])
  })

  // ── 3. 乱序到达（10 先于 9）→ 等待后按序处理 ────────────────────────────────
  it('乱序到达：等待窗口内补齐后按序处理，不触发 onGapStalled', async () => {
    const processed = []
    let gapStalled = false
    const queue = createOrderedQueue({
      onGapStalled: () => { gapStalled = true },
    })
    for (let i = 1; i <= 8; i++) {
      await queue.process('s1', { seq: i }, async () => { processed.push(i) })
    }
    // 10 先到（9 缺失）
    const p10 = queue.process('s1', { seq: 10 }, async () => { processed.push(10) })
    // 10 后立即补发 9（在 GAP_WAIT_MS 窗口内）
    await sleep(100)
    await queue.process('s1', { seq: 9 }, async () => { processed.push(9) })
    await p10
    expect(gapStalled, '窗口内补齐不应触发 onGapStalled').toBe(false)
    expect(processed).toStrictEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
  })

  // ── 4. 过期事件 → onDropped 联动 ────────────────────────────────────────────
  it('过期事件（seq < expectedSeq）触发 onDropped 且不处理', async () => {
    const processed = []
    const dropped = []
    const queue = createOrderedQueue({
      onDropped: (sessionId, seq) => dropped.push([sessionId, seq]),
    })
    await queue.process('s1', { seq: 5 }, async () => { processed.push(5) })
    await queue.process('s1', { seq: 3 }, async () => { processed.push(3) })
    expect(processed).toStrictEqual([5])
    expect(dropped).toStrictEqual([['s1', 3]])
  })

  // ── 5. 无 seq 事件 → 直接处理、不推进基线 ──────────────────────────────────
  it('无 seq 事件直接处理且不触发基线回调', async () => {
    const processed = []
    let baselineCalls = 0
    const queue = createOrderedQueue({
      onProcessed: () => { baselineCalls++ },
    })
    await queue.process('s1', { type: 'no_seq' }, async () => { processed.push('no_seq') })
    expect(processed).toStrictEqual(['no_seq'])
    expect(baselineCalls).toBe(0)
  })
})
