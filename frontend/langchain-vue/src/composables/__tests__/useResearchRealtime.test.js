import { describe, it, expect, beforeEach, vi } from 'vitest'

const mockSubscribeTask = vi.fn()
const mockSubscribeSession = vi.fn()
vi.mock('@/composables/useRealtimeSync', () => ({
  useRealtimeSync: () => ({
    subscribeTask: (...args) => mockSubscribeTask(...args),
    subscribeSession: (...args) => mockSubscribeSession(...args),
  }),
}))

const mockHandleRealtimeEvent = vi.fn()
vi.mock('@/stores/sync', () => ({
  useSyncStore: () => ({ handleRealtimeEvent: mockHandleRealtimeEvent }),
}))

vi.mock('@/utils/logger', () => ({
  logger: { log: () => {}, info: () => {}, warn: () => {}, error: () => {}, debug: () => {} },
}))

import { useResearchRealtime } from '../useResearchRealtime'

describe('useResearchRealtime', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockSubscribeTask.mockImplementation(() => () => {})
    mockSubscribeSession.mockImplementation(() => () => {})
  })

  it('subscribeRealtimeForTask 为 task + session 订阅', () => {
    const { subscribeRealtimeForTask, realtimeUnsubscribers } = useResearchRealtime()

    subscribeRealtimeForTask({ task_id: 't-1', chat_session_id: 's-1' })

    expect(mockSubscribeTask).toHaveBeenCalledWith('t-1', mockHandleRealtimeEvent, { replayFromSeq: 0 })
    expect(mockSubscribeSession).toHaveBeenCalledWith('s-1', mockHandleRealtimeEvent, { replayFromSeq: 0 })
    expect(realtimeUnsubscribers.value).toHaveLength(2)
  })

  it('subscribeRealtimeForTask 无 session 时仅订阅 task', () => {
    const { subscribeRealtimeForTask, realtimeUnsubscribers } = useResearchRealtime()

    subscribeRealtimeForTask({ task_id: 't-1' })

    expect(mockSubscribeTask).toHaveBeenCalledWith('t-1', mockHandleRealtimeEvent, { replayFromSeq: 0 })
    expect(mockSubscribeSession).not.toHaveBeenCalled()
    expect(realtimeUnsubscribers.value).toHaveLength(1)
  })

  it('subscribeRealtimeForTask 入参为 null 时直接返回', () => {
    const { subscribeRealtimeForTask, realtimeUnsubscribers } = useResearchRealtime()

    subscribeRealtimeForTask(null)

    expect(mockSubscribeTask).not.toHaveBeenCalled()
    expect(realtimeUnsubscribers.value).toHaveLength(0)
  })

  it('subscribeRealtimeForTask 幂等：同 task + session 跳过', () => {
    const { subscribeRealtimeForTask } = useResearchRealtime()

    subscribeRealtimeForTask({ task_id: 't-1', session_id: 's-1' })
    subscribeRealtimeForTask({ task_id: 't-1', session_id: 's-1' })

    expect(mockSubscribeTask).toHaveBeenCalledTimes(1)
    expect(mockSubscribeSession).toHaveBeenCalledTimes(1)
  })

  it('subscribeRealtimeForTask 切换任务时清理上一任务订阅', () => {
    const { subscribeRealtimeForTask, realtimeUnsubscribers } = useResearchRealtime()

    subscribeRealtimeForTask({ task_id: 't-1' })
    expect(realtimeUnsubscribers.value).toHaveLength(1)

    subscribeRealtimeForTask({ task_id: 't-2' })
    // 切换任务先清理（unsubscribers 调用），再订阅新任务
    expect(realtimeUnsubscribers.value).toHaveLength(1)
    expect(mockSubscribeTask).toHaveBeenNthCalledWith(2, 't-2', mockHandleRealtimeEvent, { replayFromSeq: 0 })
  })

  it('clearRealtimeSubscriptions 调用所有 unsubscribe 函数', () => {
    const unsubTask = vi.fn()
    const unsubSession = vi.fn()
    mockSubscribeTask.mockReturnValue(unsubTask)
    mockSubscribeSession.mockReturnValue(unsubSession)

    const { subscribeRealtimeForTask, clearRealtimeSubscriptions, realtimeUnsubscribers } = useResearchRealtime()
    subscribeRealtimeForTask({ task_id: 't-1', chat_session_id: 's-1' })
    expect(realtimeUnsubscribers.value).toHaveLength(2)

    clearRealtimeSubscriptions()

    expect(unsubTask).toHaveBeenCalled()
    expect(unsubSession).toHaveBeenCalled()
    expect(realtimeUnsubscribers.value).toHaveLength(0)
  })

  it('clearRealtimeSubscriptions 容错：unsubscribe 抛错时不中断', () => {
    const unsubThrow = vi.fn(() => { throw new Error('取消订阅失败') })
    const unsubOk = vi.fn()
    mockSubscribeTask.mockReturnValue(unsubThrow)
    mockSubscribeSession.mockReturnValue(unsubOk)

    const { subscribeRealtimeForTask, clearRealtimeSubscriptions } = useResearchRealtime()
    subscribeRealtimeForTask({ task_id: 't-1', chat_session_id: 's-1' })

    expect(() => clearRealtimeSubscriptions()).not.toThrow()
    expect(unsubOk).toHaveBeenCalled()
  })

  it('session_id 与 chat_session_id 均支持作为 session 标识', () => {
    const { subscribeRealtimeForTask } = useResearchRealtime()

    subscribeRealtimeForTask({ task_id: 't-1', chat_session_id: 's-chat' })
    expect(mockSubscribeSession).toHaveBeenNthCalledWith(1, 's-chat', mockHandleRealtimeEvent, { replayFromSeq: 0 })

    subscribeRealtimeForTask({ task_id: 't-2', session_id: 's-direct' })
    expect(mockSubscribeSession).toHaveBeenNthCalledWith(2, 's-direct', mockHandleRealtimeEvent, { replayFromSeq: 0 })
  })
})
