/**
 * chat store 流式会话守卫回归测试（Task 3：D-2 / D-4）
 *
 * D-2：流式发起时在 store 流状态（_activeStream）捕获 sessionId，
 * stopStreaming 据此信令与标记——流式期间切换会话后点停止，只作用于
 * 发起流式的原会话（停止信令发原会话、只标记原会话末条 assistant）。
 *
 * D-4：sendMessage 守卫与 isLoading 置位原子化——无会话时并发双发，
 * 第二次调用被守卫拦截，createNewSession 仅调用一次（不再创建两个会话）。
 *
 * 运行方式（vitest）：
 *   npm run test -- src/stores/__tests__/chatStreamingSessionGuard.test.js
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { StreamState } from '@/types'

const sessionMock = vi.hoisted(() => {
  // state 用 getter/setter 暴露：chat.js 以 `sessionStore.currentSessionId`
  // / `sessionStore.sessions` 属性访问（Pinia 会解包 ref，纯对象 mock 需等价）
  const state = { currentSessionId: null, sessions: [] }
  const store = {
    get currentSessionId() {
      return state.currentSessionId
    },
    set currentSessionId(v) {
      state.currentSessionId = v
    },
    get sessions() {
      return state.sessions
    },
    set sessions(v) {
      state.sessions = v
    },
    selectedKnowledgeBase: null,
    selectedKnowledgeBases: [],
    createNewSession: vi.fn(async () => {
      // 模拟真实 createNewSession 的异步窗口（HTTP 往返）
      await new Promise(resolve => setTimeout(resolve, 0))
      state.currentSessionId = 'session-created'
      return state.currentSessionId
    }),
    setSelectedKnowledgeBase: vi.fn(async () => {}),
    finalizeLatestMessage: vi.fn(async () => {}),
    addMessageToSession: vi.fn(),
    getSessionMessages: vi.fn(() => []),
    updateLastMessage: vi.fn(),
    updateSessionTitle: vi.fn(),
    touchSessionUpdatedAt: vi.fn(),
    clearToolSyncTimer: vi.fn(),
    syncLastMessageToBackend: vi.fn(async () => {}),
    resetFinalizeTimer: vi.fn(),
    removeMessagePairFromSession: vi.fn(),
  }
  return { state, store }
})

const streamChatMock = vi.hoisted(() => vi.fn())

const chatAPIMock = vi.hoisted(() => ({
  stopStreaming: vi.fn(() => Promise.resolve()),
  streamMessage: vi.fn(),
  updateMessage: vi.fn(() => Promise.resolve()),
  getModes: vi.fn(() => Promise.resolve({ data: {} })),
  createSession: vi.fn(),
  deleteMessagePair: vi.fn(() => Promise.resolve()),
}))

vi.mock('@/composables/useStreamChat', () => ({
  useStreamChat: vi.fn(() => ({
    isStreaming: ref(false),
    abortController: ref(null),
    abort: vi.fn(),
    streamChat: streamChatMock,
    lastError: ref(null),
  })),
}))

vi.mock('@/composables/useStreamFinalizer', () => ({
  useStreamFinalizer: vi.fn(() => ({
    finalizeStream: vi.fn(async () => {}),
  })),
}))

vi.mock('@/stores/session', () => ({
  useSessionStore: vi.fn(() => sessionMock.store),
}))

vi.mock('@/stores/model', () => ({
  useModelStore: vi.fn(() => ({
    getModelConfig: () => ({
      providerId: null,
      modelName: null,
      specialParams: null,
      temperature: null,
      maxTokens: null,
    }),
    thinkingEnabled: false,
  })),
}))

vi.mock('@/stores/user', () => ({
  useUserStore: vi.fn(() => ({ isLoggedIn: true })),
}))

vi.mock('@/stores/approval', () => ({
  useApprovalStore: vi.fn(() => ({
    pendingApprovals: new Map(),
    clearAll: vi.fn(),
    executeApproval: vi.fn(),
  })),
}))

vi.mock('@/stores/sync', () => ({
  useSyncStore: vi.fn(() => ({
    startStreaming: vi.fn(),
    stopStreaming: vi.fn(),
  })),
}))

vi.mock('@/stores/chatDeepResearch', () => ({
  useChatDeepResearchStore: vi.fn(() => ({
    clearChatDeepResearchTask: vi.fn(),
    researchTaskId: null,
    researchContextInfo: null,
    clearChatResearchContext: vi.fn(),
    resetChatDeepResearch: vi.fn(),
    getResearchTaskIdForApproval: vi.fn(() => null),
  })),
}))

vi.mock('@/api/chat', () => ({
  chatAPI: chatAPIMock,
}))

vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

import { useChatStore } from '../chat.js'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  sessionMock.state.currentSessionId = null
  sessionMock.state.sessions = []
})

describe('D-2：切换会话后停止只影响原会话', () => {
  it('流式期间切换会话，stopStreaming 信令发往原会话且只标记原会话末条 assistant', async () => {
    const sessionA = {
      id: 'session-a',
      messages: [
        { id: 'u1', role: 'user', content: '你好' },
        { id: 'a1', role: 'assistant', content: '部分输出', isStreaming: true, streamState: StreamState.STREAMING },
      ],
    }
    const sessionB = {
      id: 'session-b',
      messages: [
        { id: 'u2', role: 'user', content: '第二个会话的问题' },
        { id: 'a2', role: 'assistant', content: '已完成的历史回答', isStreaming: false, streamState: StreamState.COMPLETED },
      ],
    }
    sessionMock.state.sessions = [sessionA, sessionB]
    sessionMock.state.currentSessionId = 'session-a'

    // streamChat 挂起，模拟流式进行中（waitForStreamEnd 未返回）
    let resolveStream
    streamChatMock.mockImplementation(() => new Promise(resolve => { resolveStream = resolve }))

    const store = useChatStore()
    const sendPromise = store.sendMessage('你好')
    await vi.waitFor(() => expect(streamChatMock).toHaveBeenCalledTimes(1))
    expect(store.isLoading).toBe(true)

    // 流式期间用户切换到会话 B
    sessionMock.state.currentSessionId = 'session-b'

    store.stopStreaming()

    // 停止信令发往发起流式的原会话 A（而非 currentSessionId=B）
    expect(chatAPIMock.stopStreaming).toHaveBeenCalledTimes(1)
    expect(chatAPIMock.stopStreaming).toHaveBeenCalledWith('session-a')

    // 只标记原会话 A 的末条 assistant 为 stopped 终态
    expect(sessionA.messages[1]).toMatchObject({
      stopped: true,
      isStreaming: false,
      streamState: StreamState.COMPLETED,
    })
    // 新会话 B 的末条 assistant 不被误标
    expect(sessionB.messages[1].stopped).toBeUndefined()
    expect(sessionB.messages[1].isStreaming).toBe(false)
    expect(sessionB.messages[1].streamState).toBe(StreamState.COMPLETED)

    // 收尾：释放挂起的流（aborted 路径），确认状态复位
    resolveStream({ success: false, aborted: true })
    await sendPromise
    expect(store.isLoading).toBe(false)
  })

  it('无活跃流时 stopStreaming 不发送停止信令、不标记任何会话消息', () => {
    const session = {
      id: 'session-a',
      messages: [
        { id: 'u1', role: 'user', content: '你好' },
        { id: 'a1', role: 'assistant', content: '历史回答', isStreaming: false, streamState: StreamState.COMPLETED },
      ],
    }
    sessionMock.state.sessions = [session]
    sessionMock.state.currentSessionId = 'session-a'

    const store = useChatStore()
    store.stopStreaming()

    expect(chatAPIMock.stopStreaming).not.toHaveBeenCalled()
    expect(session.messages[1].stopped).toBeUndefined()
    expect(session.messages[1].streamState).toBe(StreamState.COMPLETED)
  })

  it('流式正常结束后 stopStreaming 不再发送停止信令（流状态已清理）', async () => {
    const session = {
      id: 'session-a',
      messages: [
        { id: 'u1', role: 'user', content: '你好' },
        { id: 'a1', role: 'assistant', content: '回答', isStreaming: true, streamState: StreamState.STREAMING },
      ],
    }
    sessionMock.state.sessions = [session]
    sessionMock.state.currentSessionId = 'session-a'
    streamChatMock.mockResolvedValue({ success: true })

    const store = useChatStore()
    await store.sendMessage('你好')

    // finally 已清理 _activeStream：迟到的停止点击不再发信令/误标消息
    store.stopStreaming()
    expect(chatAPIMock.stopStreaming).not.toHaveBeenCalled()
    expect(session.messages[1].stopped).toBeUndefined()
  })
})

describe('D-4：无会话并发双发只建一个会话', () => {
  it('守卫与 isLoading 置位原子：并发 sendMessage 只通过一次守卫，createNewSession 仅调用一次', async () => {
    sessionMock.state.currentSessionId = null
    sessionMock.state.sessions = []
    streamChatMock.mockResolvedValue({ success: true })

    const store = useChatStore()
    // 并发双发：第二次调用必须在第一次置位 isLoading 后被守卫拦截
    const [p1, p2] = [store.sendMessage('第一条'), store.sendMessage('第二条')]
    await Promise.all([p1, p2])

    expect(sessionMock.store.createNewSession).toHaveBeenCalledTimes(1)
    expect(store.isLoading).toBe(false)
  })

  it('单发且创建会话成功：全流程正常完成，isLoading 复位', async () => {
    sessionMock.state.currentSessionId = null
    sessionMock.state.sessions = []
    streamChatMock.mockResolvedValue({ success: true })

    const store = useChatStore()
    await store.sendMessage('只有一条')

    expect(sessionMock.store.createNewSession).toHaveBeenCalledTimes(1)
    expect(store.isLoading).toBe(false)
    expect(streamChatMock).toHaveBeenCalledTimes(1)
    // 流式 payload 携带新建会话 ID
    expect(streamChatMock.mock.calls[0][0].sessionId).toBe('session-created')
  })
})
