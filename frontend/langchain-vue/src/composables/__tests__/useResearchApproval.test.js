import { describe, it, expect, beforeEach, vi } from 'vitest'
import { ref } from 'vue'

const {
  mockElMessage,
  mockPendingApprovals,
  mockExecuteApproval,
  mockUpdateToolCallApprovalState,
  mockSetApprovalToLastMessage,
  mockGetInterruptId,
} = vi.hoisted(() => ({
  mockElMessage: { success: vi.fn(), info: vi.fn(), error: vi.fn(), warning: vi.fn() },
  mockPendingApprovals: new Map(),
  mockExecuteApproval: vi.fn(),
  mockUpdateToolCallApprovalState: vi.fn(),
  mockSetApprovalToLastMessage: vi.fn(),
  mockGetInterruptId: vi.fn(),
}))

vi.mock('@/stores/approval', () => ({
  useApprovalStore: () => ({
    pendingApprovals: mockPendingApprovals,
    executeApproval: (...args) => mockExecuteApproval(...args),
  }),
}))

vi.mock('@/stores/session', () => ({
  useSessionStore: () => ({
    currentSessionId: 'session-1',
    updateToolCallApprovalState: (...args) => mockUpdateToolCallApprovalState(...args),
    setApprovalToLastMessage: (...args) => mockSetApprovalToLastMessage(...args),
  }),
}))

vi.mock('element-plus', () => ({ ElMessage: mockElMessage }))

vi.mock('@/utils/message-operations', () => ({
  getInterruptId: (...args) => mockGetInterruptId(...args),
}))

import { useResearchApproval } from '../useResearchApproval'

describe('useResearchApproval', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPendingApprovals.clear()
  })

  it('handleApprove 调用 executeApproval(approval, true) 并提示成功', async () => {
    mockGetInterruptId.mockReturnValue('int-1')
    mockExecuteApproval.mockResolvedValue(true)
    const task = ref({ task_id: 't-1' })

    const { handleApprove } = useResearchApproval({ task })
    await handleApprove({ approval: { interrupt_id: 'int-1' }, id: 'int-1' })

    expect(mockExecuteApproval).toHaveBeenCalledWith(
      { interrupt_id: 'int-1' },
      true,
      undefined,
      { taskId: 't-1' }
    )
    expect(mockElMessage.success).toHaveBeenCalledWith('已确认操作')
  })

  it('handleApprove 携带 _user_input', async () => {
    mockGetInterruptId.mockReturnValue('int-1')
    mockExecuteApproval.mockResolvedValue(true)
    const task = ref({ task_id: 't-1' })

    const { handleApprove } = useResearchApproval({ task })
    await handleApprove({ approval: {}, id: 'int-1', _user_input: 'hello' })

    expect(mockExecuteApproval).toHaveBeenCalledWith(
      expect.anything(),
      true,
      'hello',
      { taskId: 't-1' }
    )
  })

  it('handleApprove 遇到 processing 态时跳过（防重复）', async () => {
    mockGetInterruptId.mockReturnValue('int-1')
    mockPendingApprovals.set('int-1', { approvalData: { state: 'processing' } })
    const task = ref({ task_id: 't-1' })

    const { handleApprove } = useResearchApproval({ task })
    await handleApprove({ approval: {}, id: 'int-1' })

    expect(mockExecuteApproval).not.toHaveBeenCalled()
  })

  it('handleApprove 失败时恢复 session store 状态并提示错误', async () => {
    mockGetInterruptId.mockReturnValue('int-1')
    mockExecuteApproval.mockRejectedValue(new Error('执行失败'))
    const task = ref({ task_id: 't-1' })

    const { handleApprove } = useResearchApproval({ task })
    await handleApprove({ approval: { tool_call_id: 'int-1' }, id: 'int-1' })

    expect(mockUpdateToolCallApprovalState).toHaveBeenCalledWith('session-1', 'int-1', 'pending')
    expect(mockSetApprovalToLastMessage).toHaveBeenCalledWith(
      'session-1',
      expect.objectContaining({ state: 'pending' })
    )
    expect(mockElMessage.error).toHaveBeenCalledWith('确认操作失败')
  })

  it('handleReject 调用 executeApproval(approval, false) 并提示拒绝', async () => {
    mockGetInterruptId.mockReturnValue('int-1')
    mockExecuteApproval.mockResolvedValue(true)
    const task = ref({ task_id: 't-1' })

    const { handleReject } = useResearchApproval({ task })
    await handleReject({ approval: {}, id: 'int-1' })

    expect(mockExecuteApproval).toHaveBeenCalledWith(
      expect.anything(),
      false,
      null,
      { taskId: 't-1' }
    )
    expect(mockElMessage.info).toHaveBeenCalledWith('已拒绝操作')
  })

  it('handleReject 失败时恢复 session store 状态并提示错误', async () => {
    mockGetInterruptId.mockReturnValue('int-1')
    mockExecuteApproval.mockRejectedValue(new Error('执行失败'))
    const task = ref({ task_id: 't-1' })

    const { handleReject } = useResearchApproval({ task })
    await handleReject({ approval: {}, id: 'int-1' })

    expect(mockUpdateToolCallApprovalState).toHaveBeenCalledWith('session-1', 'int-1', 'pending')
    expect(mockElMessage.error).toHaveBeenCalledWith('拒绝操作失败')
  })

  it('getInterruptId 无返回时回退到 toolCallData.id', async () => {
    mockGetInterruptId.mockReturnValue(null)
    mockExecuteApproval.mockResolvedValue(true)
    const task = ref({ task_id: 't-1' })

    const { handleApprove } = useResearchApproval({ task })
    await handleApprove({ approval: {}, id: 'fallback-id' })

    // 防重复检查使用 fallback-id
    expect(mockExecuteApproval).toHaveBeenCalled()
  })
})
