import { describe, it, expect, beforeEach, vi } from 'vitest'

// Mock approval store
const mockRestoreFromSSEHistory = vi.fn()
vi.mock('@/stores/approval', () => ({
  useApprovalStore: () => ({
    restoreFromSSEHistory: mockRestoreFromSSEHistory,
  }),
}))

// Mock useSSEConnection - 捕获 connect 传入的 options 以便触发 onEvent
let capturedOptions = {}
const mockConnect = vi.fn()
const mockDisconnectAll = vi.fn()
vi.mock('@/composables/useSSEConnection', () => ({
  useSSEConnection: () => ({
    connect: mockConnect,
    disconnectAll: mockDisconnectAll,
  }),
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

import { useResearchSSE } from '../useResearchSSE'

describe('useResearchSSE', () => {
  let sse

  beforeEach(() => {
    vi.clearAllMocks()
    capturedOptions = {}
    mockConnect.mockImplementation(async (url, options) => {
      capturedOptions = options
      // 模拟连接建立成功，否则 isConnected 保持 false 导致重复连接跳过逻辑失效
      if (options.onStatusChange) {
        options.onStatusChange('connected', null)
      }
      return { connectionId: 'test-conn', status: { value: 'connected' } }
    })
    sse = useResearchSSE()
  })

  it('connect 建立 SSE 连接', async () => {
    await sse.connect('task-1', {})

    expect(mockConnect).toHaveBeenCalledWith(
      '/research/task-1/stream/',
      expect.objectContaining({
        connectionId: 'research-task-1',
        fetchOptions: { injectTokenQuery: true },
      })
    )
    expect(sse.isConnected.value).toBe(true)
    expect(sse.currentTaskId.value).toBe('task-1')
  })

  it('approval_history 事件委托 restoreFromSSEHistory（单条）', async () => {
    await sse.connect('task-1', { sessionId: 'session-1' })

    const historyItem = { interrupt_id: 'int-h-1', state: 'pending' }
    capturedOptions.onEvent({ type: 'approval_history', data: historyItem, task_id: 'task-1' })

    expect(mockRestoreFromSSEHistory).toHaveBeenCalledWith(historyItem, 'task-1', 'session-1')
  })

  it('approval_history 事件委托 restoreFromSSEHistory（批量）', async () => {
    await sse.connect('task-1', { sessionId: 'session-1' })

    const historyItems = [
      { interrupt_id: 'int-h-1', state: 'pending' },
      { interrupt_id: 'int-h-2', state: 'pending' },
    ]
    capturedOptions.onEvent({ type: 'approval_history', data: historyItems, task_id: 'task-1' })

    expect(mockRestoreFromSSEHistory).toHaveBeenCalledTimes(2)
    expect(mockRestoreFromSSEHistory).toHaveBeenNthCalledWith(1, historyItems[0], 'task-1', 'session-1')
    expect(mockRestoreFromSSEHistory).toHaveBeenNthCalledWith(2, historyItems[1], 'task-1', 'session-1')
  })

  it('status_change 事件回调 onStatus', async () => {
    const onStatus = vi.fn()
    await sse.connect('task-1', { onStatus })

    const statusEvent = { type: 'status_change', status: 'running' }
    capturedOptions.onEvent(statusEvent)
    expect(onStatus).toHaveBeenCalledWith(statusEvent)
  })

  it('status_change 为 completed 时返回 false 中断流读取', async () => {
    const onStatus = vi.fn()
    await sse.connect('task-1', { onStatus })

    const result = capturedOptions.onEvent({ type: 'status_change', status: 'completed' })
    expect(result).toBe(false)
  })

  it('status_change 为 failed 时返回 false 中断流读取', async () => {
    const onStatus = vi.fn()
    await sse.connect('task-1', { onStatus })

    const result = capturedOptions.onEvent({ type: 'status_change', status: 'failed' })
    expect(result).toBe(false)
  })

  it('error 事件回调 onError', async () => {
    const onError = vi.fn()
    await sse.connect('task-1', { onError })

    const errorData = { message: 'something went wrong' }
    capturedOptions.onEvent({ type: 'error', data: errorData })
    expect(onError).toHaveBeenCalledWith(errorData)
  })

  it('普通消息事件回调 onMessage', async () => {
    const onMessage = vi.fn()
    await sse.connect('task-1', { onMessage })

    const stepEvent = { type: 'step_update', data: { step: 'searching' } }
    capturedOptions.onEvent(stepEvent)
    expect(onMessage).toHaveBeenCalledWith(stepEvent)
  })

  it('disconnect 清理资源', async () => {
    await sse.connect('task-1', {})
    expect(sse.isConnected.value).toBe(true)
    expect(sse.currentTaskId.value).toBe('task-1')

    await sse.disconnect()

    expect(mockDisconnectAll).toHaveBeenCalled()
    expect(sse.isConnected.value).toBe(false)
    expect(sse.currentTaskId.value).toBe(null)
  })

  it('重复连接同一任务时跳过', async () => {
    await sse.connect('task-1', {})
    expect(mockConnect).toHaveBeenCalledTimes(1)

    // 再次连接同一任务，应跳过
    await sse.connect('task-1', {})
    expect(mockConnect).toHaveBeenCalledTimes(1)
  })
})
