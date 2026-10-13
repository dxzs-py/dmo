/**
 * waitForStreamEnd 会话删除退出条件测试（D-7 回归）
 *
 * 背景：流式期间会话被删除时，等待的消息 target 永远找不到，轮询 interval
 * 与事件订阅永不清理（内存泄漏 + 幽灵等待）。修复后 poll 检测到会话不在
 * session store 中立即以 'deleted' 结束并回收 interval / 订阅。
 *
 * mock 说明：
 * - '@/stores/session'：sessions 数组由测试直接控制（模拟删除动作）
 * - '../useRealtimeSync'：订阅/退订 spy，验证资源回收
 * - '@/api/chat'：useStreamChat 顶层引入，waitForStreamEnd 不触达，mock 断链
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { StreamState } from '@/types'

const h = vi.hoisted(() => {
  const state = { sessions: [] }
  const unsubscribe = vi.fn()
  const subscribeSession = vi.fn(() => unsubscribe)
  return { state, unsubscribe, subscribeSession }
})

vi.mock('@/stores/session', () => ({
  // getter 模拟 setup store 的 ref 解包：每次访问拿到当前数组（与真实 store 一致，
  // 否则测试中重新赋值 h.state.sessions 后 store 内捕获的仍是旧数组）
  useSessionStore: () => ({
    get sessions() {
      return h.state.sessions
    },
  }),
}))

vi.mock('../useRealtimeSync', () => ({
  useRealtimeSync: () => ({ subscribeSession: h.subscribeSession }),
}))

vi.mock('@/api/chat', () => ({
  chatAPI: {},
}))

import { waitForStreamEnd } from '../useStreamChat.js'

describe('waitForStreamEnd（D-7 会话删除退出）', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    h.state.sessions = []
    h.unsubscribe.mockClear()
    h.subscribeSession.mockClear()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  const makeSession = (id, messages = []) => ({ id, messages })

  it('等待期间会话被删除：立即以 deleted 结束并退订', async () => {
    h.state.sessions = [makeSession('s1', [])]

    const promise = waitForStreamEnd('s1', 'm1')
    // 消息尚未同步进 store：保持等待
    await vi.advanceTimersByTimeAsync(900)
    expect(h.unsubscribe).not.toHaveBeenCalled()

    // 模拟用户删除会话（session store 移除），下一个轮询 tick 检出并退出
    h.state.sessions = []
    await vi.advanceTimersByTimeAsync(300)
    await expect(promise).resolves.toBe('deleted')
    expect(h.unsubscribe).toHaveBeenCalledTimes(1)
  })

  it('开始等待时会话已不存在：立即 deleted，不再轮询', async () => {
    const promise = waitForStreamEnd('s-missing', null)
    await expect(promise).resolves.toBe('deleted')
    expect(h.unsubscribe).toHaveBeenCalledTimes(1)
  })

  it('会话存在且消息到达 COMPLETED：正常结束（既有语义不回归）', async () => {
    h.state.sessions = [
      makeSession('s2', [{ id: 'm2', role: 'assistant', streamState: StreamState.COMPLETED }]),
    ]
    await expect(waitForStreamEnd('s2', 'm2')).resolves.toBe('completed')
    expect(h.unsubscribe).toHaveBeenCalledTimes(1)
  })

  it('会话存在但 target 未就绪：持续等待不被误判为 deleted', async () => {
    h.state.sessions = [makeSession('s3', [{ id: 'other', role: 'user', content: 'q' }])]

    const promise = waitForStreamEnd('s3', 'm3')
    await vi.advanceTimersByTimeAsync(900)

    // 仍挂起：消息补齐后由轮询检出 COMPLETED 并 settle
    h.state.sessions = [
      makeSession('s3', [
        { id: 'other', role: 'user', content: 'q' },
        { id: 'm3', role: 'assistant', streamState: StreamState.COMPLETED },
      ]),
    ]
    await vi.advanceTimersByTimeAsync(300)
    await expect(promise).resolves.toBe('completed')
  })
})
