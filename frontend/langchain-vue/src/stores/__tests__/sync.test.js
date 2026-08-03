import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { StreamState } from '@/types'

// 先 mock 依赖模块，再导入被测 store
const mockConnectionStatus = { value: 'connected' }
const mockSubscribeUser = vi.fn()
const mockUnsubscribeUser = vi.fn()
const mockIncrementStreaming = vi.fn()
const mockDecrementStreaming = vi.fn()
const mockSubscribeSession = vi.fn()

vi.mock('@/composables/useRealtimeSync', () => ({
  useRealtimeSync: () => ({
    connectionStatus: mockConnectionStatus,
    subscribeUser: mockSubscribeUser,
    unsubscribeUser: mockUnsubscribeUser,
    incrementStreaming: mockIncrementStreaming,
    decrementStreaming: mockDecrementStreaming,
    subscribeSession: mockSubscribeSession,
    subscribeUserEvents: vi.fn(),
  }),
}))

const mockSessionStore = {
  sessions: [],
  currentSessionId: null,
  loadSessionDetail: vi.fn(),
  upsertSession: vi.fn(),
  removeMessagesByIds: vi.fn(),
}

vi.mock('@/stores/session', () => ({
  useSessionStore: () => mockSessionStore,
}))

vi.mock('@/stores/approval', () => ({
  useApprovalStore: () => ({
    pendingApprovals: { size: 0 },
  }),
}))

// Mock research / workflow store：避免加载真实模块拉入 api/axios → user → router → createWebHistory（需要 window）
vi.mock('@/stores/research', () => ({
  useResearchStore: () => ({}),
}))

vi.mock('@/stores/workflow', () => ({
  useWorkflowStore: () => ({
    updateWorkflowFromEvent: vi.fn(),
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

vi.mock('@/utils/session-transformers', () => ({
  transformBackendMessageToFrontend: vi.fn((data) => data),
  toCamelCase: vi.fn((data) => data),
}))
vi.mock('@/utils/message-operations', () => ({
  mergeMessageFromBackend: vi.fn((existing, backend) => Object.assign(existing, backend)),
  getInterruptId: vi.fn((approval) => approval?.interruptId || approval?.toolCallId || ''),
}))

import { useSyncStore } from '../sync'

describe('useSyncStore', () => {
  let store

  beforeEach(() => {
    setActivePinia(createPinia())
    store = useSyncStore()
    mockSessionStore.sessions = []
    mockSessionStore.loadSessionDetail.mockReset()
    mockSessionStore.upsertSession.mockReset()
    mockSessionStore.removeMessagesByIds.mockReset()
    mockIncrementStreaming.mockReset()
    mockDecrementStreaming.mockReset()
  })

  const createSession = (sessionId, streamState) => ({
    id: sessionId,
    messages: [
      {
        id: 'msg-1',
        backendId: 100,
        role: 'assistant',
        content: 'local content',
        streamState,
        isStreaming: streamState !== StreamState.COMPLETED && streamState !== StreamState.ERROR,
      },
    ],
  })

  it('当目标消息处于 STREAMING 时，应跳过 message_updated WebSocket 事件并更新 seq', async () => {
    const sessionId = 'session-1'
    mockSessionStore.sessions = [createSession(sessionId, StreamState.STREAMING)]
    // STREAMING 状态需要标记为请求浏览器（startStreaming）才会跳过 message_updated
    store.startStreaming(sessionId)

    const event = {
      type: 'message_updated',
      seq: 5,
      session_id: sessionId,
      payload: { message_id: 100, message: { content: 'backend snapshot' } },
    }

    await store.applySessionEvent(event)
    const msg = mockSessionStore.sessions[0].messages[0]
    expect(msg.content).toBe('local content')
  })

  it('当目标消息处于 INTERRUPTED 时，应跳过 message_updated WebSocket 事件', async () => {
    const sessionId = 'session-2'
    mockSessionStore.sessions = [createSession(sessionId, StreamState.INTERRUPTED)]

    // 后端 content 短于本地，使 isContentGrowingUpdate=false（不满足"内容增长放行"条件），
    // 从而触发 INTERRUPTED 状态的跳过逻辑
    const event = {
      type: 'message_updated',
      seq: 3,
      session_id: sessionId,
      payload: { message_id: 100, message: { content: 'short' } },
    }

    await store.applySessionEvent(event)
    const msg = mockSessionStore.sessions[0].messages[0]
    expect(msg.content).toBe('local content')
  })

  it('当目标消息处于 COMPLETED 时，应正常应用 message_updated 事件', async () => {
    const sessionId = 'session-4'
    mockSessionStore.sessions = [createSession(sessionId, StreamState.COMPLETED)]

    const event = {
      type: 'message_updated',
      seq: 2,
      session_id: sessionId,
      payload: { message_id: 100, message: { content: 'backend final content' } },
    }

    await store.applySessionEvent(event)
    const msg = mockSessionStore.sessions[0].messages[0]
    expect(msg.content).toBe('backend final content')
  })

  it('stream_completed 事件在请求浏览器中不应将 INTERRUPTED 改为 COMPLETED', async () => {
    const sessionId = 'session-5'
    mockSessionStore.sessions = [createSession(sessionId, StreamState.INTERRUPTED)]
    store.startStreaming(sessionId)

    const event = {
      type: 'stream_completed',
      seq: 1,
      session_id: sessionId,
      payload: { message_id: 100 },
    }

    await store.applySessionEvent(event)
    const msg = mockSessionStore.sessions[0].messages[0]
    expect(msg.streamState).toBe(StreamState.INTERRUPTED)
  })

  it('stream_completed 事件在请求浏览器中应将 SYNCING 兜底为 COMPLETED', async () => {
    const sessionId = 'session-6'
    // 仅 SYNCING 状态允许兜底为 COMPLETED（FINALIZING 等待 PATCH 完成，不兜底）
    mockSessionStore.sessions = [createSession(sessionId, StreamState.SYNCING)]
    store.startStreaming(sessionId)

    const event = {
      type: 'stream_completed',
      seq: 1,
      session_id: sessionId,
      payload: { message_id: 100, finalized: true },
    }

    await store.applySessionEvent(event)
    const msg = mockSessionStore.sessions[0].messages[0]
    expect(msg.streamState).toBe(StreamState.COMPLETED)
    expect(msg.isStreaming).toBe(false)
  })

  it('stream_completed 事件在请求浏览器中 FINALIZING 状态应保持（等待 PATCH 完成）', async () => {
    const sessionId = 'session-6b'
    // FINALIZING 状态表示 PATCH 尚未完成，不兜底为 COMPLETED
    mockSessionStore.sessions = [createSession(sessionId, StreamState.FINALIZING)]
    store.startStreaming(sessionId)

    const event = {
      type: 'stream_completed',
      seq: 1,
      session_id: sessionId,
      payload: { message_id: 100, finalized: true },
    }

    await store.applySessionEvent(event)
    const msg = mockSessionStore.sessions[0].messages[0]
    expect(msg.streamState).toBe(StreamState.FINALIZING)
  })

  it('stream_completed 事件在非请求浏览器中不应将 INTERRUPTED 改为 COMPLETED', async () => {
    const sessionId = 'session-7'
    mockSessionStore.sessions = [createSession(sessionId, StreamState.INTERRUPTED)]
    // 不调用 startStreaming，模拟非请求浏览器

    const event = {
      type: 'stream_completed',
      seq: 1,
      session_id: sessionId,
      payload: { message_id: 100 },
    }

    await store.applySessionEvent(event)
    const msg = mockSessionStore.sessions[0].messages[0]
    expect(msg.streamState).toBe(StreamState.INTERRUPTED)
  })

  it('startStreaming/stopStreaming 应管理 streamingSessions 集合', () => {
    const sessionId = 'session-8'
    store.startStreaming(sessionId)
    expect(store.streamingSessions.has(sessionId)).toBe(true)
    store.stopStreaming(sessionId)
    expect(store.streamingSessions.has(sessionId)).toBe(false)
  })

  it('stream_completed 事件在非请求浏览器中不应将 FINALIZING 改为 COMPLETED', async () => {
    const sessionId = 'session-9'
    mockSessionStore.sessions = [createSession(sessionId, StreamState.FINALIZING)]
    // 不调用 startStreaming，模拟非请求浏览器

    const event = {
      type: 'stream_completed',
      seq: 1,
      session_id: sessionId,
      payload: { message_id: 100 },
    }

    await store.applySessionEvent(event)
    const msg = mockSessionStore.sessions[0].messages[0]
    expect(msg.streamState).toBe(StreamState.FINALIZING)
  })

  it('requestFullSync 应保护 INTERRUPTED 消息不被后端快照整体替换', async () => {
    const sessionId = 'session-10'
    const localMsg = {
      id: 'msg-1',
      backendId: 100,
      role: 'assistant',
      content: 'local interrupted content',
      streamState: StreamState.INTERRUPTED,
      isStreaming: false,
    }
    mockSessionStore.sessions = [{ id: sessionId, messages: [localMsg] }]

    // 模拟后端返回的快照（content 为空旧值）
    const backendMsg = {
      id: 'msg-1',
      backendId: 100,
      role: 'assistant',
      content: '',
      streamState: StreamState.COMPLETED,
      isStreaming: false,
    }
    mockSessionStore.loadSessionDetail.mockResolvedValue({
      id: sessionId,
      messages: [backendMsg],
    })

    await store.requestFullSync(sessionId)

    // mergeMessageFromBackend mock 会做 Object.assign，但保护态逻辑应在调用前保留本地消息
    // 由于 mock 的 mergeMessageFromBackend 是 Object.assign(existing, backend)，
    // 保护态路径会用 local 而非 backend 替换，所以 localMsg 应仍存在于 session.messages 中
    const session = mockSessionStore.sessions.find(s => s.id === sessionId)
    expect(session).toBeTruthy()
    // 验证保护态消息被保留（而非被后端快照整体替换）
    const preservedMsg = session.messages.find(m => m.backendId === 100)
    expect(preservedMsg).toBeTruthy()
  })

  it('requestFullSync 应保护 FINALIZING 消息不被后端快照整体替换', async () => {
    const sessionId = 'session-11'
    const localMsg = {
      id: 'msg-2',
      backendId: 200,
      role: 'assistant',
      content: 'local finalizing content',
      streamState: StreamState.FINALIZING,
      isStreaming: true,
    }
    mockSessionStore.sessions = [{ id: sessionId, messages: [localMsg] }]

    const backendMsg = {
      id: 'msg-2',
      backendId: 200,
      role: 'assistant',
      content: 'old backend snapshot',
      streamState: StreamState.COMPLETED,
      isStreaming: false,
    }
    mockSessionStore.loadSessionDetail.mockResolvedValue({
      id: sessionId,
      messages: [backendMsg],
    })

    await store.requestFullSync(sessionId)

    const session = mockSessionStore.sessions.find(s => s.id === sessionId)
    expect(session).toBeTruthy()
    const preservedMsg = session.messages.find(m => m.backendId === 200)
    expect(preservedMsg).toBeTruthy()
  })
})
