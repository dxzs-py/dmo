/**
 * messageOperations.finalizeToolCallsInMap 单元测试
 *
 * 覆盖（P3-34/P3-35 根因修复）：最终化时必须跳过「审批未放行执行」的工具
 * （approval.state 非 approved/completed），防止审批等待/拒绝/超时期间
 * 工具被误标为 COMPLETED（审批按钮因 status 终态消失）。
 *
 * 运行方式（项目 package.json 为 "type": "module"，使用 Node 内置 assert，无需第三方框架）：
 *   node src/utils/__tests__/messageOperations.test.js
 */
import { strict as assert } from 'node:assert'
import { ToolCallStatus, ApprovalState } from '../../types/index.js'
import { finalizeToolCallsInMap, sortToolCallsForDisplay, _mergeToolCalls } from '../messageOperations.js'

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

/** 构造包含单个 toolCall 的 Map */
function makeMap(id, toolCall) {
  const map = new Map()
  map.set(id, toolCall)
  return map
}

function baseToolCall(overrides = {}) {
  return {
    id: 'call_1',
    toolCallId: 'call_1',
    name: 'execute',
    status: ToolCallStatus.PENDING,
    ...overrides,
  }
}

console.log('\n=== finalizeToolCallsInMap ===')

test('无审批的非终态工具被兜底为 COMPLETED', () => {
  const map = makeMap('call_1', baseToolCall({ status: ToolCallStatus.PENDING }))
  const count = finalizeToolCallsInMap(map)
  assert.equal(count, 1)
  assert.equal(map.get('call_1').status, ToolCallStatus.COMPLETED)
})

test('无审批的 running 工具被兜底为 COMPLETED（审批通过但 completed 事件丢失）', () => {
  const map = makeMap('call_1', baseToolCall({ status: ToolCallStatus.RUNNING }))
  finalizeToolCallsInMap(map)
  assert.equal(map.get('call_1').status, ToolCallStatus.COMPLETED)
})

test('审批 pending 的工具不被最终化（P3-34/P3-35 根因）', () => {
  const tc = baseToolCall({
    status: ToolCallStatus.PENDING,
    approval: { state: ApprovalState.PENDING },
  })
  const map = makeMap('call_1', tc)
  const count = finalizeToolCallsInMap(map)
  assert.equal(count, 0)
  assert.equal(tc.status, ToolCallStatus.PENDING)
  assert.equal(tc.approval.state, ApprovalState.PENDING)
})

test('审批 waiting 的工具不被最终化', () => {
  const tc = baseToolCall({
    status: ToolCallStatus.WAITING,
    approval: { state: ApprovalState.WAITING },
  })
  const map = makeMap('call_1', tc)
  finalizeToolCallsInMap(map)
  assert.equal(tc.status, ToolCallStatus.WAITING)
  assert.equal(tc.approval.state, ApprovalState.WAITING)
})

test('审批 processing 的工具不被最终化', () => {
  const tc = baseToolCall({
    status: ToolCallStatus.PENDING,
    approval: { state: ApprovalState.PROCESSING },
  })
  const map = makeMap('call_1', tc)
  finalizeToolCallsInMap(map)
  assert.equal(tc.status, ToolCallStatus.PENDING)
  assert.equal(tc.approval.state, ApprovalState.PROCESSING)
})

test('审批 rejected 的工具不被兜底为 COMPLETED（防止误标"已完成"）', () => {
  const tc = baseToolCall({
    status: ToolCallStatus.PENDING,
    approval: { state: ApprovalState.REJECTED },
  })
  const map = makeMap('call_1', tc)
  const count = finalizeToolCallsInMap(map)
  assert.equal(count, 0)
  assert.equal(tc.status, ToolCallStatus.PENDING)
  assert.equal(tc.approval.state, ApprovalState.REJECTED)
})

test('审批 timeout 的工具不被兜底为 COMPLETED', () => {
  const tc = baseToolCall({
    status: ToolCallStatus.PENDING,
    approval: { state: ApprovalState.TIMEOUT },
  })
  const map = makeMap('call_1', tc)
  finalizeToolCallsInMap(map)
  assert.equal(tc.status, ToolCallStatus.PENDING)
})

test('审批 approved 且工具非终态 → 兜底为 COMPLETED（审批已放行执行）', () => {
  const tc = baseToolCall({
    status: ToolCallStatus.RUNNING,
    approval: { state: ApprovalState.APPROVED },
  })
  const map = makeMap('call_1', tc)
  const count = finalizeToolCallsInMap(map)
  assert.equal(count, 1)
  assert.equal(tc.status, ToolCallStatus.COMPLETED)
})

test('审批 completed 且工具非终态 → 兜底为 COMPLETED', () => {
  const tc = baseToolCall({
    status: ToolCallStatus.RUNNING,
    approval: { state: 'completed' },
  })
  const map = makeMap('call_1', tc)
  finalizeToolCallsInMap(map)
  assert.equal(tc.status, ToolCallStatus.COMPLETED)
})

test('终态工具不被回退（已完成保持）', () => {
  const tc = baseToolCall({
    status: ToolCallStatus.COMPLETED,
    approval: null,
  })
  const map = makeMap('call_1', tc)
  const count = finalizeToolCallsInMap(map)
  assert.equal(count, 0)
  assert.equal(tc.status, ToolCallStatus.COMPLETED)
})

test('messageBackendId 过滤：仅最终化目标消息的工具', () => {
  const tcA = baseToolCall({ id: 'call_a', toolCallId: 'call_a', messageBackendId: 100, status: ToolCallStatus.PENDING })
  const tcB = baseToolCall({ id: 'call_b', toolCallId: 'call_b', messageBackendId: 200, status: ToolCallStatus.PENDING })
  const map = new Map()
  map.set('call_a', tcA)
  map.set('call_b', tcB)
  const count = finalizeToolCallsInMap(map, 100)
  assert.equal(count, 1)
  assert.equal(tcA.status, ToolCallStatus.COMPLETED)
  assert.equal(tcB.status, ToolCallStatus.PENDING)
})

test('空 Map 返回 0', () => {
  assert.equal(finalizeToolCallsInMap(new Map()), 0)
})

test('混合场景：仅审批未放行工具被跳过，其余兜底', () => {
  const pendingApproval = baseToolCall({
    id: 'call_1',
    toolCallId: 'call_1',
    status: ToolCallStatus.PENDING,
    approval: { state: ApprovalState.PENDING },
  })
  const plain = baseToolCall({ id: 'call_2', toolCallId: 'call_2', status: ToolCallStatus.WAITING })
  const map = new Map()
  map.set('call_1', pendingApproval)
  map.set('call_2', plain)
  const count = finalizeToolCallsInMap(map)
  assert.equal(count, 1)
  assert.equal(pendingApproval.status, ToolCallStatus.PENDING)
  assert.equal(pendingApproval.approval.state, ApprovalState.PENDING)
  assert.equal(plain.status, ToolCallStatus.COMPLETED)
})

console.log('\n=== sortToolCallsForDisplay（seq 主 + 数组兜底） ===')

test('全部条目有 seq → 按 seq 升序排序', () => {
  const arr = [
    { id: 'fs_write_file', seq: 3 },
    { id: 'shell_exec_1', seq: 1 },
    { id: 'shell_exec_2', seq: 2 },
  ]
  sortToolCallsForDisplay(arr)
  assert.deepEqual(arr.map(t => t.id), ['shell_exec_1', 'shell_exec_2', 'fs_write_file'])
})

test('全部条目无 seq → 保持原数组顺序（回归修复：不再把无 seq 按 MAX 排后）', () => {
  const arr = [
    { id: 'shell_exec_1' },
    { id: 'shell_exec_2' },
    { id: 'fs_write_file' },
  ]
  sortToolCallsForDisplay(arr)
  assert.deepEqual(arr.map(t => t.id), ['shell_exec_1', 'shell_exec_2', 'fs_write_file'])
})

test('部分条目无 seq（混合数据）→ 保持原数组顺序（seq 不完整时无法比较，退回数组兜底）', () => {
  const arr = [
    { id: 'shell_exec_1' },
    { id: 'shell_exec_2' },
    { id: 'fs_write_file', seq: 3 },
  ]
  sortToolCallsForDisplay(arr)
  assert.deepEqual(arr.map(t => t.id), ['shell_exec_1', 'shell_exec_2', 'fs_write_file'])
})

test('seq 含非正整数（0/负数）→ 视为无 seq，保持数组顺序', () => {
  const arr = [
    { id: 'a', seq: 0 },
    { id: 'b', seq: -1 },
    { id: 'c' },
  ]
  sortToolCallsForDisplay(arr)
  assert.deepEqual(arr.map(t => t.id), ['a', 'b', 'c'])
})

test('空数组与单元素 → 原样返回', () => {
  const empty = []
  sortToolCallsForDisplay(empty)
  assert.deepEqual(empty, [])
  const single = [{ id: 'x', seq: 5 }]
  sortToolCallsForDisplay(single)
  assert.deepEqual(single.map(t => t.id), ['x'])
})

console.log('\n=== _mergeToolCalls（position 保护 / 同名多工具按 id 匹配） ===')

test('后端缺失 position：保留本地 number position', () => {
  const local = [{ id: 'spawn_1', name: 'spawn_sub_agent', position: 38 }]
  const backend = [{ id: 'spawn_1', name: 'spawn_sub_agent' }]
  const merged = _mergeToolCalls(local, backend)
  assert.equal(merged[0].position, 38)
})

test('position=0（本地）不被后端 undefined 覆盖', () => {
  const local = [{ id: 'wait_1', name: 'wait_for_subagent', position: 0 }]
  const backend = [{ id: 'wait_1', name: 'wait_for_subagent' }]
  const merged = _mergeToolCalls(local, backend)
  assert.equal(merged[0].position, 0)
})

test('position=0（后端）覆盖本地缺失的 position', () => {
  const local = [{ id: 'wait_1', name: 'wait_for_subagent' }]
  const backend = [{ id: 'wait_1', name: 'wait_for_subagent', position: 0 }]
  const merged = _mergeToolCalls(local, backend)
  assert.equal(merged[0].position, 0)
})

test('同名多工具（两个 spawn_sub_agent）按 id 精确匹配，各自 position 不丢失', () => {
  const local = [
    { id: 'spawn_1', name: 'spawn_sub_agent', position: 38 },
    { id: 'spawn_2', name: 'spawn_sub_agent', position: 38 },
  ]
  const backend = [
    { id: 'spawn_1', name: 'spawn_sub_agent', position: 38 },
    { id: 'spawn_2', name: 'spawn_sub_agent' },
  ]
  const merged = _mergeToolCalls(local, backend)
  const spawn1 = merged.find(t => t.id === 'spawn_1')
  const spawn2 = merged.find(t => t.id === 'spawn_2')
  assert.equal(spawn1.position, 38)
  assert.equal(spawn2.position, 38)
})

test('后端独有条目 position=0 原样保留（本地为空列表）', () => {
  const merged = _mergeToolCalls([], [{ id: 'wait_1', name: 'wait_for_subagent', position: 0 }])
  assert.equal(merged[0].position, 0)
})

test('后端缺失 subagentThreadId：保留本地事件已写入的图层字段', () => {
  const local = [{ id: 'sh_1', name: 'shell_exec', subagentThreadId: 'subagent_abc', agentName: 'ollama-list', depth: 1 }]
  const backend = [{ id: 'sh_1', name: 'shell_exec' }]
  const merged = _mergeToolCalls(local, backend)
  assert.equal(merged[0].subagentThreadId, 'subagent_abc')
  assert.equal(merged[0].agentName, 'ollama-list')
  assert.equal(merged[0].depth, 1)
})

test('后端缺失 subagentThreadId 且本地为空字符串：不误回填（空串原样保留）', () => {
  const local = [{ id: 'sh_1', name: 'shell_exec', subagentThreadId: '' }]
  const backend = [{ id: 'sh_1', name: 'shell_exec' }]
  const merged = _mergeToolCalls(local, backend)
  assert.equal(merged[0].subagentThreadId, '')
})

console.log(`\n结果: ${passed} 通过, ${failed} 失败`)
if (failed > 0) process.exit(1)
