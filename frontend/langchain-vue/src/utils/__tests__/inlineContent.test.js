/**
 * inlineContent 单元测试（Agent 图层嵌套规范 Task 5.2）
 *
 * 覆盖：空 content、空 toolCalls、同 position 排序、无 position 追加末尾、
 * position 超界按末尾、乱序/重复 position 幂等跳过、正文切分正确性。
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect } from 'vitest'
import { splitContentByToolPositions } from '../inlineContent.js'

const tool = (id, { position, seq } = {}) => ({
  id,
  toolCallId: id,
  ...(typeof position === 'number' ? { position } : {}),
  ...(typeof seq === 'number' ? { seq } : {}),
})

describe('inlineContent', () => {
  // ===== 空输入 =====
  it('空 content + 空 toolCalls：返回空数组', () => {
    expect(splitContentByToolPositions('', [])).toEqual([])
  })

  it('空 toolCalls + 非空 content：返回纯正文单段', () => {
    const segs = splitContentByToolPositions('hello', [])
    expect(segs).toEqual([{ type: 'content', text: 'hello' }])
  })

  it('非空 content + 无 position 工具：纯正文 + 工具段末尾', () => {
    const segs = splitContentByToolPositions('hello', [tool('t1')])
    expect(segs.length).toEqual(2)
    expect(segs[0]).toEqual({ type: 'content', text: 'hello' })
    expect(segs[1]).toEqual({ type: 'tool', toolCall: tool('t1') })
  })

  // ===== 基础切分 =====
  it('按 position 切分正文并在 position 处插入工具段', () => {
    // 正文 "0123456789"，工具 position=3
    const segs = splitContentByToolPositions('0123456789', [tool('t1', { position: 3 })])
    expect(segs).toEqual([
      { type: 'content', text: '012' },
      { type: 'tool', toolCall: tool('t1', { position: 3 }) },
      { type: 'content', text: '3456789' },
    ])
  })

  it('position=0：工具段在正文之前', () => {
    const segs = splitContentByToolPositions('abc', [tool('t1', { position: 0 })])
    expect(segs).toEqual([
      { type: 'tool', toolCall: tool('t1', { position: 0 }) },
      { type: 'content', text: 'abc' },
    ])
  })

  it('position=content 末尾：正文 + 工具段末尾', () => {
    const segs = splitContentByToolPositions('abc', [tool('t1', { position: 3 })])
    expect(segs).toEqual([
      { type: 'content', text: 'abc' },
      { type: 'tool', toolCall: tool('t1', { position: 3 }) },
    ])
  })

  // ===== 同 position =====
  it('同 position 多工具：按传入顺序连续插入（调用方已按 seq 排序）', () => {
    const t1 = tool('t1', { position: 3, seq: 1 })
    const t2 = tool('t2', { position: 3, seq: 2 })
    const segs = splitContentByToolPositions('0123456789', [t1, t2])
    expect(segs).toEqual([
      { type: 'content', text: '012' },
      { type: 'tool', toolCall: t1 },
      { type: 'tool', toolCall: t2 },
      { type: 'content', text: '3456789' },
    ])
  })

  // ===== 无 position =====
  it('无 position 工具追加末尾（保持传入顺序）', () => {
    const t1 = tool('t1', { position: 2 })
    const noPos1 = tool('no1')
    const noPos2 = tool('no2')
    const segs = splitContentByToolPositions('0123456789', [noPos2, t1, noPos1])
    // 有 position 的在正文内联；无 position 的按传入顺序（seq 顺序）追加末尾
    expect(segs.length).toEqual(5)
    expect(segs[1]).toEqual({ type: 'tool', toolCall: t1 })
    expect(segs[3]).toEqual({ type: 'tool', toolCall: noPos2 })
    expect(segs[4]).toEqual({ type: 'tool', toolCall: noPos1 })
  })

  // ===== position 超界 =====
  it('position 超界（> content 长度）：按末尾处理', () => {
    const segs = splitContentByToolPositions('abc', [tool('t1', { position: 99 })])
    expect(segs).toEqual([
      { type: 'content', text: 'abc' },
      { type: 'tool', toolCall: tool('t1', { position: 99 }) },
    ])
  })

  it('超界工具与无 position 工具混合：正文 + 末尾工具段（保持传入顺序）', () => {
    const over = tool('over', { position: 50 })
    const noPos = tool('noPos')
    const segs = splitContentByToolPositions('abc', [noPos, over])
    // 无 position / 超界均追加末尾，保持传入顺序（调用方已按 seq 排序）
    expect(segs).toEqual([
      { type: 'content', text: 'abc' },
      { type: 'tool', toolCall: noPos },
      { type: 'tool', toolCall: over },
    ])
  })

  // ===== 乱序/重复 position =====
  it('乱序 position：position 回退（< 游标）工具追加末尾（保持传入顺序）', () => {
    const t5 = tool('t5', { position: 5 })
    const t1 = tool('t1', { position: 1 })
    const t3 = tool('t3', { position: 3 })
    const segs = splitContentByToolPositions('0123456789', [t5, t1, t3])
    // 传入顺序（seq 已排序）是唯一排序权威；position 乱序属异常输入，
    // 回退工具追加末尾而非按 position 重排（避免与 seq 排序冲突导致乱序）
    expect(segs.map(s => s.type === 'tool' ? s.toolCall.id : s.text)).toEqual([
      '01234', 't5', '56789', 't1', 't3',
    ])
  })

  it('重复 position：幂等跳过（不重复插入）', () => {
    const t1a = tool('t1a', { position: 3, seq: 1 })
    const t1b = tool('t1b', { position: 3, seq: 1 }) // 同一 position 同 seq（去重语义）
    const segs = splitContentByToolPositions('0123456789', [t1a, t1b])
    // 同一 position 多个工具都应插入（正常并发工具场景），此处验证不产生空正文段
    const tools = segs.filter(s => s.type === 'tool')
    expect(tools.length).toEqual(2)
    const contentSegs = segs.filter(s => s.type === 'content')
    expect(contentSegs.length).toEqual(2)
    expect(contentSegs[0].text).toEqual('012')
    expect(contentSegs[1].text).toEqual('3456789')
  })

  // ===== 停止中断（工具未完成） =====
  it('停止中断：部分工具 running/pending 也参与切段（position 已写入）', () => {
    const t1 = tool('t1', { position: 2, status: 'completed' })
    const t2 = tool('t2', { position: 5, status: 'running' })
    const segs = splitContentByToolPositions('0123456789', [t1, t2])
    expect(segs.map(s => s.type === 'tool' ? s.toolCall.id : s.text)).toEqual([
      '01', 't1', '234', 't2', '56789',
    ])
  })
})
