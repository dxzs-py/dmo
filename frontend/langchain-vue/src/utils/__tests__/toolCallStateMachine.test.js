/**
 * toolCallStateMachine 单元测试（工具状态机统一，Task 2）
 *
 * 覆盖：状态域/事件态常量、canTransition 合法/非法转换、
 * applyToolCallState（合法推进、终态不回退、failed/timeout、非法转换拒绝、add/result 模式）、
 * deriveDisplayStatus 各分支、isTerminalStatus。
 *
 * 运行方式（项目 package.json 为 "type": "module"，使用 Node 内置 assert，无需第三方框架）：
 *   node src/utils/__tests__/toolCallStateMachine.test.js
 */
import { strict as assert } from 'node:assert'
import {
  ToolCallStatus,
  TOOL_CALL_EVENT_STATES,
  EVENT_STATE_TO_STATUS,
  TERMINAL_STATUSES,
  NON_TERMINAL_STATUSES,
  canTransition,
  isTerminalStatus,
  getToolCallStatusPriority,
  applyToolCallState,
  deriveDisplayStatus,
  approvalStateToToolStatus,
} from '../toolCallStateMachine.js'

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

console.log('toolCallStateMachine 单元测试\n')

// ==================== 状态域 / 事件态常量 ====================

test('状态域常量：pending/waiting/running/completed/failed/timeout（含 rejected）', () => {
  assert.strictEqual(ToolCallStatus.PENDING, 'pending')
  assert.strictEqual(ToolCallStatus.WAITING, 'waiting')
  assert.strictEqual(ToolCallStatus.RUNNING, 'running')
  assert.strictEqual(ToolCallStatus.COMPLETED, 'completed')
  assert.strictEqual(ToolCallStatus.FAILED, 'failed')
  assert.strictEqual(ToolCallStatus.TIMEOUT, 'timeout')
  assert.strictEqual(ToolCallStatus.REJECTED, 'rejected')
})

test('事件态常量：input-available/input-error/output-available/output-error/interrupt', () => {
  assert.strictEqual(TOOL_CALL_EVENT_STATES.INPUT_AVAILABLE, 'input-available')
  assert.strictEqual(TOOL_CALL_EVENT_STATES.INPUT_ERROR, 'input-error')
  assert.strictEqual(TOOL_CALL_EVENT_STATES.OUTPUT_AVAILABLE, 'output-available')
  assert.strictEqual(TOOL_CALL_EVENT_STATES.OUTPUT_ERROR, 'output-error')
  assert.strictEqual(TOOL_CALL_EVENT_STATES.INTERRUPT, 'interrupt')
})

test('事件态→状态映射：对齐后端 _STATE_TO_STATUS 语义', () => {
  assert.strictEqual(EVENT_STATE_TO_STATUS['input-available'], ToolCallStatus.PENDING)
  assert.strictEqual(EVENT_STATE_TO_STATUS['output-available'], ToolCallStatus.COMPLETED)
  assert.strictEqual(EVENT_STATE_TO_STATUS['output-error'], ToolCallStatus.FAILED)
  assert.strictEqual(EVENT_STATE_TO_STATUS['input-error'], ToolCallStatus.FAILED)
  assert.strictEqual(EVENT_STATE_TO_STATUS['interrupt'], ToolCallStatus.WAITING)
})

// ==================== 终态 / 优先级 ====================

test('isTerminalStatus / TERMINAL_STATUSES：终态判定', () => {
  for (const s of ['completed', 'failed', 'timeout', 'rejected']) {
    assert.ok(isTerminalStatus(s), `${s} 应为终态`)
    assert.ok(TERMINAL_STATUSES.has(s))
  }
  for (const s of ['pending', 'waiting', 'running']) {
    assert.ok(!isTerminalStatus(s), `${s} 不应为终态`)
    assert.ok(!TERMINAL_STATUSES.has(s))
  }
})

test('NON_TERMINAL_STATUSES：非终态集合', () => {
  assert.deepStrictEqual(
    [...NON_TERMINAL_STATUSES].sort(),
    ['pending', 'running', 'waiting']
  )
})

test('getToolCallStatusPriority：终态优先级最高，pending 最低', () => {
  assert.strictEqual(getToolCallStatusPriority('pending'), 0)
  assert.strictEqual(getToolCallStatusPriority('waiting'), 1)
  assert.strictEqual(getToolCallStatusPriority('running'), 2)
  assert.strictEqual(getToolCallStatusPriority('completed'), 3)
  assert.strictEqual(getToolCallStatusPriority('failed'), 3)
  assert.strictEqual(getToolCallStatusPriority('timeout'), 3)
  assert.strictEqual(getToolCallStatusPriority('rejected'), 3)
})

// ==================== canTransition ====================

test('canTransition 合法转换：pending→waiting→running→completed', () => {
  assert.ok(canTransition('pending', 'waiting'))
  assert.ok(canTransition('pending', 'running'))
  assert.ok(canTransition('waiting', 'running'))
  assert.ok(canTransition('running', 'completed'))
  assert.ok(canTransition('waiting', 'completed'))
})

test('canTransition 合法转换：failed / timeout / rejected', () => {
  assert.ok(canTransition('pending', 'failed'))
  assert.ok(canTransition('pending', 'timeout'))
  assert.ok(canTransition('pending', 'rejected')) // 对齐后端 _VALID_TRANSITIONS：REJECTED 前驱含 PENDING
  assert.ok(canTransition('waiting', 'failed'))
  assert.ok(canTransition('waiting', 'timeout'))
  assert.ok(canTransition('waiting', 'rejected'))
  assert.ok(canTransition('running', 'failed'))
  assert.ok(canTransition('running', 'timeout'))
})

test('canTransition 非法转换：终态不可回退', () => {
  assert.ok(!canTransition('completed', 'pending'))
  assert.ok(!canTransition('completed', 'running'))
  assert.ok(!canTransition('failed', 'pending'))
  assert.ok(!canTransition('timeout', 'running'))
  assert.ok(!canTransition('rejected', 'waiting'))
})

test('canTransition 非法转换：running 不可回退 pending（事件乱序防御）', () => {
  assert.ok(!canTransition('running', 'pending'))
  assert.ok(!canTransition('running', 'waiting'))
  assert.ok(!canTransition('running', 'rejected')) // 后端：执行中的工具不可被拒绝
})

test('canTransition 同状态幂等', () => {
  assert.ok(canTransition('pending', 'pending'))
  assert.ok(canTransition('completed', 'completed'))
})

test('canTransition 非法转换：waiting 不可被 SSE tool 事件(pending) 回退（Task 7 场景）', () => {
  // 触发浏览器同时接收 SSE tool 事件(status=pending) 与 WS tool_call_waiting(status=waiting)：
  // waiting（已审批/等待同批）不能被 SSE 的 pending 事件回退，保持 waiting 显示"待审批"
  assert.ok(!canTransition('waiting', 'pending'))
  assert.ok(canTransition('waiting', 'running')) // waiting→running 合法：同批全部审批完成后开始执行
})

test('applyToolCallState 场景：waiting 收到 SSE pending 事件保持 waiting（Task 7 场景）', () => {
  const tc = { status: 'waiting' }
  const r = applyToolCallState(tc, { status: 'pending' }, { mode: 'add' })
  assert.ok(!r.applied)
  assert.strictEqual(r.status, 'waiting')
})

// ==================== approvalStateToToolStatus ====================

test('approvalStateToToolStatus：待审批/等待同批不映射为 running（Task 7 P3-19 快照恢复根因）', () => {
  // 快照恢复 / 审批面板兜底展示：非终态审批不得显示"执行中"
  assert.strictEqual(approvalStateToToolStatus('pending'), 'pending')
  assert.strictEqual(approvalStateToToolStatus('waiting'), 'waiting')
  // 已通过审批：工具开始执行 → running
  assert.strictEqual(approvalStateToToolStatus('processing'), 'running')
  assert.strictEqual(approvalStateToToolStatus('approved'), 'running')
  // 终态透传
  assert.strictEqual(approvalStateToToolStatus('rejected'), 'rejected')
  assert.strictEqual(approvalStateToToolStatus('timeout'), 'timeout')
  // 未知/缺失状态：保守默认 running（对齐旧映射行为）
  assert.strictEqual(approvalStateToToolStatus(undefined), 'running')
  assert.strictEqual(approvalStateToToolStatus(''), 'running')
})

// ==================== applyToolCallState ====================

test('applyToolCallState 合法推进：pending→waiting→running→completed', () => {
  let tc = { status: 'pending' }
  let r = applyToolCallState(tc, { status: 'waiting' }, { mode: 'add' })
  assert.ok(r.applied)
  tc.status = r.status
  assert.strictEqual(tc.status, 'waiting')
  r = applyToolCallState(tc, { status: 'running' }, { mode: 'add' })
  assert.ok(r.applied)
  tc.status = r.status
  assert.strictEqual(tc.status, 'running')
  r = applyToolCallState(tc, { status: 'completed' }, { mode: 'result' })
  assert.ok(r.applied)
  tc.status = r.status
  assert.strictEqual(tc.status, 'completed')
})

test('applyToolCallState 终态不回退：completed 后收到 pending/running 保持不变', () => {
  const tc = { status: 'completed' }
  const r1 = applyToolCallState(tc, { status: 'pending' }, { mode: 'add' })
  assert.ok(!r1.applied)
  assert.strictEqual(r1.status, 'completed')
  const r2 = applyToolCallState(tc, { status: 'running' }, { mode: 'add' })
  assert.ok(!r2.applied)
  assert.strictEqual(r2.status, 'completed')
  const r3 = applyToolCallState(tc, { status: 'failed' }, { mode: 'result' })
  assert.ok(!r3.applied)
  assert.strictEqual(r3.status, 'completed')
})

test('applyToolCallState failed/timeout 转换', () => {
  // pending → failed
  let r = applyToolCallState({ status: 'pending' }, { status: 'failed' }, { mode: 'result' })
  assert.ok(r.applied)
  assert.strictEqual(r.status, 'failed')
  // running → timeout
  r = applyToolCallState({ status: 'running' }, { status: 'timeout' }, { mode: 'result' })
  assert.ok(r.applied)
  assert.strictEqual(r.status, 'timeout')
  // waiting → failed（审批等待中失败）
  r = applyToolCallState({ status: 'waiting' }, { status: 'failed' }, { mode: 'result' })
  assert.ok(r.applied)
  assert.strictEqual(r.status, 'failed')
})

test('applyToolCallState 非法转换拒绝：running 被旧事件回退 pending 保持 running', () => {
  const tc = { status: 'running' }
  const r = applyToolCallState(tc, { status: 'pending' }, { mode: 'add' })
  assert.ok(!r.applied)
  assert.strictEqual(r.status, 'running')
})

test('applyToolCallState 新建（existing=null）：直接应用推导状态', () => {
  const r = applyToolCallState(null, { status: 'pending' }, { mode: 'add' })
  assert.ok(r.applied)
  assert.strictEqual(r.status, 'pending')
})

test('applyToolCallState add 模式：显式 status 优先，state 映射兜底 pending', () => {
  // 显式 status
  let r = applyToolCallState(null, { status: 'running', state: 'input-available' }, { mode: 'add' })
  assert.strictEqual(r.status, 'running')
  // 无 status、有 state → 事件态映射
  r = applyToolCallState(null, { state: 'input-available' }, { mode: 'add' })
  assert.strictEqual(r.status, 'pending')
  r = applyToolCallState(null, { state: 'output-available' }, { mode: 'add' })
  assert.strictEqual(r.status, 'completed')
  r = applyToolCallState(null, { state: 'output-error' }, { mode: 'add' })
  assert.strictEqual(r.status, 'failed')
  // 无 status、无 state → 不设置（fallback undefined）
  r = applyToolCallState(null, { name: 'ls' }, { mode: 'add' })
  assert.ok(!r.applied)
  assert.strictEqual(r.status, undefined)
})

test('applyToolCallState result 模式：result→completed / error→failed 推导', () => {
  // result 有值 → COMPLETED
  let r = applyToolCallState(null, { result: 'ok' }, { mode: 'result' })
  assert.strictEqual(r.status, 'completed')
  // error 有值 → FAILED
  r = applyToolCallState(null, { error: 'boom' }, { mode: 'result' })
  assert.strictEqual(r.status, 'failed')
  // state=output-error 时 result 不触发 COMPLETED（错误输出仍为失败态）
  r = applyToolCallState(null, { state: 'output-error', result: 'partial' }, { mode: 'result' })
  assert.strictEqual(r.status, 'failed')
  // 无任何可推导字段 → fallback
  r = applyToolCallState(null, { name: 'ls' }, { mode: 'result', fallback: 'pending' })
  assert.strictEqual(r.status, 'pending')
})

test('applyToolCallState 纯函数：不修改 existing / updates 对象', () => {
  const tc = { status: 'running', id: 't1' }
  const data = { status: 'completed' }
  applyToolCallState(tc, data, { mode: 'result' })
  assert.strictEqual(tc.status, 'running')
  assert.strictEqual(data.status, 'completed')
})

test('applyToolCallState 终态工具接收 result 数据不回退（result 模式）', () => {
  const tc = { status: 'completed' }
  const r = applyToolCallState(tc, { result: 'new' }, { mode: 'result' })
  assert.ok(!r.applied)
  assert.strictEqual(r.status, 'completed')
})

// ==================== deriveDisplayStatus ====================

test('deriveDisplayStatus：显式 status 优先', () => {
  assert.strictEqual(deriveDisplayStatus({ status: 'running' }), 'running')
  assert.strictEqual(deriveDisplayStatus({ status: 'waiting' }), 'waiting')
  assert.strictEqual(deriveDisplayStatus({ status: 'failed', state: 'output-available' }), 'failed')
})

test('deriveDisplayStatus：事件态 state 映射', () => {
  assert.strictEqual(deriveDisplayStatus({ state: 'input-available' }), 'pending')
  assert.strictEqual(deriveDisplayStatus({ state: 'output-available' }), 'completed')
  assert.strictEqual(deriveDisplayStatus({ state: 'output-error' }), 'failed')
  assert.strictEqual(deriveDisplayStatus({ state: 'input-error' }), 'failed')
  assert.strictEqual(deriveDisplayStatus({ state: 'interrupt' }), 'waiting')
})

test('deriveDisplayStatus：result/output → completed，error → failed', () => {
  assert.strictEqual(deriveDisplayStatus({ result: 'data' }), 'completed')
  assert.strictEqual(deriveDisplayStatus({ output: 'data' }), 'completed')
  assert.strictEqual(deriveDisplayStatus({ error: 'boom' }), 'failed')
  // status 为空字符串时回退推导
  assert.strictEqual(deriveDisplayStatus({ status: '', result: 'data' }), 'completed')
})

test('deriveDisplayStatus：无字段可推导时兜底 RUNNING（与原 ChatMessage 三元推导默认一致）', () => {
  assert.strictEqual(deriveDisplayStatus({ name: 'ls' }), 'running')
  assert.strictEqual(deriveDisplayStatus(null), 'running')
  assert.strictEqual(deriveDisplayStatus(undefined), 'running')
})

test('deriveDisplayStatus：支持自定义 fallback（思维链快照等场景）', () => {
  assert.strictEqual(deriveDisplayStatus({ name: 'ls' }, 'completed'), 'completed')
})

console.log('')
console.log(`通过 ${passed} 项，失败 ${failed} 项`)
if (failed > 0) {
  process.exitCode = 1
}
