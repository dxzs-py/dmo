/**
 * toolCallTree 单元测试（Task 6：子代理递归嵌套式容器树）
 *
 * 覆盖：ID 提取、agentPath 兜底、分组统计/状态优先级、agentPath 分组稳定性、
 * parentToolCallId 递归建树（含孤儿提升）、子代理组查找、根组过滤、终态判定。
 *
 * 运行方式（项目 package.json 为 "type": "module"，使用 Node 内置 assert，无需第三方框架）：
 *   node src/utils/__tests__/toolCallTree.test.js
 */
import { strict as assert } from 'node:assert'
import {
  getToolCallId,
  getAgentPath,
  computeGroupStats,
  computeGroupDurationMs,
  computeGroupStatus,
  formatDuration,
  buildAgentGroups,
  buildToolTree,
  findChildGroupsByTool,
  buildRootGroups,
  isTerminalToolStatus,
} from '../toolCallTree.js'

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

console.log('toolCallTree 单元测试\n')

// ===== 模拟数据：main agent 触发 web-researcher 子代理，后者再触发 analyst 子代理 =====
const mainTask = { id: 'a1', toolCallId: undefined, name: 'task', status: 'completed', agentPath: ['main'], parentToolCallId: '' }
const mainGrep = { id: 'a2', name: 'grep', status: 'completed', agentPath: ['main'], parentToolCallId: '' }
const subWrite = { id: 'b1', name: 'write_file', status: 'completed', agentPath: ['main', 'web-researcher'], parentToolCallId: 'a1' }
const subExec = { id: 'b2', name: 'execute', status: 'running', agentPath: ['main', 'web-researcher'], parentToolCallId: 'a1' }
const deepTask = { id: 'c1', name: 'task', status: 'completed', agentPath: ['main', 'web-researcher', 'analyst'], parentToolCallId: 'b1' }

// ===== getToolCallId =====
test('getToolCallId：id 优先，toolCallId 兜底，空对象返回空串', () => {
  assert.equal(getToolCallId(mainTask), 'a1')
  assert.equal(getToolCallId({ toolCallId: 'x1' }), 'x1')
  assert.equal(getToolCallId({}), '')
  assert.equal(getToolCallId(null), '')
})

// ===== getAgentPath =====
test('getAgentPath：合法数组保留，缺失/非法归入 ["main"]', () => {
  assert.deepEqual(getAgentPath(subExec), ['main', 'web-researcher'])
  assert.deepEqual(getAgentPath({}), ['main'])
  assert.deepEqual(getAgentPath({ agentPath: null }), ['main'])
  assert.deepEqual(getAgentPath({ agentPath: [1, 2] }), ['1', '2'])
})

// ===== computeGroupStats =====
test('computeGroupStats：总数/完成/运行/失败/待执行统计正确（无时间戳时 durationMs 为 null）', () => {
  const stats = computeGroupStats([mainTask, mainGrep, subWrite, subExec, deepTask])
  assert.deepEqual(stats, { total: 5, completed: 4, failed: 0, running: 1, pending: 0, durationMs: null })
})

test('computeGroupStats：rejected/timeout 计入失败', () => {
  const stats = computeGroupStats([
    { id: 'x1', status: 'rejected' },
    { id: 'x2', status: 'timeout' },
    { id: 'x3', status: 'pending' },
  ])
  assert.deepEqual(stats, { total: 3, completed: 0, failed: 2, running: 0, pending: 1, durationMs: null })
})

// ===== computeGroupDurationMs（Task 4.1 耗时计算） =====
test('computeGroupDurationMs：min(createdAt) 与 max(completedAt) 之差', () => {
  const t0 = '2026-08-13T01:00:00.000Z'
  const t1 = '2026-08-13T01:00:05.000Z'
  const t2 = '2026-08-13T01:00:12.300Z'
  // 首个工具起始 t0，末个工具完成 t2 → 12300ms
  const ms = computeGroupDurationMs([
    { id: 'a', createdAt: t0, completedAt: t1 },
    { id: 'b', createdAt: t1, completedAt: t2 },
  ])
  assert.equal(ms, 12300)
})

test('computeGroupDurationMs：时间戳缺失（未完成组）返回 null', () => {
  const t0 = '2026-08-13T01:00:00.000Z'
  // 仅 createdAt 无 completedAt（运行中）→ 无法计算
  assert.equal(computeGroupDurationMs([{ id: 'a', createdAt: t0 }]), null)
  // 仅 completedAt 无 createdAt → 无法计算
  assert.equal(computeGroupDurationMs([{ id: 'a', completedAt: t0 }]), null)
  // 空数组 → null
  assert.equal(computeGroupDurationMs([]), null)
})

test('computeGroupDurationMs：跨工具聚合取组内最早起始与最晚完成', () => {
  const t0 = '2026-08-13T01:00:00.000Z'
  const t2 = '2026-08-13T01:00:10.000Z'
  const t3 = '2026-08-13T01:00:15.000Z'
  const ms = computeGroupDurationMs([
    { id: 'a', createdAt: t3, completedAt: t3 }, // 最后出现但起始最晚
    { id: 'b', createdAt: t0, completedAt: t2 }, // 组最早起始
    { id: 'c', createdAt: t2, completedAt: t3 }, // 组最晚完成
  ])
  assert.equal(ms, 15000)
})

// ===== formatDuration（Task 4.1 耗时格式化） =====
test('formatDuration：不足 1s 显示 "<1s"，其余保留 1 位小数', () => {
  assert.equal(formatDuration(500), '<1s')
  assert.equal(formatDuration(999), '<1s')
  assert.equal(formatDuration(12300), '12.3s')
  assert.equal(formatDuration(120000), '120.0s')
})

test('formatDuration：非法输入返回 null', () => {
  assert.equal(formatDuration(null), null)
  assert.equal(formatDuration(undefined), null)
  assert.equal(formatDuration(NaN), null)
  assert.equal(formatDuration(-1), null)
})

// ===== computeGroupStatus =====
test('computeGroupStatus：running 优先级最高', () => {
  assert.equal(computeGroupStatus([subWrite, subExec]), 'running')
})

test('computeGroupStatus：无运行中时 completed 优先于 failed', () => {
  assert.equal(computeGroupStatus([{ status: 'completed' }, { status: 'failed' }]), 'failed')
  assert.equal(computeGroupStatus([{ status: 'completed' }, { status: 'completed' }]), 'completed')
})

test('computeGroupStatus：空数组返回 pending', () => {
  assert.equal(computeGroupStatus([]), 'pending')
})

// ===== buildAgentGroups =====
test('buildAgentGroups：按 agentPath 分组且保持首次出现顺序', () => {
  const groups = buildAgentGroups([mainTask, subWrite, mainGrep, subExec, deepTask])
  assert.deepEqual(
    groups.map((g) => g.key),
    ['main', 'main>web-researcher', 'main>web-researcher>analyst'],
  )
  assert.equal(groups[0].agentName, 'main')
  assert.equal(groups[1].agentName, 'web-researcher')
  assert.equal(groups[1].toolCalls.length, 2)
  assert.equal(groups[2].toolCalls.length, 1)
})

test('buildAgentGroups：缺失 agentPath 归入 main 组', () => {
  const groups = buildAgentGroups([{ id: 'x1', status: 'completed' }])
  assert.equal(groups.length, 1)
  assert.deepEqual(groups[0].agentPath, ['main'])
})

// ===== buildAgentGroups description（Task 4.1/4.5 子代理任务目标描述） =====
test('buildAgentGroups：组 description 取组内首个携带 description 的工具值，无则空串', () => {
  const desc = '网络搜索和信息整理专家，负责从互联网搜索和整理研究信息'
  const withDesc = [
    { id: 'b1', status: 'completed', agentPath: ['main', 'web-researcher'], description: desc },
    { id: 'b2', status: 'completed', agentPath: ['main', 'web-researcher'] },
    { id: 'a1', status: 'completed', agentPath: ['main'] },
  ]
  const groups = buildAgentGroups(withDesc)
  assert.equal(groups.find((g) => g.key === 'main>web-researcher').description, desc)
  assert.equal(groups.find((g) => g.key === 'main').description, '')
})

// ===== buildToolTree =====
test('buildToolTree：同组父子关系递归建树', () => {
  const tree = buildToolTree([
    { id: 'p', parentToolCallId: '' },
    { id: 'c', parentToolCallId: 'p' },
    { id: 'gc', parentToolCallId: 'c' },
  ])
  assert.equal(tree.length, 1)
  assert.equal(tree[0].id, 'p')
  assert.equal(tree[0].children[0].id, 'c')
  assert.equal(tree[0].children[0].children[0].id, 'gc')
})

test('buildToolTree：parentToolCallId 指向组外工具时提升为根（不丢失）', () => {
  const tree = buildToolTree([subWrite, subExec])
  assert.equal(tree.length, 2)
  assert.ok(tree.every((n) => n.id === 'b1' || n.id === 'b2'))
})

test('buildToolTree：无 ID 工具作为根节点', () => {
  const tree = buildToolTree([{ name: 'x', status: 'completed' }])
  assert.equal(tree.length, 1)
  assert.deepEqual(tree[0].children, [])
})

// ===== findChildGroupsByTool =====
test('findChildGroupsByTool：找到由指定父工具触发的子代理组', () => {
  const groups = buildAgentGroups([mainTask, mainGrep, subWrite, subExec, deepTask])
  const childGroups = findChildGroupsByTool(groups, 'a1')
  assert.equal(childGroups.length, 1)
  assert.equal(childGroups[0].key, 'main>web-researcher')
})

test('findChildGroupsByTool：无匹配返回空数组', () => {
  const groups = buildAgentGroups([mainTask, mainGrep])
  assert.deepEqual(findChildGroupsByTool(groups, 'nope'), [])
  assert.deepEqual(findChildGroupsByTool(groups, ''), [])
})

// ===== buildRootGroups =====
test('buildRootGroups：子代理组被过滤，仅根组保留在顶层', () => {
  const groups = buildRootGroups([mainTask, mainGrep, subWrite, subExec, deepTask])
  assert.deepEqual(
    groups.map((g) => g.key),
    ['main'],
  )
})

test('buildRootGroups：无子代理时全部分组保留', () => {
  const groups = buildRootGroups([mainTask, mainGrep])
  assert.equal(groups.length, 1)
})

// ===== isTerminalToolStatus =====
test('isTerminalToolStatus：终态集合判定', () => {
  assert.equal(isTerminalToolStatus('completed'), true)
  assert.equal(isTerminalToolStatus('failed'), true)
  assert.equal(isTerminalToolStatus('rejected'), true)
  assert.equal(isTerminalToolStatus('timeout'), true)
  assert.equal(isTerminalToolStatus('running'), false)
  assert.equal(isTerminalToolStatus('waiting'), false)
  assert.equal(isTerminalToolStatus('pending'), false)
})

console.log(`\n结果：${passed} 通过，${failed} 失败`)
if (failed > 0) process.exit(1)
