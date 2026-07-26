import { describe, it, expect, beforeEach, vi } from 'vitest'
import { StreamState } from '@/types'

// Mock session store
const mockSessionStore = {
  setStreamStateToLastMessage: vi.fn(),
  waitForSyncLock: vi.fn().mockResolvedValue(undefined),
  flushPendingSync: vi.fn().mockResolvedValue(undefined),
  syncLastMessageToBackend: vi.fn().mockResolvedValue(undefined),
  clearToolSyncTimer: vi.fn(),
  clearSyncSignature: vi.fn(),
  touchSessionUpdatedAt: vi.fn(),
}

vi.mock('@/stores/session', () => ({
  useSessionStore: () => mockSessionStore,
}))

// Mock sync store
const mockSyncStore = {
  stopStreaming: vi.fn(),
}

vi.mock('@/stores/sync', () => ({
  useSyncStore: () => mockSyncStore,
}))

// Mock chat API
const mockChatFinalize = vi.fn().mockResolvedValue({})
vi.mock('@/api/chat', () => ({
  chatFinalize: (...args) => mockChatFinalize(...args),
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

import { useStreamFinalizer } from '../useStreamFinalizer'

describe('useStreamFinalizer', () => {
  let finalizeStream
  let markInterrupted

  beforeEach(() => {
    vi.clearAllMocks()
    // 重置默认实现
    mockSessionStore.waitForSyncLock.mockResolvedValue(undefined)
    mockSessionStore.flushPendingSync.mockResolvedValue(undefined)
    mockSessionStore.syncLastMessageToBackend.mockResolvedValue(undefined)
    mockChatFinalize.mockResolvedValue({})
    const { finalizeStream: f, markInterrupted: m } = useStreamFinalizer()
    finalizeStream = f
    markInterrupted = m
  })

  it('finalizeStream 状态流转 FINALIZING → SYNCING → COMPLETED', async () => {
    const sessionId = 'session-1'
    const lastMsg = { id: 'msg-1', backendId: 100, streamState: StreamState.STREAMING }

    const result = await finalizeStream(sessionId, lastMsg)

    expect(result).toBe(true)
    // 验证状态调用顺序：FINALIZING → SYNCING → COMPLETED
    expect(mockSessionStore.setStreamStateToLastMessage).toHaveBeenNthCalledWith(1, sessionId, StreamState.FINALIZING)
    expect(mockSessionStore.setStreamStateToLastMessage).toHaveBeenNthCalledWith(2, sessionId, StreamState.SYNCING)
    expect(mockSessionStore.setStreamStateToLastMessage).toHaveBeenNthCalledWith(3, sessionId, StreamState.COMPLETED)
    // 验证同步流程：waitForSyncLock 调用 3 次
    expect(mockSessionStore.waitForSyncLock).toHaveBeenCalledTimes(3)
    expect(mockSessionStore.waitForSyncLock).toHaveBeenCalledWith(sessionId)
    // flushPendingSync 调用 1 次
    // flushPendingSync(sessionId, messageIndex !== undefined ? { messageIndex } : {})
    // 无 messageIndex 时传入 {}
    expect(mockSessionStore.flushPendingSync).toHaveBeenCalledTimes(1)
    expect(mockSessionStore.flushPendingSync).toHaveBeenCalledWith(sessionId, {})
    // syncLastMessageToBackend 调用 1 次
    expect(mockSessionStore.syncLastMessageToBackend).toHaveBeenCalledWith(sessionId, { allowCreate: false })
    // clearToolSyncTimer 在 FINALIZING 阶段调用
    expect(mockSessionStore.clearToolSyncTimer).toHaveBeenCalledWith(sessionId)
    // touchSessionUpdatedAt 在 COMPLETED 后调用
    expect(mockSessionStore.touchSessionUpdatedAt).toHaveBeenCalledWith(sessionId)
    // chatFinalize 调用
    expect(mockChatFinalize).toHaveBeenCalledWith(sessionId, 100)
    // syncStore.stopStreaming 在 finally 中调用
    expect(mockSyncStore.stopStreaming).toHaveBeenCalledWith(sessionId)
  })

  it('finalizeStream 状态守卫：COMPLETED 跳过最终化', async () => {
    const lastMsg = { id: 'msg-1', streamState: StreamState.COMPLETED }
    const result = await finalizeStream('session-1', lastMsg)
    expect(result).toBe(false)
    expect(mockSessionStore.setStreamStateToLastMessage).not.toHaveBeenCalled()
    expect(mockChatFinalize).not.toHaveBeenCalled()
    expect(mockSyncStore.stopStreaming).not.toHaveBeenCalled()
  })

  it('finalizeStream 状态守卫：INTERRUPTED 跳过最终化', async () => {
    const lastMsg = { id: 'msg-1', streamState: StreamState.INTERRUPTED }
    const result = await finalizeStream('session-1', lastMsg)
    expect(result).toBe(false)
    expect(mockSessionStore.setStreamStateToLastMessage).not.toHaveBeenCalled()
    expect(mockChatFinalize).not.toHaveBeenCalled()
  })

  it('finalizeStream 状态守卫：ERROR 跳过最终化', async () => {
    const lastMsg = { id: 'msg-1', streamState: StreamState.ERROR }
    const result = await finalizeStream('session-1', lastMsg)
    expect(result).toBe(false)
    expect(mockSessionStore.setStreamStateToLastMessage).not.toHaveBeenCalled()
    expect(mockChatFinalize).not.toHaveBeenCalled()
  })

  it('finalizeStream chatFinalize 重试 3 次（前两次失败，第三次成功）', async () => {
    vi.useFakeTimers()
    mockChatFinalize
      .mockRejectedValueOnce(new Error('fail-1'))
      .mockRejectedValueOnce(new Error('fail-2'))
      .mockResolvedValueOnce({ ok: true })

    const lastMsg = { id: 'msg-1', backendId: 100, streamState: StreamState.STREAMING }
    const promise = finalizeStream('session-1', lastMsg)

    // 推进所有重试延迟（1000ms + 2000ms）
    await vi.runAllTimersAsync()
    const result = await promise

    expect(result).toBe(true)
    expect(mockChatFinalize).toHaveBeenCalledTimes(3)
    // 最终状态仍为 COMPLETED
    expect(mockSessionStore.setStreamStateToLastMessage).toHaveBeenLastCalledWith('session-1', StreamState.COMPLETED)
    vi.useRealTimers()
  })

  it('markInterrupted 设置 INTERRUPTED + 清理 timer/signature + stopStreaming', () => {
    const sessionId = 'session-1'
    markInterrupted(sessionId)

    expect(mockSessionStore.setStreamStateToLastMessage).toHaveBeenCalledWith(sessionId, StreamState.INTERRUPTED)
    expect(mockSessionStore.clearToolSyncTimer).toHaveBeenCalledWith(sessionId)
    expect(mockSessionStore.clearSyncSignature).toHaveBeenCalledWith(sessionId)
    expect(mockSyncStore.stopStreaming).toHaveBeenCalledWith(sessionId)
  })

  it('markInterrupted 支持自定义 setStreamState', () => {
    const customSetState = vi.fn()
    markInterrupted('session-1', { setStreamState: customSetState })

    expect(customSetState).toHaveBeenCalledWith(StreamState.INTERRUPTED)
    // 仍应清理 timer/signature
    expect(mockSessionStore.clearToolSyncTimer).toHaveBeenCalledWith('session-1')
    expect(mockSessionStore.clearSyncSignature).toHaveBeenCalledWith('session-1')
    expect(mockSyncStore.stopStreaming).toHaveBeenCalledWith('session-1')
  })
})
