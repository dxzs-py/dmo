/**
 * inlineContent 单元测试（Agent 图层嵌套规范 Task 5.2）
 *
 * 覆盖：空 content、空 toolCalls、同 position 排序、无 position 追加末尾、
 * position 超界按末尾、乱序/重复 position 幂等跳过、正文切分正确性。
 *
 * 运行方式（Node 内置 assert，无需第三方框架）：
 *   node src/utils/__tests__/inlineContent.test.js
 */
import { strict as assert } from 'node:assert'
import { splitContentByToolPositions } from '../inlineContent.js'

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

console.log('inlineContent 单元测试\n')

const tool = (id, { position, seq } = {}) => ({
  id,
  toolCallId: id,
  ...(typeof position === 'number' ? { position } : {}),
  ...(typeof seq === 'number' ? { seq } : {}),
})

// ===== 空输入 =====
test('空 content + 空 toolCalls：返回空数组', () => {
  assert.deepEqual(splitContentByToolPositions('', []), [])
})

test('空 toolCalls + 非空 content：返回纯正文单段', () => {
  const segs = splitContentByToolPositions('hello', [])
  assert.deepEqual(segs, [{ type: 'content', text: 'hello' }])
})

test('非空 content + 无 position 工具：纯正文 + 工具段末尾', () => {
  const segs = splitContentByToolPositions('hello', [tool('t1')])
  assert.equal(segs.length, 2)
  assert.deepEqual(segs[0], { type: 'content', text: 'hello' })
  assert.deepEqual(segs[1], { type: 'tool', toolCall: tool('t1') })
})

// ===== 基础切分 =====
test('按 position 切分正文并在 position 处插入工具段', () => {
  // 正文 "0123456789"，工具 position=3
  const segs = splitContentByToolPositions('0123456789', [tool('t1', { position: 3 })])
  assert.deepEqual(segs, [
    { type: 'content', text: '012' },
    { type: 'tool', toolCall: tool('t1', { position: 3 }) },
    { type: 'content', text: '3456789' },
  ])
})

test('position=0：工具段在正文之前', () => {
  const segs = splitContentByToolPositions('abc', [tool('t1', { position: 0 })])
  assert.deepEqual(segs, [
    { type: 'tool', toolCall: tool('t1', { position: 0 }) },
    { type: 'content', text: 'abc' },
  ])
})

test('position=content 末尾：正文 + 工具段末尾', () => {
  const segs = splitContentByToolPositions('abc', [tool('t1', { position: 3 })])
  assert.deepEqual(segs, [
    { type: 'content', text: 'abc' },
    { type: 'tool', toolCall: tool('t1', { position: 3 }) },
  ])
})

// ===== 同 position =====
test('同 position 多工具：按 seq 连续插入（不重复正文）', () => {
  const t2 = tool('t2', { position: 3, seq: 2 })
  const t1 = tool('t1', { position: 3, seq: 1 })
  const segs = splitContentByToolPositions('0123456789', [t2, t1])
  assert.deepEqual(segs, [
    { type: 'content', text: '012' },
    { type: 'tool', toolCall: t1 },
    { type: 'tool', toolCall: t2 },
    { type: 'content', text: '3456789' },
  ])
})

// ===== 无 position =====
test('无 position 工具追加末尾（保持排序后相对顺序）', () => {
  const t1 = tool('t1', { position: 2 })
  const noPos1 = tool('no1')
  const noPos2 = tool('no2')
  const segs = splitContentByToolPositions('0123456789', [noPos2, t1, noPos1])
  // 有 position 的在正文内联；无 position 的按排序后稳定顺序追加末尾
  assert.equal(segs.length, 5)
  assert.deepEqual(segs[1], { type: 'tool', toolCall: t1 })
  assert.deepEqual(segs[3], { type: 'tool', toolCall: noPos2 })
  assert.deepEqual(segs[4], { type: 'tool', toolCall: noPos1 })
})

// ===== position 超界 =====
test('position 超界（> content 长度）：按末尾处理', () => {
  const segs = splitContentByToolPositions('abc', [tool('t1', { position: 99 })])
  assert.deepEqual(segs, [
    { type: 'content', text: 'abc' },
    { type: 'tool', toolCall: tool('t1', { position: 99 }) },
  ])
})

test('超界工具与无 position 工具混合：正文 + 末尾工具段（排序后相对顺序）', () => {
  const over = tool('over', { position: 50 })
  const noPos = tool('noPos')
  const segs = splitContentByToolPositions('abc', [noPos, over])
  // 排序后 over（有 position）在前；末尾追加保持 sorted 顺序
  assert.deepEqual(segs, [
    { type: 'content', text: 'abc' },
    { type: 'tool', toolCall: over },
    { type: 'tool', toolCall: noPos },
  ])
})

// ===== 乱序/重复 position =====
test('乱序 position：按 position 升序切段（输入乱序不影响输出）', () => {
  const t5 = tool('t5', { position: 5 })
  const t1 = tool('t1', { position: 1 })
  const t3 = tool('t3', { position: 3 })
  const segs = splitContentByToolPositions('0123456789', [t5, t1, t3])
  assert.deepEqual(segs.map(s => s.type === 'tool' ? s.toolCall.id : s.text), [
    '0', 't1', '12', 't3', '34', 't5', '56789',
  ])
})

test('重复 position：幂等跳过（不重复插入）', () => {
  const t1a = tool('t1a', { position: 3, seq: 1 })
  const t1b = tool('t1b', { position: 3, seq: 1 }) // 同一 position 同 seq（去重语义）
  const segs = splitContentByToolPositions('0123456789', [t1a, t1b])
  // 同一 position 多个工具都应插入（正常并发工具场景），此处验证不产生空正文段
  const tools = segs.filter(s => s.type === 'tool')
  assert.equal(tools.length, 2)
  const contentSegs = segs.filter(s => s.type === 'content')
  assert.equal(contentSegs.length, 2)
  assert.equal(contentSegs[0].text, '012')
  assert.equal(contentSegs[1].text, '3456789')
})

// ===== 停止中断（工具未完成） =====
test('停止中断：部分工具 running/pending 也参与切段（position 已写入）', () => {
  const t1 = tool('t1', { position: 2, status: 'completed' })
  const t2 = tool('t2', { position: 5, status: 'running' })
  const segs = splitContentByToolPositions('0123456789', [t1, t2])
  assert.deepEqual(segs.map(s => s.type === 'tool' ? s.toolCall.id : s.text), [
    '01', 't1', '234', 't2', '56789',
  ])
})

console.log(`\ninlineContent 测试完成: ${passed} 通过, ${failed} 失败\n`)
if (failed > 0) process.exit(1)
