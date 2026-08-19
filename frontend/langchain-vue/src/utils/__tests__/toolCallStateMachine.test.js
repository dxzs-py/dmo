/**
 * toolCallStateMachine 单元测试（工具状态机统一，Task 2）
 *
 * 覆盖：状态域/事件态常量、canTransition 合法/非法转换、
 * applyToolCallState（合法推进、终态不回退、failed/timeout、非法转换拒绝、add/result 模式）、
 * deriveDisplayStatus 各分支、isTerminalStatus。
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect } from 'vitest'
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

describe('toolCallStateMachine', () => {
  // ==================== 状态域 / 事件态常量 ====================

  it('状态域常量：pending/waiting/running/completed/failed/timeout（含 rejected）', () => {
    expect(ToolCallStatus.PENDING).toBe('pending')
    expect(ToolCallStatus.WAITING).toBe('waiting')
    expect(ToolCallStatus.RUNNING).toBe('running')
    expect(ToolCallStatus.COMPLETED).toBe('completed')
    expect(ToolCallStatus.FAILED).toBe('failed')
    expect(ToolCallStatus.TIMEOUT).toBe('timeout')
    expect(ToolCallStatus.REJECTED).toBe('rejected')
  })

  it('事件态常量：input-available/input-error/output-available/output-error/interrupt', () => {
    expect(TOOL_CALL_EVENT_STATES.INPUT_AVAILABLE).toBe('input-available')
    expect(TOOL_CALL_EVENT_STATES.INPUT_ERROR).toBe('input-error')
    expect(TOOL_CALL_EVENT_STATES.OUTPUT_AVAILABLE).toBe('output-available')
    expect(TOOL_CALL_EVENT_STATES.OUTPUT_ERROR).toBe('output-error')
    expect(TOOL_CALL_EVENT_STATES.INTERRUPT).toBe('interrupt')
  })

  it('事件态→状态映射：对齐后端 _STATE_TO_STATUS 语义', () => {
    expect(EVENT_STATE_TO_STATUS['input-available']).toBe(ToolCallStatus.PENDING)
    expect(EVENT_STATE_TO_STATUS['output-available']).toBe(ToolCallStatus.COMPLETED)
    expect(EVENT_STATE_TO_STATUS['output-error']).toBe(ToolCallStatus.FAILED)
    expect(EVENT_STATE_TO_STATUS['input-error']).toBe(ToolCallStatus.FAILED)
    expect(EVENT_STATE_TO_STATUS['interrupt']).toBe(ToolCallStatus.WAITING)
  })

  // ==================== 终态 / 优先级 ====================

  it('isTerminalStatus / TERMINAL_STATUSES：终态判定', () => {
    for (const s of ['completed', 'failed', 'timeout', 'rejected']) {
      expect(isTerminalStatus(s), `${s} 应为终态`).toBeTruthy()
      expect(TERMINAL_STATUSES.has(s)).toBeTruthy()
    }
    for (const s of ['pending', 'waiting', 'running']) {
      expect(isTerminalStatus(s), `${s} 不应为终态`).toBeFalsy()
      expect(TERMINAL_STATUSES.has(s)).toBeFalsy()
    }
  })

  it('NON_TERMINAL_STATUSES：非终态集合', () => {
    expect(
      [...NON_TERMINAL_STATUSES].sort(),
    ).toStrictEqual(
      ['pending', 'running', 'waiting']
    )
  })

  it('getToolCallStatusPriority：终态优先级最高，pending 最低', () => {
    expect(getToolCallStatusPriority('pending')).toBe(0)
    expect(getToolCallStatusPriority('waiting')).toBe(1)
    expect(getToolCallStatusPriority('running')).toBe(2)
    expect(getToolCallStatusPriority('completed')).toBe(3)
    expect(getToolCallStatusPriority('failed')).toBe(3)
    expect(getToolCallStatusPriority('timeout')).toBe(3)
    expect(getToolCallStatusPriority('rejected')).toBe(3)
  })

  // ==================== canTransition ====================

  it('canTransition 合法转换：pending→waiting→running→completed', () => {
    expect(canTransition('pending', 'waiting')).toBeTruthy()
    expect(canTransition('pending', 'running')).toBeTruthy()
    expect(canTransition('waiting', 'running')).toBeTruthy()
    expect(canTransition('running', 'completed')).toBeTruthy()
    expect(canTransition('waiting', 'completed')).toBeTruthy()
  })

  it('canTransition 合法转换：failed / timeout / rejected', () => {
    expect(canTransition('pending', 'failed')).toBeTruthy()
    expect(canTransition('pending', 'timeout')).toBeTruthy()
    expect(canTransition('pending', 'rejected')).toBeTruthy() // 对齐后端 _VALID_TRANSITIONS：REJECTED 前驱含 PENDING
    expect(canTransition('waiting', 'failed')).toBeTruthy()
    expect(canTransition('waiting', 'timeout')).toBeTruthy()
    expect(canTransition('waiting', 'rejected')).toBeTruthy()
    expect(canTransition('running', 'failed')).toBeTruthy()
    expect(canTransition('running', 'timeout')).toBeTruthy()
  })

  it('canTransition 非法转换：终态不可回退', () => {
    expect(canTransition('completed', 'pending')).toBeFalsy()
    expect(canTransition('completed', 'running')).toBeFalsy()
    expect(canTransition('failed', 'pending')).toBeFalsy()
    expect(canTransition('timeout', 'running')).toBeFalsy()
    expect(canTransition('rejected', 'waiting')).toBeFalsy()
  })

  it('canTransition 非法转换：running 不可回退 pending（事件乱序防御）', () => {
    expect(canTransition('running', 'pending')).toBeFalsy()
    expect(canTransition('running', 'waiting')).toBeFalsy()
    expect(canTransition('running', 'rejected')).toBeFalsy() // 后端：执行中的工具不可被拒绝
  })

  it('canTransition 同状态幂等', () => {
    expect(canTransition('pending', 'pending')).toBeTruthy()
    expect(canTransition('completed', 'completed')).toBeTruthy()
  })

  it('canTransition 非法转换：waiting 不可被 SSE tool 事件(pending) 回退（Task 7 场景）', () => {
    // 触发浏览器同时接收 SSE tool 事件(status=pending) 与 WS tool_call_waiting(status=waiting)：
    // waiting（已审批/等待同批）不能被 SSE 的 pending 事件回退，保持 waiting 显示"待审批"
    expect(canTransition('waiting', 'pending')).toBeFalsy()
    expect(canTransition('waiting', 'running')).toBeTruthy() // waiting→running 合法：同批全部审批完成后开始执行
  })

  it('applyToolCallState 场景：waiting 收到 SSE pending 事件保持 waiting（Task 7 场景）', () => {
    const tc = { status: 'waiting' }
    const r = applyToolCallState(tc, { status: 'pending' }, { mode: 'add' })
    expect(r.applied).toBeFalsy()
    expect(r.status).toBe('waiting')
  })

  // ==================== approvalStateToToolStatus ====================

  it('approvalStateToToolStatus：待审批/等待同批不映射为 running（Task 7 P3-19 快照恢复根因）', () => {
    // 快照恢复 / 审批面板兜底展示：非终态审批不得显示"执行中"
    expect(approvalStateToToolStatus('pending')).toBe('pending')
    expect(approvalStateToToolStatus('waiting')).toBe('waiting')
    // 已通过审批：工具开始执行 → running
    expect(approvalStateToToolStatus('processing')).toBe('running')
    expect(approvalStateToToolStatus('approved')).toBe('running')
    // 终态透传
    expect(approvalStateToToolStatus('rejected')).toBe('rejected')
    expect(approvalStateToToolStatus('timeout')).toBe('timeout')
    // 未知/缺失状态：保守默认 running（对齐旧映射行为）
    expect(approvalStateToToolStatus(undefined)).toBe('running')
    expect(approvalStateToToolStatus('')).toBe('running')
  })

  // ==================== applyToolCallState ====================

  it('applyToolCallState 合法推进：pending→waiting→running→completed', () => {
    let tc = { status: 'pending' }
    let r = applyToolCallState(tc, { status: 'waiting' }, { mode: 'add' })
    expect(r.applied).toBeTruthy()
    tc.status = r.status
    expect(tc.status).toBe('waiting')
    r = applyToolCallState(tc, { status: 'running' }, { mode: 'add' })
    expect(r.applied).toBeTruthy()
    tc.status = r.status
    expect(tc.status).toBe('running')
    r = applyToolCallState(tc, { status: 'completed' }, { mode: 'result' })
    expect(r.applied).toBeTruthy()
    tc.status = r.status
    expect(tc.status).toBe('completed')
  })

  it('applyToolCallState 终态不回退：completed 后收到 pending/running 保持不变', () => {
    const tc = { status: 'completed' }
    const r1 = applyToolCallState(tc, { status: 'pending' }, { mode: 'add' })
    expect(r1.applied).toBeFalsy()
    expect(r1.status).toBe('completed')
    const r2 = applyToolCallState(tc, { status: 'running' }, { mode: 'add' })
    expect(r2.applied).toBeFalsy()
    expect(r2.status).toBe('completed')
    const r3 = applyToolCallState(tc, { status: 'failed' }, { mode: 'result' })
    expect(r3.applied).toBeFalsy()
    expect(r3.status).toBe('completed')
  })

  it('applyToolCallState failed/timeout 转换', () => {
    // pending → failed
    let r = applyToolCallState({ status: 'pending' }, { status: 'failed' }, { mode: 'result' })
    expect(r.applied).toBeTruthy()
    expect(r.status).toBe('failed')
    // running → timeout
    r = applyToolCallState({ status: 'running' }, { status: 'timeout' }, { mode: 'result' })
    expect(r.applied).toBeTruthy()
    expect(r.status).toBe('timeout')
    // waiting → failed（审批等待中失败）
    r = applyToolCallState({ status: 'waiting' }, { status: 'failed' }, { mode: 'result' })
    expect(r.applied).toBeTruthy()
    expect(r.status).toBe('failed')
  })

  it('applyToolCallState 非法转换拒绝：running 被旧事件回退 pending 保持 running', () => {
    const tc = { status: 'running' }
    const r = applyToolCallState(tc, { status: 'pending' }, { mode: 'add' })
    expect(r.applied).toBeFalsy()
    expect(r.status).toBe('running')
  })

  it('applyToolCallState 新建（existing=null）：直接应用推导状态', () => {
    const r = applyToolCallState(null, { status: 'pending' }, { mode: 'add' })
    expect(r.applied).toBeTruthy()
    expect(r.status).toBe('pending')
  })

  it('applyToolCallState add 模式：显式 status 优先，state 映射兜底 pending', () => {
    // 显式 status
    let r = applyToolCallState(null, { status: 'running', state: 'input-available' }, { mode: 'add' })
    expect(r.status).toBe('running')
    // 无 status、有 state → 事件态映射
    r = applyToolCallState(null, { state: 'input-available' }, { mode: 'add' })
    expect(r.status).toBe('pending')
    r = applyToolCallState(null, { state: 'output-available' }, { mode: 'add' })
    expect(r.status).toBe('completed')
    r = applyToolCallState(null, { state: 'output-error' }, { mode: 'add' })
    expect(r.status).toBe('failed')
    // 无 status、无 state → 不设置（fallback undefined）
    r = applyToolCallState(null, { name: 'ls' }, { mode: 'add' })
    expect(r.applied).toBeFalsy()
    expect(r.status).toBe(undefined)
  })

  it('applyToolCallState result 模式：result→completed / error→failed 推导', () => {
    // result 有值 → COMPLETED
    let r = applyToolCallState(null, { result: 'ok' }, { mode: 'result' })
    expect(r.status).toBe('completed')
    // error 有值 → FAILED
    r = applyToolCallState(null, { error: 'boom' }, { mode: 'result' })
    expect(r.status).toBe('failed')
    // state=output-error 时 result 不触发 COMPLETED（错误输出仍为失败态）
    r = applyToolCallState(null, { state: 'output-error', result: 'partial' }, { mode: 'result' })
    expect(r.status).toBe('failed')
    // 无任何可推导字段 → fallback
    r = applyToolCallState(null, { name: 'ls' }, { mode: 'result', fallback: 'pending' })
    expect(r.status).toBe('pending')
  })

  it('applyToolCallState 纯函数：不修改 existing / updates 对象', () => {
    const tc = { status: 'running', id: 't1' }
    const data = { status: 'completed' }
    applyToolCallState(tc, data, { mode: 'result' })
    expect(tc.status).toBe('running')
    expect(data.status).toBe('completed')
  })

  it('applyToolCallState 终态工具接收 result 数据不回退（result 模式）', () => {
    const tc = { status: 'completed' }
    const r = applyToolCallState(tc, { result: 'new' }, { mode: 'result' })
    expect(r.applied).toBeFalsy()
    expect(r.status).toBe('completed')
  })

  // ==================== deriveDisplayStatus ====================

  it('deriveDisplayStatus：显式 status 优先', () => {
    expect(deriveDisplayStatus({ status: 'running' })).toBe('running')
    expect(deriveDisplayStatus({ status: 'waiting' })).toBe('waiting')
    expect(deriveDisplayStatus({ status: 'failed', state: 'output-available' })).toBe('failed')
  })

  it('deriveDisplayStatus：事件态 state 映射', () => {
    expect(deriveDisplayStatus({ state: 'input-available' })).toBe('pending')
    expect(deriveDisplayStatus({ state: 'output-available' })).toBe('completed')
    expect(deriveDisplayStatus({ state: 'output-error' })).toBe('failed')
    expect(deriveDisplayStatus({ state: 'input-error' })).toBe('failed')
    expect(deriveDisplayStatus({ state: 'interrupt' })).toBe('waiting')
  })

  it('deriveDisplayStatus：result/output → completed，error → failed', () => {
    expect(deriveDisplayStatus({ result: 'data' })).toBe('completed')
    expect(deriveDisplayStatus({ output: 'data' })).toBe('completed')
    expect(deriveDisplayStatus({ error: 'boom' })).toBe('failed')
    // status 为空字符串时回退推导
    expect(deriveDisplayStatus({ status: '', result: 'data' })).toBe('completed')
  })

  it('deriveDisplayStatus：无字段可推导时兜底 RUNNING（与原 ChatMessage 三元推导默认一致）', () => {
    expect(deriveDisplayStatus({ name: 'ls' })).toBe('running')
    expect(deriveDisplayStatus(null)).toBe('running')
    expect(deriveDisplayStatus(undefined)).toBe('running')
  })

  it('deriveDisplayStatus：支持自定义 fallback（思维链快照等场景）', () => {
    expect(deriveDisplayStatus({ name: 'ls' }, 'completed')).toBe('completed')
  })
})
