import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { ToolCallStatus, ApprovalState } from '@/types'

// ==================== Mock 依赖（使用 vi.hoisted 避免 hoisting 问题） ====================

const {
  mockApprovalAPI,
} = vi.hoisted(() => {
  const mockApprovalAPI = {
    getApprovalHistory: vi.fn(),
  }
  return { mockApprovalAPI }
})

vi.mock('@/api/approval', () => ({
  getApprovalHistory: (...args) => mockApprovalAPI.getApprovalHistory(...args),
}))

vi.mock('element-plus', () => {
  const mockFn = vi.fn(() => ({ close: vi.fn() }))
  mockFn.success = vi.fn()
  mockFn.error = vi.fn()
  mockFn.warning = vi.fn()
  mockFn.info = vi.fn()
  return { ElMessage: mockFn }
})

vi.mock('@/utils/logger', () => ({
  logger: {
    log: () => {},
    info: () => {},
    warn: () => {},
    error: () => {},
    debug: () => {},
  },
}))

// 使用真实的 message-operations 函数（架构核心，需端到端验证一致性）
// 不 mock @/utils/message-operations

import { useResearchStore } from '../research'

// ==================== 测试辅助 ====================

/**
 * 构造 toolCall 数据（仅使用 id 作为 Map key）
 */
function makeToolCall(id, extra = {}) {
  return {
    id,
    name: 'ls',
    status: ToolCallStatus.RUNNING,
    parameters: {},
    ...extra,
  }
}

describe('useResearchStore - Map 操作一致性（SubTask 9.5）', () => {
  let store

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    store = useResearchStore()
  })

  // ==================== 1. addOrUpdateToolCall ====================

  describe('addOrUpdateToolCall', () => {
    it('新增 toolCall → toolCallMap + toolCalls 数组同步', () => {
      const taskId = 'task-1'
      store.addOrUpdateToolCall(taskId, makeToolCall('tc-1', {
        name: 'execute',
        status: ToolCallStatus.RUNNING,
        parameters: { command: 'ls' },
      }))

      const toolCalls = store.getToolCalls(taskId)
      expect(toolCalls).toHaveLength(1)
      expect(toolCalls[0].id).toBe('tc-1')
      expect(toolCalls[0].name).toBe('execute')
      expect(toolCalls[0].status).toBe(ToolCallStatus.RUNNING)
      expect(toolCalls[0].parameters).toEqual({ command: 'ls' })
    })

    it('更新已存在 toolCall → 合并字段（保留 parameters）', () => {
      const taskId = 'task-2'
      store.addOrUpdateToolCall(taskId, makeToolCall('tc-update', {
        name: 'execute',
        status: ToolCallStatus.RUNNING,
        parameters: { command: 'pwd' },
      }))

      store.addOrUpdateToolCall(taskId, makeToolCall('tc-update', {
        name: 'execute',
        status: ToolCallStatus.COMPLETED,
      }))

      const toolCalls = store.getToolCalls(taskId)
      expect(toolCalls).toHaveLength(1)
      expect(toolCalls[0].status).toBe(ToolCallStatus.COMPLETED)
      // parameters 应保留
      expect(toolCalls[0].parameters).toEqual({ command: 'pwd' })
    })

    it('无效 taskId → 无操作', () => {
      store.addOrUpdateToolCall('', makeToolCall('tc-x'))
      store.addOrUpdateToolCall('task-3', null)
      expect(store.getToolCalls('task-3')).toEqual([])
    })
  })

  // ==================== 2. updateOrAddToolResult ====================

  describe('updateOrAddToolResult', () => {
    it('更新已有 toolCall 结果 → status 转 completed', () => {
      const taskId = 'task-result-1'
      store.addOrUpdateToolCall(taskId, makeToolCall('tc-result', {
        name: 'execute',
        status: ToolCallStatus.RUNNING,
        parameters: { command: 'echo hi' },
      }))

      store.updateOrAddToolResult(taskId, {
        id: 'tc-result',
        tool_call_id: 'tc-result',
        result: 'hi\n',
        state: 'success',
      })

      const toolCalls = store.getToolCalls(taskId)
      expect(toolCalls[0].result).toBe('hi\n')
      expect(toolCalls[0].status).toBe(ToolCallStatus.COMPLETED)
    })

    it('tool_result 先于 tool 到达 → 容错创建新条目', () => {
      const taskId = 'task-result-2'
      store.updateOrAddToolResult(taskId, {
        id: 'tc-fallback',
        tool_call_id: 'tc-fallback',
        name: 'execute',
        result: 'done',
        state: 'success',
      })

      const toolCalls = store.getToolCalls(taskId)
      expect(toolCalls).toHaveLength(1)
      expect(toolCalls[0].id).toBe('tc-fallback')
      expect(toolCalls[0].result).toBe('done')
      expect(toolCalls[0].status).toBe(ToolCallStatus.COMPLETED)
    })

    it('显式传 status=FAILED → 状态为 failed', () => {
      const taskId = 'task-result-3'
      store.addOrUpdateToolCall(taskId, makeToolCall('tc-fail', {
        name: 'execute',
        status: ToolCallStatus.RUNNING,
      }))

      store.updateOrAddToolResult(taskId, {
        id: 'tc-fail',
        tool_call_id: 'tc-fail',
        result: 'error',
        state: 'output-error',
        status: ToolCallStatus.FAILED,
      })

      const toolCalls = store.getToolCalls(taskId)
      expect(toolCalls[0].status).toBe(ToolCallStatus.FAILED)
      expect(toolCalls[0].result).toBe('error')
    })
  })

  // ==================== 3. setApprovalToToolCall + pendingApprovals ====================

  describe('setApprovalToToolCall + pendingApprovals', () => {
    it('审批先于 tool 到达 → 创建 _synthetic 占位 + pendingApprovals 队列', () => {
      const taskId = 'task-approval-1'
      const toolCallId = 'interrupt-1'
      const approvalData = {
        interrupt_id: toolCallId,
        tool_name: 'execute',
        operation: 'rm -rf /tmp/test',
        state: ApprovalState.PENDING,
      }

      store.setApprovalToToolCall(taskId, toolCallId, approvalData)

      const toolCalls = store.getToolCalls(taskId)
      expect(toolCalls).toHaveLength(1)
      expect(toolCalls[0]._synthetic).toBe(true)
      expect(toolCalls[0].approval).toBeTruthy()
      expect(toolCalls[0].approval.state).toBe(ApprovalState.PENDING)

      // pendingApprovals 队列应有记录
      const task = store.tasks.get(taskId)
      expect(task.pendingApprovals.value.has(toolCallId)).toBe(true)
    })

    it('审批携带 tool_call_id → pendingApprovals 多 ID 存储', () => {
      const taskId = 'task-approval-2'
      const interruptId = 'interrupt-2'
      const llmToolCallId = 'call-llm-2'
      const approvalData = {
        interrupt_id: interruptId,
        tool_call_id: llmToolCallId,
        tool_name: 'execute',
        operation: 'pwd',
        state: ApprovalState.PENDING,
      }

      store.setApprovalToToolCall(taskId, interruptId, approvalData)

      const task = store.tasks.get(taskId)
      expect(task.pendingApprovals.value.has(interruptId)).toBe(true)
      expect(task.pendingApprovals.value.has(llmToolCallId)).toBe(true)
    })

    it('tool 事件到达 → flushPendingApprovals 合并审批数据（_synthetic 清除）', () => {
      const taskId = 'task-approval-3'
      const toolCallId = 'interrupt-3'
      const approvalData = {
        interrupt_id: toolCallId,
        tool_name: 'execute',
        operation: 'ls /tmp',
        state: ApprovalState.PENDING,
      }

      // 1. 审批先到达
      store.setApprovalToToolCall(taskId, toolCallId, approvalData)
      const task = store.tasks.get(taskId)
      expect(task.pendingApprovals.value.has(toolCallId)).toBe(true)

      // 2. tool 事件到达
      store.addOrUpdateToolCall(taskId, makeToolCall(toolCallId, {
        name: 'execute',
        status: ToolCallStatus.RUNNING,
        parameters: { command: 'ls /tmp' },
      }))

      // pendingApprovals 应已移除
      expect(task.pendingApprovals.value.has(toolCallId)).toBe(false)

      // toolCall 应已合并审批数据（_synthetic 标记清除）
      const toolCalls = store.getToolCalls(taskId)
      expect(toolCalls).toHaveLength(1)
      expect(toolCalls[0]._synthetic).toBeUndefined()
      expect(toolCalls[0].approval).toBeTruthy()
      expect(toolCalls[0].approval.state).toBe(ApprovalState.PENDING)
    })

    it('审批附加到已存在 toolCall → 不创建占位', () => {
      const taskId = 'task-approval-4'
      const toolCallId = 'tc-exist-4'

      // tool 先到达
      store.addOrUpdateToolCall(taskId, makeToolCall(toolCallId, {
        name: 'execute',
        status: ToolCallStatus.RUNNING,
      }))

      // 审批后到达
      store.setApprovalToToolCall(taskId, toolCallId, {
        interrupt_id: toolCallId,
        tool_name: 'execute',
        operation: 'ls',
        state: ApprovalState.PENDING,
      })

      const toolCalls = store.getToolCalls(taskId)
      expect(toolCalls[0]._synthetic).toBeUndefined()
      expect(toolCalls[0].approval).toBeTruthy()
      expect(toolCalls[0].approval.state).toBe(ApprovalState.PENDING)

      // pendingApprovals 不应有记录（非占位路径）
      const task = store.tasks.get(taskId)
      expect(task.pendingApprovals.value.has(toolCallId)).toBe(false)
    })
  })

  // ==================== 4. updateToolCallStatus ====================

  describe('updateToolCallStatus', () => {
    it('更新 status → toolCall.status 改变', () => {
      const taskId = 'task-status-1'
      store.addOrUpdateToolCall(taskId, makeToolCall('tc-status', {
        status: ToolCallStatus.RUNNING,
      }))

      store.updateToolCallStatus(taskId, 'tc-status', ToolCallStatus.COMPLETED)

      const toolCalls = store.getToolCalls(taskId)
      expect(toolCalls[0].status).toBe(ToolCallStatus.COMPLETED)
    })

    it('不存在的 task → 无操作（不抛错）', () => {
      store.updateToolCallStatus('nonexistent-task', 'tc-x', ToolCallStatus.COMPLETED)
      // 不应抛错
    })

    it('不存在的 toolCall → 无操作（不抛错）', () => {
      const taskId = 'task-status-2'
      store.addOrUpdateToolCall(taskId, makeToolCall('tc-exist'))
      store.updateToolCallStatus(taskId, 'nonexistent-tc', ToolCallStatus.COMPLETED)
      // 不应抛错
    })
  })

  // ==================== 5. updateToolCallApprovalStateOnly ====================

  describe('updateToolCallApprovalStateOnly', () => {
    it('仅更新 approval.state → 不改 toolCall.status', () => {
      const taskId = 'task-approval-state-1'
      store.addOrUpdateToolCall(taskId, makeToolCall('tc-aps', {
        name: 'execute',
        status: ToolCallStatus.WAITING,
      }))
      store.setApprovalToToolCall(taskId, 'tc-aps', {
        interrupt_id: 'tc-aps',
        tool_name: 'execute',
        operation: 'ls',
        state: ApprovalState.PENDING,
      })

      const result = store.updateToolCallApprovalStateOnly(
        taskId,
        'tc-aps',
        ApprovalState.PROCESSING
      )

      expect(result).toBe(true)
      const toolCalls = store.getToolCalls(taskId)
      expect(toolCalls[0].approval.state).toBe(ApprovalState.PROCESSING)
      // status 不应被修改
      expect(toolCalls[0].status).toBe(ToolCallStatus.WAITING)
    })

    it('不存在的 toolCall → 返回 false', () => {
      const taskId = 'task-approval-state-2'
      store.addOrUpdateToolCall(taskId, makeToolCall('tc-exist'))

      const result = store.updateToolCallApprovalStateOnly(
        taskId,
        'nonexistent',
        ApprovalState.PROCESSING
      )
      expect(result).toBe(false)
    })

    it('不存在的 task → 返回 false', () => {
      const result = store.updateToolCallApprovalStateOnly(
        'nonexistent-task',
        'tc-x',
        ApprovalState.PROCESSING
      )
      expect(result).toBe(false)
    })
  })

  // ==================== 6. getToolCalls / clearTask ====================

  describe('getToolCalls / clearTask', () => {
    it('getToolCalls → 不存在的 task 返回空数组', () => {
      expect(store.getToolCalls('nonexistent')).toEqual([])
    })

    it('getToolCalls → 返回所有 toolCall', () => {
      const taskId = 'task-get-1'
      store.addOrUpdateToolCall(taskId, makeToolCall('tc-g1'))
      store.addOrUpdateToolCall(taskId, makeToolCall('tc-g2', { status: ToolCallStatus.COMPLETED }))

      const all = store.getToolCalls(taskId)
      expect(all).toHaveLength(2)
      expect(all.map(t => t.id).sort()).toEqual(['tc-g1', 'tc-g2'])
    })

    it('clearTask → 清理 task 所有数据', () => {
      const taskId = 'task-clear-1'
      store.addOrUpdateToolCall(taskId, makeToolCall('tc-c1'))
      // setApprovalToToolCall 使用不同的 interrupt_id，会创建 _synthetic 占位条目
      // 因此 toolCalls 数组会有 2 个条目（tc-c1 + interrupt-c1 占位）
      store.setApprovalToToolCall(taskId, 'interrupt-c1', {
        interrupt_id: 'interrupt-c1',
        tool_name: 'execute',
        operation: 'ls',
        state: ApprovalState.PENDING,
      })

      expect(store.getToolCalls(taskId)).toHaveLength(2)
      expect(store.tasks.has(taskId)).toBe(true)

      store.clearTask(taskId)

      expect(store.tasks.has(taskId)).toBe(false)
      expect(store.getToolCalls(taskId)).toEqual([])
    })

    it('clearTask → 不存在的 task 无操作', () => {
      store.clearTask('nonexistent')
      // 不应抛错
    })
  })

  // ==================== 7. loadHistory ====================

  describe('loadHistory', () => {
    it('成功加载历史（Approval 记录） → toolCallMap + toolCalls 同步', async () => {
      const taskId = 'task-history-1'
      // 后端返回 Approval 记录数组（ApprovalReadSerializer 格式）
      const mockResponse = {
        data: {
          data: [
            {
              interrupt_id: 'int-h1',
              source: 'deep_research',
              source_id: taskId,
              tool_name: 'execute',
              title: '执行命令',
              description: '执行 shell 命令',
              action: 'confirm',
              operation: 'ls -la',
              danger_level: 'medium',
              parameters: { command: 'ls -la' },
              state: 'approved',
              user_input: null,
              approved_by: null,
              extra: { tool_call_id: 'tc-h1' },
              created_at: '2026-01-01T00:00:00Z',
              resolved_at: '2026-01-01T00:01:00Z',
            },
            {
              interrupt_id: 'int-h2',
              source: 'deep_research',
              source_id: taskId,
              tool_name: 'read_file',
              title: '读取文件',
              description: '',
              action: 'confirm',
              operation: 'cat file.txt',
              danger_level: 'low',
              parameters: { path: 'file.txt' },
              state: 'rejected',
              user_input: null,
              approved_by: null,
              extra: { tool_call_id: 'tc-h2' },
              created_at: '2026-01-01T00:02:00Z',
              resolved_at: '2026-01-01T00:03:00Z',
            },
          ],
        },
      }
      mockApprovalAPI.getApprovalHistory.mockResolvedValue(mockResponse)

      await store.loadHistory(taskId)

      const toolCalls = store.getToolCalls(taskId)
      expect(toolCalls).toHaveLength(2)
      // 验证 Approval → toolCall 转换
      const tc1 = toolCalls.find(tc => tc.id === 'tc-h1')
      expect(tc1).toBeDefined()
      expect(tc1.name).toBe('execute')
      expect(tc1.status).toBe(ToolCallStatus.APPROVED)
      expect(tc1.parameters).toEqual({ command: 'ls -la' })
      expect(tc1.approval).toBeDefined()
      expect(tc1.approval.interrupt_id).toBe('int-h1')
      expect(tc1.approval.state).toBe('approved')

      const tc2 = toolCalls.find(tc => tc.id === 'tc-h2')
      expect(tc2).toBeDefined()
      expect(tc2.status).toBe(ToolCallStatus.REJECTED)

      // 验证调用参数：source_id + source 过滤
      expect(mockApprovalAPI.getApprovalHistory).toHaveBeenCalledWith(taskId, { source: 'deep_research' })
    })

    it('API 失败 → 清空数据，不抛错', async () => {
      const taskId = 'task-history-2'
      mockApprovalAPI.getApprovalHistory.mockRejectedValue(new Error('network error'))

      await store.loadHistory(taskId)

      expect(store.getToolCalls(taskId)).toEqual([])
    })

    it('无 taskId → 直接返回', async () => {
      await store.loadHistory('')
      expect(mockApprovalAPI.getApprovalHistory).not.toHaveBeenCalled()
    })
  })

  // ==================== 8. flushPendingApprovals（直接调用） ====================

  describe('flushPendingApprovals', () => {
    it('无 pending 数据 → 无操作', () => {
      const taskId = 'task-flush-1'
      store.addOrUpdateToolCall(taskId, makeToolCall('tc-fp'))

      // 直接调用 flushPendingApprovals，无 pending 数据
      store.flushPendingApprovals(taskId, 'tc-fp')
      // 不应抛错，toolCall 仍存在
      expect(store.getToolCalls(taskId)).toHaveLength(1)
    })

    it('无效参数 → 无操作', () => {
      store.flushPendingApprovals('', 'tc-x')
      store.flushPendingApprovals('task-x', '')
      // 不应抛错
    })
  })

  // ==================== 9. 状态转换流程（与 session.js 一致性验证） ====================

  describe('状态转换流程（与 sessionStore 一致性）', () => {
    it('PENDING → RUNNING → COMPLETED 全流程', () => {
      const taskId = 'task-flow-1'
      // PENDING
      store.addOrUpdateToolCall(taskId, {
        id: 'tc-flow',
        tool_call_id: 'tc-flow',
        name: 'execute',
        state: 'input-available',
        status: ToolCallStatus.PENDING,
        parameters: { command: 'ls' },
      })
      expect(store.getToolCalls(taskId)[0].status).toBe(ToolCallStatus.PENDING)

      // RUNNING
      store.updateToolCallStatus(taskId, 'tc-flow', ToolCallStatus.RUNNING)
      expect(store.getToolCalls(taskId)[0].status).toBe(ToolCallStatus.RUNNING)

      // COMPLETED
      store.updateOrAddToolResult(taskId, {
        id: 'tc-flow',
        tool_call_id: 'tc-flow',
        result: 'file1\nfile2',
        state: 'success',
      })
      const tc = store.getToolCalls(taskId)[0]
      expect(tc.status).toBe(ToolCallStatus.COMPLETED)
      expect(tc.result).toBe('file1\nfile2')
    })

    it('PENDING → RUNNING → FAILED（显式传 status=FAILED）', () => {
      const taskId = 'task-flow-2'
      store.addOrUpdateToolCall(taskId, {
        id: 'tc-fail',
        tool_call_id: 'tc-fail',
        name: 'execute',
        status: ToolCallStatus.PENDING,
        parameters: { command: 'exit 1' },
      })
      store.updateToolCallStatus(taskId, 'tc-fail', ToolCallStatus.RUNNING)
      store.updateOrAddToolResult(taskId, {
        id: 'tc-fail',
        tool_call_id: 'tc-fail',
        result: 'error output',
        state: 'output-error',
        status: ToolCallStatus.FAILED,
      })
      const tc = store.getToolCalls(taskId)[0]
      expect(tc.status).toBe(ToolCallStatus.FAILED)
      expect(tc.result).toBe('error output')
    })
  })
})
