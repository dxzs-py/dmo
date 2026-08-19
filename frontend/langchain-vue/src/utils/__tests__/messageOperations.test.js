/**
 * messageOperations.finalizeToolCallsInMap 单元测试
 *
 * 覆盖（P3-34/P3-35 根因修复）：最终化时必须跳过「审批未放行执行」的工具
 * （approval.state 非 approved/completed），防止审批等待/拒绝/超时期间
 * 工具被误标为 COMPLETED（审批按钮因 status 终态消失）。
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect } from 'vitest'
import { ToolCallStatus, ApprovalState } from '../../types/index.js'
import { finalizeToolCallsInMap, sortToolCallsForDisplay, _mergeToolCalls } from '../messageOperations.js'

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

describe('messageOperations', () => {
  describe('finalizeToolCallsInMap', () => {
    it('无审批的非终态工具被兜底为 COMPLETED', () => {
      const map = makeMap('call_1', baseToolCall({ status: ToolCallStatus.PENDING }))
      const count = finalizeToolCallsInMap(map)
      expect(count).toEqual(1)
      expect(map.get('call_1').status).toEqual(ToolCallStatus.COMPLETED)
    })

    it('无审批的 running 工具被兜底为 COMPLETED（审批通过但 completed 事件丢失）', () => {
      const map = makeMap('call_1', baseToolCall({ status: ToolCallStatus.RUNNING }))
      finalizeToolCallsInMap(map)
      expect(map.get('call_1').status).toEqual(ToolCallStatus.COMPLETED)
    })

    it('审批 pending 的工具不被最终化（P3-34/P3-35 根因）', () => {
      const tc = baseToolCall({
        status: ToolCallStatus.PENDING,
        approval: { state: ApprovalState.PENDING },
      })
      const map = makeMap('call_1', tc)
      const count = finalizeToolCallsInMap(map)
      expect(count).toEqual(0)
      expect(tc.status).toEqual(ToolCallStatus.PENDING)
      expect(tc.approval.state).toEqual(ApprovalState.PENDING)
    })

    it('审批 waiting 的工具不被最终化', () => {
      const tc = baseToolCall({
        status: ToolCallStatus.WAITING,
        approval: { state: ApprovalState.WAITING },
      })
      const map = makeMap('call_1', tc)
      finalizeToolCallsInMap(map)
      expect(tc.status).toEqual(ToolCallStatus.WAITING)
      expect(tc.approval.state).toEqual(ApprovalState.WAITING)
    })

    it('审批 processing 的工具不被最终化', () => {
      const tc = baseToolCall({
        status: ToolCallStatus.PENDING,
        approval: { state: ApprovalState.PROCESSING },
      })
      const map = makeMap('call_1', tc)
      finalizeToolCallsInMap(map)
      expect(tc.status).toEqual(ToolCallStatus.PENDING)
      expect(tc.approval.state).toEqual(ApprovalState.PROCESSING)
    })

    it('审批 rejected 的工具不被兜底为 COMPLETED（防止误标"已完成"）', () => {
      const tc = baseToolCall({
        status: ToolCallStatus.PENDING,
        approval: { state: ApprovalState.REJECTED },
      })
      const map = makeMap('call_1', tc)
      const count = finalizeToolCallsInMap(map)
      expect(count).toEqual(0)
      expect(tc.status).toEqual(ToolCallStatus.PENDING)
      expect(tc.approval.state).toEqual(ApprovalState.REJECTED)
    })

    it('审批 timeout 的工具不被兜底为 COMPLETED', () => {
      const tc = baseToolCall({
        status: ToolCallStatus.PENDING,
        approval: { state: ApprovalState.TIMEOUT },
      })
      const map = makeMap('call_1', tc)
      finalizeToolCallsInMap(map)
      expect(tc.status).toEqual(ToolCallStatus.PENDING)
    })

    it('审批 approved 且工具非终态 → 兜底为 COMPLETED（审批已放行执行）', () => {
      const tc = baseToolCall({
        status: ToolCallStatus.RUNNING,
        approval: { state: ApprovalState.APPROVED },
      })
      const map = makeMap('call_1', tc)
      const count = finalizeToolCallsInMap(map)
      expect(count).toEqual(1)
      expect(tc.status).toEqual(ToolCallStatus.COMPLETED)
    })

    it('审批 completed 且工具非终态 → 兜底为 COMPLETED', () => {
      const tc = baseToolCall({
        status: ToolCallStatus.RUNNING,
        approval: { state: 'completed' },
      })
      const map = makeMap('call_1', tc)
      finalizeToolCallsInMap(map)
      expect(tc.status).toEqual(ToolCallStatus.COMPLETED)
    })

    it('终态工具不被回退（已完成保持）', () => {
      const tc = baseToolCall({
        status: ToolCallStatus.COMPLETED,
        approval: null,
      })
      const map = makeMap('call_1', tc)
      const count = finalizeToolCallsInMap(map)
      expect(count).toEqual(0)
      expect(tc.status).toEqual(ToolCallStatus.COMPLETED)
    })

    it('messageBackendId 过滤：仅最终化目标消息的工具', () => {
      const tcA = baseToolCall({ id: 'call_a', toolCallId: 'call_a', messageBackendId: 100, status: ToolCallStatus.PENDING })
      const tcB = baseToolCall({ id: 'call_b', toolCallId: 'call_b', messageBackendId: 200, status: ToolCallStatus.PENDING })
      const map = new Map()
      map.set('call_a', tcA)
      map.set('call_b', tcB)
      const count = finalizeToolCallsInMap(map, 100)
      expect(count).toEqual(1)
      expect(tcA.status).toEqual(ToolCallStatus.COMPLETED)
      expect(tcB.status).toEqual(ToolCallStatus.PENDING)
    })

    it('空 Map 返回 0', () => {
      expect(finalizeToolCallsInMap(new Map())).toEqual(0)
    })

    it('混合场景：仅审批未放行工具被跳过，其余兜底', () => {
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
      expect(count).toEqual(1)
      expect(pendingApproval.status).toEqual(ToolCallStatus.PENDING)
      expect(pendingApproval.approval.state).toEqual(ApprovalState.PENDING)
      expect(plain.status).toEqual(ToolCallStatus.COMPLETED)
    })
  })

  describe('sortToolCallsForDisplay（seq 主 + 数组兜底）', () => {
    it('全部条目有 seq → 按 seq 升序排序', () => {
      const arr = [
        { id: 'fs_write_file', seq: 3 },
        { id: 'shell_exec_1', seq: 1 },
        { id: 'shell_exec_2', seq: 2 },
      ]
      sortToolCallsForDisplay(arr)
      expect(arr.map(t => t.id)).toEqual(['shell_exec_1', 'shell_exec_2', 'fs_write_file'])
    })

    it('全部条目无 seq → 保持原数组顺序（回归修复：不再把无 seq 按 MAX 排后）', () => {
      const arr = [
        { id: 'shell_exec_1' },
        { id: 'shell_exec_2' },
        { id: 'fs_write_file' },
      ]
      sortToolCallsForDisplay(arr)
      expect(arr.map(t => t.id)).toEqual(['shell_exec_1', 'shell_exec_2', 'fs_write_file'])
    })

    it('部分条目无 seq（混合数据）→ 保持原数组顺序（seq 不完整时无法比较，退回数组兜底）', () => {
      const arr = [
        { id: 'shell_exec_1' },
        { id: 'shell_exec_2' },
        { id: 'fs_write_file', seq: 3 },
      ]
      sortToolCallsForDisplay(arr)
      expect(arr.map(t => t.id)).toEqual(['shell_exec_1', 'shell_exec_2', 'fs_write_file'])
    })

    it('seq 含非正整数（0/负数）→ 视为无 seq，保持数组顺序', () => {
      const arr = [
        { id: 'a', seq: 0 },
        { id: 'b', seq: -1 },
        { id: 'c' },
      ]
      sortToolCallsForDisplay(arr)
      expect(arr.map(t => t.id)).toEqual(['a', 'b', 'c'])
    })

    it('空数组与单元素 → 原样返回', () => {
      const empty = []
      sortToolCallsForDisplay(empty)
      expect(empty).toEqual([])
      const single = [{ id: 'x', seq: 5 }]
      sortToolCallsForDisplay(single)
      expect(single.map(t => t.id)).toEqual(['x'])
    })
  })

  describe('_mergeToolCalls（position 保护 / 同名多工具按 id 匹配）', () => {
    it('后端缺失 position：保留本地 number position', () => {
      const local = [{ id: 'spawn_1', name: 'spawn_sub_agent', position: 38 }]
      const backend = [{ id: 'spawn_1', name: 'spawn_sub_agent' }]
      const merged = _mergeToolCalls(local, backend)
      expect(merged[0].position).toEqual(38)
    })

    it('position=0（本地）不被后端 undefined 覆盖', () => {
      const local = [{ id: 'wait_1', name: 'wait_for_subagent', position: 0 }]
      const backend = [{ id: 'wait_1', name: 'wait_for_subagent' }]
      const merged = _mergeToolCalls(local, backend)
      expect(merged[0].position).toEqual(0)
    })

    it('position=0（后端）覆盖本地缺失的 position', () => {
      const local = [{ id: 'wait_1', name: 'wait_for_subagent' }]
      const backend = [{ id: 'wait_1', name: 'wait_for_subagent', position: 0 }]
      const merged = _mergeToolCalls(local, backend)
      expect(merged[0].position).toEqual(0)
    })

    it('同名多工具（两个 spawn_sub_agent）按 id 精确匹配，各自 position 不丢失', () => {
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
      expect(spawn1.position).toEqual(38)
      expect(spawn2.position).toEqual(38)
    })

    it('后端独有条目 position=0 原样保留（本地为空列表）', () => {
      const merged = _mergeToolCalls([], [{ id: 'wait_1', name: 'wait_for_subagent', position: 0 }])
      expect(merged[0].position).toEqual(0)
    })

    it('后端缺失 subagentThreadId：保留本地事件已写入的图层字段', () => {
      const local = [{ id: 'sh_1', name: 'shell_exec', subagentThreadId: 'subagent_abc', agentName: 'ollama-list', depth: 1 }]
      const backend = [{ id: 'sh_1', name: 'shell_exec' }]
      const merged = _mergeToolCalls(local, backend)
      expect(merged[0].subagentThreadId).toEqual('subagent_abc')
      expect(merged[0].agentName).toEqual('ollama-list')
      expect(merged[0].depth).toEqual(1)
    })

    it('后端缺失 subagentThreadId 且本地为空字符串：不误回填（空串原样保留）', () => {
      const local = [{ id: 'sh_1', name: 'shell_exec', subagentThreadId: '' }]
      const backend = [{ id: 'sh_1', name: 'shell_exec' }]
      const merged = _mergeToolCalls(local, backend)
      expect(merged[0].subagentThreadId).toEqual('')
    })
  })
})
