/**
 * subagentAggregation 单元测试（聚合层顺序关联 Task 3）
 *
 * 覆盖：task 回退链（meta.task → spawn input.task（对象/JSON 字符串/threadId
 * 精确匹配/唯一 spawn 兜底/多 spawn 保守空串））、spawnToolCallId 仅来自 meta、
 * mapSubagentsBySpawnToolCall 分组与孤儿收集、formatSubagentTitle 截断与回退。
 *
 * 运行方式（Node 内置 assert，无需第三方框架）：
 *   node src/utils/__tests__/subagentAggregation.test.js
 */
import { strict as assert } from 'node:assert'
import {
  buildSubagentsFromMessage,
  mapSubagentsBySpawnToolCall,
  formatSubagentTitle,
} from '../subagentAggregation.js'

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

console.log('subagentAggregation 单元测试\n')

/** 构造 spawn_sub_agent 主 agent 工具调用 */
const spawnTool = (id, input, { subagentThreadId = '' } = {}) => ({
  id,
  toolCallId: id,
  name: 'spawn_sub_agent',
  input,
  subagentThreadId,
})

/** 构造普通子代理工具调用 */
const subTool = (id, threadId) => ({
  id,
  toolCallId: id,
  name: 'read_file',
  subagentThreadId: threadId,
})

// ===== buildSubagentsFromMessage：task 来源 =====

test('task 来源：meta.task 优先（spawn input 仅作回退）', () => {
  const message = {
    toolCalls: [spawnTool('spawn-1', { task: '回退任务', threadId: 't1' })],
    subagentContents: {},
  }
  const [sa] = buildSubagentsFromMessage(message, [
    { threadId: 't1', task: '元数据任务' },
  ])
  assert.equal(sa.task, '元数据任务')
})

test('task 回退：meta.task 缺失时取 spawn input.task（对象 + threadId 精确匹配）', () => {
  const message = {
    toolCalls: [
      spawnTool('spawn-1', { task: '搜索资料', threadId: 't1' }),
      spawnTool('spawn-2', { task: '整理文档', threadId: 't2' }),
      subTool('sub-1', 't1'),
    ],
    subagentContents: {},
  }
  const subagents = buildSubagentsFromMessage(message, [
    { threadId: 't1' },
    { threadId: 't2' },
  ])
  const byThread = Object.fromEntries(subagents.map(sa => [sa.threadId, sa]))
  assert.equal(byThread.t1.task, '搜索资料')
  assert.equal(byThread.t2.task, '整理文档')
})

test('task 回退：input 为 JSON 字符串时正确解析', () => {
  const message = {
    toolCalls: [
      spawnTool('spawn-1', JSON.stringify({ task: '字符串任务', threadId: 't1' })),
      subTool('sub-1', 't1'),
    ],
    subagentContents: {},
  }
  const [sa] = buildSubagentsFromMessage(message, [{ threadId: 't1' }])
  assert.equal(sa.task, '字符串任务')
})

test('task 回退：唯一 spawn 无 threadId 时归属唯一子代理', () => {
  const message = {
    toolCalls: [spawnTool('spawn-1', { task: '唯一任务' }), subTool('sub-1', 't1')],
    subagentContents: {},
  }
  const [sa] = buildSubagentsFromMessage(message, [{ threadId: 't1' }])
  assert.equal(sa.task, '唯一任务')
})

test('task 回退：多 spawn 无 thread 关联时保守返回空串（不错配）', () => {
  const message = {
    toolCalls: [
      spawnTool('spawn-1', { task: '任务A' }),
      spawnTool('spawn-2', { task: '任务B' }),
      subTool('sub-1', 't1'),
    ],
    subagentContents: {},
  }
  const [sa] = buildSubagentsFromMessage(message, [{ threadId: 't1' }])
  assert.equal(sa.task, '')
})

test('task 回退：spawn input 非法 JSON / 缺 task 字段 → 空串', () => {
  const message = {
    toolCalls: [
      spawnTool('spawn-1', '{not valid json'),
      spawnTool('spawn-2', { description: '无 task 字段' }),
      subTool('sub-1', 't1'),
    ],
    subagentContents: {},
  }
  const [sa] = buildSubagentsFromMessage(message, [{ threadId: 't1' }])
  assert.equal(sa.task, '')
})

test('task 回退：子代理内部的 spawn 工具不作为主 agent 回退源', () => {
  const message = {
    toolCalls: [
      // 一层子代理 t1 内部调用 spawn（subagentThreadId 非空，属嵌套层）
      spawnTool('spawn-nested', { task: '嵌套任务' }, { subagentThreadId: 't1' }),
      subTool('sub-1', 't1'),
    ],
    subagentContents: {},
  }
  const [sa] = buildSubagentsFromMessage(message, [{ threadId: 't1' }])
  assert.equal(sa.task, '')
})

test('spawnToolCallId 仅来自 meta.spawnToolCallId（无 meta 时空串）', () => {
  const message = {
    toolCalls: [spawnTool('spawn-1', { task: 'x' }), subTool('sub-1', 't1')],
    subagentContents: {},
  }
  const withMeta = buildSubagentsFromMessage(message, [
    { threadId: 't1', spawnToolCallId: 'spawn-1' },
  ])
  assert.equal(withMeta[0].spawnToolCallId, 'spawn-1')

  const withoutMeta = buildSubagentsFromMessage(message)
  assert.equal(withoutMeta[0].spawnToolCallId, '')
})

test('depth 采用后端序列化字段（meta.depth），缺失时兜底 1', () => {
  const message = {
    toolCalls: [subTool('sub-1', 't1'), subTool('sub-2', 't2')],
    subagentContents: {},
  }
  const list = buildSubagentsFromMessage(message, [
    { threadId: 't1', depth: 2 }, // 嵌套层（孙代理）
    { threadId: 't2' }, // 历史实例无 depth 字段
  ])
  assert.equal(list[0].depth, 2)
  assert.equal(list[1].depth, 1)
  // 全局序号与 depth 独立（顺序号仍从 1 递增）
  assert.equal(list[0].order, 1)
  assert.equal(list[1].order, 2)
})

// ===== mapSubagentsBySpawnToolCall =====

test('mapSubagentsBySpawnToolCall：按 spawnToolCallId 分组 + 孤儿收集', () => {
  const sa1 = { threadId: 't1', spawnToolCallId: 'spawn-1' }
  const sa2 = { threadId: 't2', spawnToolCallId: 'spawn-2' }
  const orphan = { threadId: 't3', spawnToolCallId: '' }
  const { bySpawnToolCallId, orphans } = mapSubagentsBySpawnToolCall([sa1, sa2, orphan])
  assert.equal(bySpawnToolCallId.size, 2)
  assert.equal(bySpawnToolCallId.get('spawn-1'), sa1)
  assert.equal(bySpawnToolCallId.get('spawn-2'), sa2)
  assert.deepEqual(orphans, [orphan])
})

test('mapSubagentsBySpawnToolCall：全部孤儿 / 空输入安全', () => {
  const { bySpawnToolCallId, orphans } = mapSubagentsBySpawnToolCall([
    { threadId: 't1' },
    { threadId: 't2', spawnToolCallId: undefined },
  ])
  assert.equal(bySpawnToolCallId.size, 0)
  assert.equal(orphans.length, 2)

  const empty = mapSubagentsBySpawnToolCall([])
  assert.equal(empty.bySpawnToolCallId.size, 0)
  assert.equal(empty.orphans.length, 0)

  const nullInput = mapSubagentsBySpawnToolCall(null)
  assert.equal(nullInput.bySpawnToolCallId.size, 0)
  assert.equal(nullInput.orphans.length, 0)
})

// ===== formatSubagentTitle =====

test('formatSubagentTitle：task 非空且不超长 → 原样返回（含首尾空白修剪）', () => {
  assert.equal(formatSubagentTitle('搜索最新论文', 'researcher'), '搜索最新论文')
  assert.equal(formatSubagentTitle('  带空白  ', 'researcher'), '带空白')
})

test('formatSubagentTitle：task 超过 maxLength → 截断加省略号', () => {
  const longTask = 'a'.repeat(31)
  assert.equal(formatSubagentTitle(longTask, 'name'), `${'a'.repeat(30)}…`)
  // 恰好等于 maxLength 不截断
  assert.equal(formatSubagentTitle('b'.repeat(30), 'name'), 'b'.repeat(30))
})

test('formatSubagentTitle：自定义 maxLength', () => {
  assert.equal(formatSubagentTitle('abcdef', 'name', 3), 'abc…')
  assert.equal(formatSubagentTitle('abc', 'name', 3), 'abc')
})

test('formatSubagentTitle：task 空 → agentName → 子代理 回退链', () => {
  assert.equal(formatSubagentTitle('', 'web-researcher'), 'web-researcher')
  assert.equal(formatSubagentTitle(undefined, 'web-researcher'), 'web-researcher')
  assert.equal(formatSubagentTitle('', ''), '子代理')
  assert.equal(formatSubagentTitle(null, undefined), '子代理')
  // task 为纯空白视为空
  assert.equal(formatSubagentTitle('   ', 'name'), 'name')
})

console.log(`\nsubagentAggregation 测试完成: ${passed} 通过, ${failed} 失败\n`)
if (failed > 0) process.exit(1)
