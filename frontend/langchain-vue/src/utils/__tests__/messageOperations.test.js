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
import { finalizeToolCallsInMap, addOrUpdateToolCallInLastMessage, sortToolCallsForDisplay } from '../messageOperations.js'

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

// ==================== PENDING 锁死保护（_mergeExistingToolCall 共用实现） ====================

/** 构造含单条 pending 审批工具消息的 sessions */
function makeSessionWithPendingApprovalTool() {
  return [{
    id: 's1',
    messages: [{
      id: 'm1',
      backendId: 100,
      role: 'assistant',
      content: '',
      toolCalls: [{
        id: 'call_1',
        toolCallId: 'call_1',
        name: 'shell_exec',
        status: ToolCallStatus.PENDING,
        approval: { state: ApprovalState.PENDING, toolName: 'shell_exec' },
      }],
    }],
  }]
}

console.log('\n=== PENDING 锁死保护（tool_call_waiting 放行） ===')

test('tool_call_waiting 放行：pending + 审批非终态 → 推进到 waiting（P3-5 类根因修复）', () => {
  const sessions = makeSessionWithPendingApprovalTool()
  addOrUpdateToolCallInLastMessage(sessions, 's1', {
    id: 'call_1',
    name: 'shell_exec',
    status: ToolCallStatus.WAITING,
  })
  assert.equal(sessions[0].messages[0].toolCalls[0].status, ToolCallStatus.WAITING)
  // approval 保持非终态（不被覆盖）
  assert.equal(sessions[0].messages[0].toolCalls[0].approval.state, ApprovalState.PENDING)
})

test('tool_call_running 仍被拦截：pending + 审批非终态 → 保持 pending（P3-7/P3-19 保护保留）', () => {
  const sessions = makeSessionWithPendingApprovalTool()
  addOrUpdateToolCallInLastMessage(sessions, 's1', {
    id: 'call_1',
    name: 'shell_exec',
    status: ToolCallStatus.RUNNING,
  })
  assert.equal(sessions[0].messages[0].toolCalls[0].status, ToolCallStatus.PENDING)
})

test('审批已确认（approved）后 running 放行', () => {
  const sessions = makeSessionWithPendingApprovalTool()
  sessions[0].messages[0].toolCalls[0].approval.state = ApprovalState.APPROVED
  addOrUpdateToolCallInLastMessage(sessions, 's1', {
    id: 'call_1',
    name: 'shell_exec',
    status: ToolCallStatus.RUNNING,
  })
  assert.equal(sessions[0].messages[0].toolCalls[0].status, ToolCallStatus.RUNNING)
})

test('无审批工具正常推进：pending → waiting', () => {
  const sessions = [{
    id: 's1',
    messages: [{
      id: 'm1',
      backendId: 100,
      role: 'assistant',
      content: '',
      toolCalls: [{
        id: 'call_1',
        toolCallId: 'call_1',
        name: 'shell_exec',
        status: ToolCallStatus.PENDING,
      }],
    }],
  }]
  addOrUpdateToolCallInLastMessage(sessions, 's1', {
    id: 'call_1',
    name: 'shell_exec',
    status: ToolCallStatus.WAITING,
  })
  assert.equal(sessions[0].messages[0].toolCalls[0].status, ToolCallStatus.WAITING)
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

console.log(`\n结果: ${passed} 通过, ${failed} 失败`)
if (failed > 0) process.exit(1)
