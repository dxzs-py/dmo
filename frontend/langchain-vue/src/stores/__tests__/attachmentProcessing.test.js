/**
 * chat store 附件处理进度状态流转测试
 *
 * 覆盖：
 * - 带附件发送 → attachmentProcessing 进入 preparing 阶段
 * - 无附件发送 → 保持 null
 * - 流结束（成功/失败/中止）→ finally 无条件清理
 * - 重新生成入口 → 兜底清理残留
 * - clearAll → 清理
 *
 * 运行方式（vitest）：
 *   npm run test -- src/stores/__tests__/attachmentProcessing.test.js
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

const sessionMock = vi.hoisted(() => {
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
  sessionMock.state.currentSessionId = 'session-a'
  sessionMock.state.sessions = []
  streamChatMock.mockResolvedValue({ success: true })
})

describe('sendMessage 附件进度状态', () => {
  it('带附件发送 → attachmentProcessing 进入 preparing 阶段', async () => {
    // streamChat 挂起，模拟流式进行中（finally 尚未执行）
    let resolveStream
    streamChatMock.mockImplementation(() => new Promise(resolve => { resolveStream = resolve }))
    const store = useChatStore()
    const sendPromise = store.sendMessage('帮我分析这份文档', {
      attachmentIds: [1, 2],
      attachments: [{ id: 1 }, { id: 2 }],
    })
    await vi.waitFor(() => expect(streamChatMock).toHaveBeenCalledTimes(1))
    expect(store.attachmentProcessing).toEqual({ stage: 'preparing', message: '正在处理附件...' })
    resolveStream({ success: true })
    await sendPromise
  })

  it('无附件发送 → attachmentProcessing 保持 null', async () => {
    const store = useChatStore()
    await store.sendMessage('普通消息')
    expect(store.attachmentProcessing).toBeNull()
  })

  it('流正常结束 → finally 无条件清理为 null', async () => {
    const store = useChatStore()
    await store.sendMessage('帮我分析这份文档', { attachmentIds: [1] })
    expect(store.attachmentProcessing).toBeNull()
    expect(store.isLoading).toBe(false)
  })

  it('流失败结束 → finally 仍无条件清理', async () => {
    streamChatMock.mockResolvedValue({ success: false, error: { message: '失败' } })
    const store = useChatStore()
    await store.sendMessage('帮我分析这份文档', { attachmentIds: [1] })
    expect(store.attachmentProcessing).toBeNull()
  })

  it('流中止结束 → finally 仍无条件清理', async () => {
    streamChatMock.mockResolvedValue({ success: false, aborted: true })
    const store = useChatStore()
    await store.sendMessage('帮我分析这份文档', { attachmentIds: [1] })
    expect(store.attachmentProcessing).toBeNull()
  })
})

describe('regenerateMessage 附件进度兜底', () => {
  it('重新生成入口清理残留的附件进度', async () => {
    const session = {
      id: 'session-a',
      messages: [
        { id: 'u1', role: 'user', content: '第一问' },
        { id: 'a1', role: 'assistant', content: '第一答' },
      ],
    }
    sessionMock.state.sessions = [session]
    sessionMock.state.currentSessionId = 'session-a'

    const store = useChatStore()
    // 模拟上一轮残留（真实场景来自历史赋值/异常路径）
    store.attachmentProcessing = { stage: 'preparing', message: '正在处理附件...' }

    await store.regenerateMessage(1)

    expect(store.attachmentProcessing).toBeNull()
  })
})
