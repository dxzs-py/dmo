/**
 * messageFlow 切片会话删除回收测试（D-7 回归）
 *
 * 背景：_syncLocks / _lastSyncSignatures / _toolSyncTimers 按会话键缓存，
 * 删会话前只增不清（内存泄漏）。修复后新增 cleanupSessionSyncState 回收入口，
 * 由 sessionCrud.deleteSession / removeSession 调用。
 *
 * 本文件直接实例化 messageFlow 切片（不经 Pinia 组装），验证回收行为：
 * - 同步签名缓存清理：删除后相同内容可再次 PATCH（签名去重被重置）
 * - 工具同步定时器清理：回收后 debounced 同步不再触发
 *
 * mock 说明：
 * - '../../api/chat'：网络边界断链
 * - '../user'：切片创建期调用 useUserStore()，mock 避免 Pinia 依赖
 * - '../session/helpers'：localStorage 相关工具在 node 环境不可用，mock 断链
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref } from 'vue'

vi.mock('../../api/chat', () => ({
  chatAPI: {
    addMessage: vi.fn().mockResolvedValue({ data: { data: {} } }),
    updateMessage: vi.fn().mockResolvedValue({ data: { data: {} } }),
    deleteSession: vi.fn().mockResolvedValue({ data: { data: {} } }),
  },
}))

vi.mock('../user', () => ({
  useUserStore: () => ({ isLoggedIn: true }),
}))

vi.mock('../session/helpers', () => ({
  _safeRemoveLocalStorage: vi.fn(),
  _versionSelectionKey: vi.fn(() => 'key'),
}))

import { chatAPI } from '../../api/chat'
import { createMessageFlowSlice } from '../session/messageFlow'

const runtimeDeps = {
  _removeSessionToolCallState: vi.fn(),
  _pruneToolCallsByRemovedMessageIds: vi.fn(),
  clearFinalizeTimer: vi.fn(),
}

function createSlice() {
  const sessions = ref([
    {
      id: 's1',
      messages: [
        {
          id: 'm1',
          backendId: 'b1',
          role: 'assistant',
          content: 'hello',
          versions: [{ content: 'hello' }],
          currentVersion: 0,
          toolCalls: [],
          sources: [],
        },
      ],
      messageCount: 1,
    },
  ])
  return createMessageFlowSlice(
    { sessions, currentSessionId: ref('s1') },
    runtimeDeps
  )
}

describe('messageFlow.cleanupSessionSyncState（D-7）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('回收后同步签名缓存清空：相同内容可再次触发 PATCH', async () => {
    const slice = createSlice()

    await slice.syncLastMessageToBackend('s1')
    expect(chatAPI.updateMessage).toHaveBeenCalledTimes(1)

    // 签名未变：跳过 PATCH（既有去重语义）
    await slice.syncLastMessageToBackend('s1')
    expect(chatAPI.updateMessage).toHaveBeenCalledTimes(1)

    // 回收后签名缓存清空：再次同步重新 PATCH
    slice.cleanupSessionSyncState('s1')
    await slice.syncLastMessageToBackend('s1')
    expect(chatAPI.updateMessage).toHaveBeenCalledTimes(2)
  })

  it('回收后 pending 的 debounced 工具同步定时器被取消', async () => {
    vi.useFakeTimers()
    const slice = createSlice()

    slice._debouncedToolSync('s1')
    // 回收发生在 300ms 去抖窗口内
    slice.cleanupSessionSyncState('s1')
    await vi.advanceTimersByTimeAsync(400)

    expect(chatAPI.updateMessage).not.toHaveBeenCalled()
    vi.useRealTimers()
  })

  it('未回收时 debounced 工具同步正常触发（定时器语义不回归）', async () => {
    vi.useFakeTimers()
    const slice = createSlice()

    slice._debouncedToolSync('s1')
    await vi.advanceTimersByTimeAsync(400)

    expect(chatAPI.updateMessage).toHaveBeenCalledTimes(1)
    vi.useRealTimers()
  })
})
