import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { StreamState } from '@/types'

// Mock session store
// 方法名与 stores/session.js 导出保持一致（approval store 调用的实际方法名）
const mockSessionStore = {
  currentSessionId: 'session-1',
  sessions: [],
  setStreamStateToLastMessage: vi.fn(),
  updateToolCallApprovalState: vi.fn(),
  setApprovalToToolCall: vi.fn(),
  setApprovalToLastMessage: vi.fn(),
  appendToLastAssistantMessage: vi.fn(),
  appendToLastMessage: vi.fn(),
  addOrUpdateToolCallToLastMessage: vi.fn(),
  updateOrAddToolResultToLastMessage: vi.fn(),
  setReasoningToLastMessage: vi.fn(),
  syncLastMessageToBackend: vi.fn().mockResolvedValue(undefined),
  syncMessageByBackendIdToBackend: vi.fn().mockResolvedValue(undefined),
  updateToolCallStatus: vi.fn(),
  findToolCallInSession: vi.fn().mockReturnValue(null),
  setStreamStateToMessageByIdx: vi.fn(),
  appendToMessage: vi.fn(),
  setReasoningToMessage: vi.fn(),
  applyToolCallEvent: vi.fn(),
}
vi.mock('@/stores/session', () => ({
  useSessionStore: () => mockSessionStore,
}))

// Mock sync store
const mockSyncStore = {
  startStreaming: vi.fn(),
  stopStreaming: vi.fn(),
}
vi.mock('@/stores/sync', () => ({
  useSyncStore: () => mockSyncStore,
}))

// Mock model store
const mockModelStore = {
  thinkingEnabled: false,
  ensureProvidersLoaded: vi.fn().mockResolvedValue(undefined),
  getModelConfig: vi.fn().mockReturnValue({ provider_id: 'p1', model_name: 'm1' }),
}
vi.mock('@/stores/model', () => ({
  useModelStore: () => mockModelStore,
}))

// Mock approval API
// chat 审批 SSE 流响应需提供 headers.get，否则 _executeChatApproval 解析 content-type 时抛错
const mockResumeApprovalStream = vi.fn().mockResolvedValue({
  headers: { get: () => 'text/event-stream' },
})
vi.mock('@/api/approval', () => ({
  resumeApprovalStream: (...args) => mockResumeApprovalStream(...args),
}))

// Mock research API
// deep_research 审批通过 approveResearchCommand / rejectResearchCommand（@/api/research 命名导出）
const mockApproveResearch = vi.fn().mockResolvedValue({})
const mockRejectResearch = vi.fn().mockResolvedValue({})
vi.mock('@/api/research', () => ({
  approveResearchCommand: (...args) => mockApproveResearch(...args),
  rejectResearchCommand: (...args) => mockRejectResearch(...args),
}))

// Mock useStreamFinalizer
const mockFinalizeStream = vi.fn().mockResolvedValue(true)
const mockMarkInterrupted = vi.fn()
vi.mock('@/composables/useStreamFinalizer', () => ({
  useStreamFinalizer: () => ({
    finalizeStream: mockFinalizeStream,
    markInterrupted: mockMarkInterrupted,
  }),
}))

// Mock SSE
const mockReadSSEStream = vi.fn().mockResolvedValue(undefined)
vi.mock('@/utils/sse', () => ({
  readSSEStream: (...args) => mockReadSSEStream(...args),
}))

// Mock message-operations
vi.mock('@/utils/message-operations', () => ({
  getInterruptId: vi.fn((approval) => approval?.interrupt_id || approval?.tool_call_id || ''),
}))

vi.mock('@/utils/logger', () => ({
  logger: {
    log: () => {},
    info: () => {},
    warn: () => {},
    error: () => {},
    debug: () => {},
  },
}))

// Mock Element Plus
vi.mock('element-plus', () => {
  const mockFn = vi.fn(() => ({ close: vi.fn() }))
  mockFn.success = vi.fn()
  mockFn.error = vi.fn()
  mockFn.warning = vi.fn()
  mockFn.info = vi.fn()
  return { ElMessage: mockFn }
})

import { useApprovalStore } from '../approval'

describe('useApprovalStore', () => {
  let store

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    // 重置默认实现
    mockSessionStore.syncLastMessageToBackend.mockResolvedValue(undefined)
    mockSessionStore.syncMessageByBackendIdToBackend.mockResolvedValue(undefined)
    mockSessionStore.findToolCallInSession.mockReturnValue(null)
    mockModelStore.ensureProvidersLoaded.mockResolvedValue(undefined)
    mockModelStore.getModelConfig.mockReturnValue({ provider_id: 'p1', model_name: 'm1' })
    // 需提供 headers.get 方法，避免 _executeChatApproval 解析 content-type 抛错
    mockResumeApprovalStream.mockResolvedValue({
      headers: { get: () => 'text/event-stream' },
    })
    mockApproveResearch.mockResolvedValue({})
    mockRejectResearch.mockResolvedValue({})
    mockReadSSEStream.mockResolvedValue(undefined)
    mockFinalizeStream.mockResolvedValue(true)
    mockSessionStore.sessions = []
    store = useApprovalStore()
    store.clearAll()
  })

  describe('handleApprovalEvent', () => {
    it('pending 状态：加入 pendingApprovals', () => {
      const data = { interrupt_id: 'int-1', tool_name: 'shell', state: 'pending' }
      store.handleApprovalEvent(data, { source: 'chat', sessionId: 'session-1' })

      expect(store.pendingApprovals.has('int-1')).toBe(true)
      const entry = store.pendingApprovals.get('int-1')
      expect(entry.source).toBe('chat')
      expect(entry.sessionId).toBe('session-1')
      expect(entry.approvalData.state).toBe('pending')
      // 同步到 session store
      expect(mockSessionStore.setApprovalToToolCall).toHaveBeenCalledWith('session-1', 'int-1', expect.objectContaining({ state: 'pending' }))
      expect(mockSessionStore.setApprovalToLastMessage).toHaveBeenCalled()
    })

    it('processing 状态：保留在 pendingApprovals（仅更新 state）并更新 sessionStore', () => {
      // 先加入 pending
      store.handleApprovalEvent(
        { interrupt_id: 'int-2', tool_name: 'shell', state: 'pending' },
        { source: 'chat', sessionId: 'session-1' }
      )
      expect(store.pendingApprovals.has('int-2')).toBe(true)

      // 触发 processing
      store.handleApprovalEvent(
        { interrupt_id: 'int-2', type: 'approval_processing' },
        { source: 'chat', sessionId: 'session-1' }
      )

      // _handleProcessing 保留 pendingApprovals 条目，仅更新 approvalData.state='processing'
      // 设计意图：审批面板在 processing 阶段保持显示（按钮禁用），工具结果到达后才隐藏
      expect(store.pendingApprovals.has('int-2')).toBe(true)
      const entry = store.pendingApprovals.get('int-2')
      expect(entry.approvalData.state).toBe('processing')
      // updateToolCallApprovalState 调用 3 参数（sessionId, toolCallId, state）
      expect(mockSessionStore.updateToolCallApprovalState).toHaveBeenCalledWith(
        'session-1', 'int-2', 'processing'
      )
    })

    it('approved 状态：从 pendingApprovals 移除', () => {
      store.handleApprovalEvent(
        { interrupt_id: 'int-3', tool_name: 'shell', state: 'pending' },
        { source: 'chat', sessionId: 'session-1' }
      )
      expect(store.pendingApprovals.has('int-3')).toBe(true)

      store.handleApprovalEvent(
        { interrupt_id: 'int-3', type: 'approval_approved', state: 'approved' },
        { source: 'chat', sessionId: 'session-1' }
      )

      expect(store.pendingApprovals.has('int-3')).toBe(false)
      // updateToolCallApprovalState 调用 3 参数（sessionId, toolCallId, state）
      expect(mockSessionStore.updateToolCallApprovalState).toHaveBeenCalledWith(
        'session-1', 'int-3', 'approved'
      )
    })

    it('rejected 状态：从 pendingApprovals 移除', () => {
      store.handleApprovalEvent(
        { interrupt_id: 'int-4', tool_name: 'shell', state: 'pending' },
        { source: 'chat', sessionId: 'session-1' }
      )

      store.handleApprovalEvent(
        { interrupt_id: 'int-4', type: 'approval_rejected', state: 'rejected' },
        { source: 'chat', sessionId: 'session-1' }
      )

      expect(store.pendingApprovals.has('int-4')).toBe(false)
      // updateToolCallApprovalState 调用 3 参数（sessionId, toolCallId, state）
      expect(mockSessionStore.updateToolCallApprovalState).toHaveBeenCalledWith(
        'session-1', 'int-4', 'rejected'
      )
    })

    it('timeout 状态：从 pendingApprovals 移除', () => {
      store.handleApprovalEvent(
        { interrupt_id: 'int-5', tool_name: 'shell', state: 'pending' },
        { source: 'chat', sessionId: 'session-1' }
      )

      store.handleApprovalEvent(
        { interrupt_id: 'int-5', type: 'approval_timeout', tool_name: 'shell' },
        { source: 'chat', sessionId: 'session-1' }
      )

      expect(store.pendingApprovals.has('int-5')).toBe(false)
      // timeout 统一使用 updateToolCallApprovalState（不覆盖 toolCall.status）
      // 调用 3 参数（sessionId, toolCallId, state）
      expect(mockSessionStore.updateToolCallApprovalState).toHaveBeenCalledWith(
        'session-1', 'int-5', 'timeout'
      )
    })
  })

  describe('executeApproval 路由', () => {
    it('source=deep_research approved=true 调用 approveResearchCommand', async () => {
      store.handleApprovalEvent(
        { interrupt_id: 'int-dr', tool_name: 'shell', state: 'pending', source: 'deep_research' },
        { source: 'deep_research', taskId: 'task-dr', sessionId: 'session-1' }
      )

      const approval = { interrupt_id: 'int-dr' }
      await store.executeApproval(approval, true, null, { taskId: 'task-dr' })

      // deep_research 通过 approveResearchCommand(taskId, interruptId, userInput)
      // action 非 confirm_with_input 时 userInput 传 undefined
      expect(mockApproveResearch).toHaveBeenCalledWith('task-dr', 'int-dr', undefined)
      expect(mockRejectResearch).not.toHaveBeenCalled()
      expect(mockResumeApprovalStream).not.toHaveBeenCalled()
    })

    it('source=deep_research approved=false 调用 rejectResearchCommand', async () => {
      store.handleApprovalEvent(
        { interrupt_id: 'int-dr2', tool_name: 'shell', state: 'pending', source: 'deep_research' },
        { source: 'deep_research', taskId: 'task-dr', sessionId: 'session-1' }
      )

      const approval = { interrupt_id: 'int-dr2' }
      await store.executeApproval(approval, false, null, { taskId: 'task-dr' })

      // deep_research 拒绝通过 rejectResearchCommand(taskId, interruptId)
      expect(mockRejectResearch).toHaveBeenCalledWith('task-dr', 'int-dr2')
      expect(mockApproveResearch).not.toHaveBeenCalled()
      expect(mockResumeApprovalStream).not.toHaveBeenCalled()
    })

    it('source=chat 调用 resumeApprovalStream（SSE 流）', async () => {
      // 准备 session 数据：最后一条是 INTERRUPTED 的 assistant 消息
      mockSessionStore.sessions = [{
        id: 'session-1',
        messages: [
          { id: 'msg-1', role: 'user', content: 'hi' },
          { id: 'msg-2', role: 'assistant', content: '', streamState: StreamState.INTERRUPTED },
        ],
      }]

      store.handleApprovalEvent(
        { interrupt_id: 'int-chat', tool_name: 'shell', state: 'pending' },
        { source: 'chat', sessionId: 'session-1' }
      )

      const approval = { interrupt_id: 'int-chat' }
      await store.executeApproval(approval, true, null, {})

      // 验证调用了 resumeApprovalStream
      expect(mockResumeApprovalStream).toHaveBeenCalledWith(
        'int-chat',
        expect.objectContaining({ approved: true }),
        expect.anything()
      )
      // 验证调用了 readSSEStream 消费流
      expect(mockReadSSEStream).toHaveBeenCalled()
      // 验证 syncStore.startStreaming 被调用
      expect(mockSyncStore.startStreaming).toHaveBeenCalledWith('session-1')
    })
  })

  describe('approvalQueue 串行执行', () => {
    it('多个聊天审批串行执行，不并发', async () => {
      mockSessionStore.sessions = [{
        id: 'session-1',
        messages: [
          { id: 'msg-1', role: 'assistant', content: '', streamState: StreamState.INTERRUPTED },
        ],
      }]

      // 让 readSSEStream 返回可控的 Promise
      let resolve1, resolve2
      mockReadSSEStream
        .mockImplementationOnce(() => new Promise(r => { resolve1 = r }))
        .mockImplementationOnce(() => new Promise(r => { resolve2 = r }))

      // 入队两个审批
      store.handleApprovalEvent(
        { interrupt_id: 'int-q1', tool_name: 'shell', state: 'pending' },
        { source: 'chat', sessionId: 'session-1' }
      )
      store.handleApprovalEvent(
        { interrupt_id: 'int-q2', tool_name: 'shell', state: 'pending' },
        { source: 'chat', sessionId: 'session-1' }
      )

      // 同时发起两个审批执行
      const p1 = store.executeApproval({ interrupt_id: 'int-q1' }, true, null, {})
      const p2 = store.executeApproval({ interrupt_id: 'int-q2' }, true, null, {})

      // 第一个开始执行，第二个排队等待
      await vi.waitFor(() => {
        expect(mockReadSSEStream).toHaveBeenCalledTimes(1)
      })
      // 此时第二个尚未执行
      expect(mockReadSSEStream).toHaveBeenCalledTimes(1)

      // 完成第一个，第二个开始
      resolve1()
      await vi.waitFor(() => {
        expect(mockReadSSEStream).toHaveBeenCalledTimes(2)
      })

      // 完成第二个
      resolve2()
      await p1
      await p2

      // 验证两次 readSSEStream 调用（串行）
      expect(mockReadSSEStream).toHaveBeenCalledTimes(2)
    })
  })

  describe('restoreFromSSEHistory', () => {
    it('恢复 pending 审批到 pendingApprovals', () => {
      const approvalData = { interrupt_id: 'int-r-1', state: 'pending', tool_name: 'search' }
      store.restoreFromSSEHistory(approvalData, 'task-1', 'session-1')

      expect(store.pendingApprovals.has('int-r-1')).toBe(true)
      const entry = store.pendingApprovals.get('int-r-1')
      expect(entry.source).toBe('deep_research')
      expect(entry.taskId).toBe('task-1')
      expect(entry.sessionId).toBe('session-1')
    })

    it('已终态审批不加入 pendingApprovals', () => {
      const approvalData = { interrupt_id: 'int-r-2', state: 'approved' }
      store.restoreFromSSEHistory(approvalData, 'task-1', 'session-1')

      expect(store.pendingApprovals.has('int-r-2')).toBe(false)
    })

    it('重复恢复同一审批时跳过', () => {
      const approvalData = { interrupt_id: 'int-r-3', state: 'pending' }
      store.restoreFromSSEHistory(approvalData, 'task-1', 'session-1')
      store.restoreFromSSEHistory(approvalData, 'task-1', 'session-1')

      expect(store.pendingApprovals.has('int-r-3')).toBe(true)
    })
  })
})
