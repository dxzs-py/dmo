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
 * 运行方式（Node 内置 assert，需在项目根 frontend/langchain-vue 目录执行）：
 *   node src/utils/__tests__/orderedQueue.test.js
 */
import { strict as assert } from 'node:assert'
import { register } from 'node:module'
import { pathToFileURL } from 'node:url'

// 先注册 `@/` 别名 loader，再动态 import（静态 import 会先于 register 执行，无法命中 loader）
register(pathToFileURL('./src/utils/__tests__/vite-alias-loader.mjs'))
const { createOrderedQueue } = await import('../../stores/sync/orderedQueue.js')

let passed = 0
let failed = 0

function test(name, fn) {
  try {
    fn()
    passed++
    console.log(`  ✓ ${name}`)
  } catch (err) {
    failed++
    console.error(`  ✗ ${name}`)
    console.error(`    ${err && err.message ? err.message : err}`)
  }
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms))

console.log('orderedQueue 单元测试\n')

// ── 1. 正常顺序处理 + onProcessed 推进 ──────────────────────────────────────
await test('正常顺序处理并联动 onProcessed 推进基线', async () => {
  const processed = []
  const baseline = []
  const queue = createOrderedQueue({
    onProcessed: (sessionId, seq) => baseline.push([sessionId, seq]),
  })
  await queue.process('s1', { seq: 1 }, async () => { processed.push(1) })
  await queue.process('s1', { seq: 2 }, async () => { processed.push(2) })
  await queue.process('s1', { seq: 3 }, async () => { processed.push(3) })
  assert.deepStrictEqual(processed, [1, 2, 3])
  assert.deepStrictEqual(baseline, [['s1', 1], ['s1', 2], ['s1', 3]])
})

// ── 2. 精确缺 1 个 seq（expected=9, got=10）→ 间隙停滞联动 ──────────────────
await test('缺 1 个 seq 且下一个连续：触发 onGapStalled 而非直接处理', async () => {
  const processed = []
  let gapStalled = null
  const queue = createOrderedQueue({
    onGapStalled: (sessionId, expectedSeq, minSeqInQueue) => {
      gapStalled = { sessionId, expectedSeq, minSeqInQueue }
    },
    onProcessed: (sessionId, seq) => { /* 基线推进由调用方注入，此处仅断言触发 */ },
  })
  // 先正常处理 1-8，推进 expectedSeq=9
  for (let i = 1; i <= 8; i++) {
    await queue.process('s1', { seq: i }, async () => { processed.push(i) })
  }
  // 收到 10（9 缺失）：应进入间隙等待而非当作连续事件直接处理
  const p = queue.process('s1', { seq: 10 }, async () => { processed.push(10) })
  await sleep(600) // 超过 GAP_WAIT_MS(400)
  await p
  assert.ok(gapStalled, '缺 1 seq 必须触发 onGapStalled')
  assert.strictEqual(gapStalled.expectedSeq, 9)
  assert.strictEqual(gapStalled.minSeqInQueue, 10)
  assert.deepStrictEqual(processed, [1, 2, 3, 4, 5, 6, 7, 8, 10])
})

// ── 3. 乱序到达（10 先于 9）→ 等待后按序处理 ────────────────────────────────
await test('乱序到达：等待窗口内补齐后按序处理，不触发 onGapStalled', async () => {
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
  assert.strictEqual(gapStalled, false, '窗口内补齐不应触发 onGapStalled')
  assert.deepStrictEqual(processed, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
})

// ── 4. 过期事件 → onDropped 联动 ────────────────────────────────────────────
await test('过期事件（seq < expectedSeq）触发 onDropped 且不处理', async () => {
  const processed = []
  const dropped = []
  const queue = createOrderedQueue({
    onDropped: (sessionId, seq) => dropped.push([sessionId, seq]),
  })
  await queue.process('s1', { seq: 5 }, async () => { processed.push(5) })
  await queue.process('s1', { seq: 3 }, async () => { processed.push(3) })
  assert.deepStrictEqual(processed, [5])
  assert.deepStrictEqual(dropped, [['s1', 3]])
})

// ── 5. 无 seq 事件 → 直接处理、不推进基线 ──────────────────────────────────
await test('无 seq 事件直接处理且不触发基线回调', async () => {
  const processed = []
  let baselineCalls = 0
  const queue = createOrderedQueue({
    onProcessed: () => { baselineCalls++ },
  })
  await queue.process('s1', { type: 'no_seq' }, async () => { processed.push('no_seq') })
  assert.deepStrictEqual(processed, ['no_seq'])
  assert.strictEqual(baselineCalls, 0)
})

console.log(`\n${passed} 通过, ${failed} 失败`)
if (failed > 0) process.exit(1)
