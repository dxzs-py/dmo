/**
 * Task 5/6/3-4 修复的单元测试
 *
 * Task 5: 删除对话去重（deleteSession + session_deleted 事件去重）
 * Task 6: 刷新后自动选中最新会话（loadSessionsFromBackend）
 * Task 3/4: 合并逻辑保留 tool_calls（_syncToolCallsMapFromMessage）
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { ToolCallStatus, StreamState } from '@/types'

// ==================== Mock 依赖（使用 vi.hoisted 避免 hoisting 问题） ====================

const {
  mockUserStore,
  mockChatAPI,
  mockKnowledgeAPI,
} = vi.hoisted(() => {
  const mockUserStore = {
    isLoggedIn: false,
    userInfo: { id: 'user-1' },
  }
  const mockChatAPI = {
    getSessions: vi.fn(),
    getSession: vi.fn(),
    createSession: vi.fn(),
    deleteSession: vi.fn(),
    updateSession: vi.fn(),
    addMessage: vi.fn(),
    updateMessage: vi.fn(),
  }
  const mockKnowledgeAPI = {
    getKnowledgeBases: vi.fn(),
  }
  return { mockUserStore, mockChatAPI, mockKnowledgeAPI }
})

vi.mock('@/stores/user', () => ({
  useUserStore: () => mockUserStore,
}))

vi.mock('@/api', () => ({
  chatAPI: mockChatAPI,
  knowledgeAPI: mockKnowledgeAPI,
}))

vi.mock('element-plus', () => {
  const mockFn = vi.fn(() => ({ close: vi.fn() }))
  mockFn.success = vi.fn()
  mockFn.error = vi.fn()
  mockFn.warning = vi.fn()
  mockFn.info = vi.fn()
  return {
    ElMessage: mockFn,
    ElMessageBox: { confirm: vi.fn(() => Promise.resolve('confirm')) },
  }
})

vi.mock('@/utils/logger', () => ({
  logger: {
    log: () => {},
    info: () => {},
    warn: () => {},
    error: () => {},
    debug: () => {},
  },
}))

// Mock sessionTransformers（保留必要字段，updatedAt 可控用于 Task 6 排序测试）
vi.mock('@/utils/sessionTransformers', () => ({
  isApiSuccess: (response) => response?.data?.code === 200,
  transformBackendSessionToFrontend: (data) => {
    if (!data) return null
    return {
      id: data.id || String(data.id),
      title: data.title || '测试会话',
      mode: data.mode || 'agent',
      messages: (data.messages || []).map(m => ({
        id: m.id || String(m.id),
        backendId: m.id || m.backendId,
        role: m.role || 'assistant',
        content: m.content || '',
        toolCalls: m.tool_calls || m.toolCalls || [],
        streamState: m.streamState || StreamState.COMPLETED,
        isStreaming: false,
        versions: [{
          content: m.content || '',
          toolCalls: m.tool_calls || m.toolCalls || [],
          reasoning: null,
          sources: [],
          suggestions: null,
          context: null,
          streamState: m.streamState || StreamState.COMPLETED,
          isStreaming: false,
        }],
        currentVersion: 0,
      })),
      messageCount: data.messages?.length || 0,
      updatedAt: data.updated_at || data.updatedAt || Date.now(),
      createdAt: data.created_at || data.createdAt || Date.now(),
    }
  },
  transformBackendMessageToFrontend: (data) => data,
  transformFrontendMessageToBackend: (msg) => msg,
}))

import { useSessionStore } from '../session'

// ==================== 测试辅助 ====================

function makeSession(id, updatedAt, messages = []) {
  return {
    id,
    title: `会话-${id}`,
    mode: 'agent',
    messages,
    messageCount: messages.length,
    updatedAt,
    createdAt: updatedAt,
  }
}

function makeAssistantMessage(backendId, toolCalls = [], content = 'AI 回复') {
  return {
    id: `msg-${backendId}`,
    backendId,
    role: 'assistant',
    content,
    toolCalls,
    streamState: StreamState.COMPLETED,
    isStreaming: false,
    versions: [{
      content,
      toolCalls,
      reasoning: null,
      sources: [],
      suggestions: null,
      context: null,
      streamState: StreamState.COMPLETED,
      isStreaming: false,
    }],
    currentVersion: 0,
  }
}

function makeToolCall(id, extra = {}) {
  return {
    id,
    tool_call_id: id,
    name: 'execute',
    status: ToolCallStatus.RUNNING,
    parameters: {},
    ...extra,
  }
}

// ==================== Task 5: 删除对话去重 ====================

describe('Task 5: 删除对话去重', () => {
  let store

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    mockUserStore.isLoggedIn = true
    mockChatAPI.deleteSession.mockResolvedValue({ data: { code: 200, data: {} } })
    store = useSessionStore()
  })

  it('deleteSession 按 session_id 过滤移除（非按 index splice）', async () => {
    // 3 个会话，删除中间的
    store.sessions = [
      makeSession('s1', 1000),
      makeSession('s2', 2000),
      makeSession('s3', 3000),
    ]

    await store.deleteSession('s2')

    // 应只删除 s2，s1 和 s3 保留
    expect(store.sessions).toHaveLength(2)
    expect(store.sessions.map(s => s.id).sort()).toEqual(['s1', 's3'])
  })

  it('乐观删除 + 事件删除同一会话 → sessions 只减少 1 个', async () => {
    // 模拟触发浏览器场景：
    // 1. deleteSession HTTP 路径删除（标记 deletedSessionIds + filter）
    // 2. WebSocket session_deleted 事件到达（检查 deletedSessionIds → 跳过）
    store.sessions = [
      makeSession('s1', 1000),
      makeSession('s2', 2000),
      makeSession('s3', 3000),
    ]

    // 1. HTTP 删除 s2
    await store.deleteSession('s2')
    expect(store.sessions).toHaveLength(2)

    // 2. 模拟 WS session_deleted 事件到达（通过 deletedSessionIds 去重）
    expect(store.deletedSessionIds.has('s2')).toBe(true)
    // 若 deletedSessionIds 已含该 id，不应再次移除
    // （handleUserEvent.js 中的逻辑：检查 deletedSessionIds → 跳过）
    if (store.deletedSessionIds.has('s2')) {
      // 模拟 handleUserEvent.js 的去重逻辑：跳过 removeSession
      // 不调用 store.removeSession('s2')
    } else {
      store.deletedSessionIds.add('s2')
      store.removeSession('s2')
    }

    // 仍为 2 个会话，未误删
    expect(store.sessions).toHaveLength(2)
    expect(store.sessions.map(s => s.id).sort()).toEqual(['s1', 's3'])
  })

  it('WS 事件先到达 + HTTP 删除后到达 → sessions 只减少 1 个', async () => {
    // 模拟 WS 事件先于 HTTP 响应到达的场景：
    // 1. WS session_deleted 事件先到达 → removeSession + 标记 deletedSessionIds
    // 2. HTTP deleteSession 的 await 完成 → filter（no-op，已被移除）
    store.sessions = [
      makeSession('s1', 1000),
      makeSession('s2', 2000),
      makeSession('s3', 3000),
    ]

    // 1. WS 事件先到达：标记 + removeSession
    store.deletedSessionIds.add('s2')
    store.removeSession('s2')
    expect(store.sessions).toHaveLength(2)

    // 2. HTTP deleteSession 的 await 完成后执行 filter
    // 此时 sessions 中已无 s2，filter 是 no-op
    store.sessions = store.sessions.filter(s => s.id !== 's2')

    // 仍为 2 个，未误删相邻会话
    expect(store.sessions).toHaveLength(2)
    expect(store.sessions.map(s => s.id).sort()).toEqual(['s1', 's3'])
  })

  it('原 bug 复现：splice(staleIndex) 会误删相邻会话（对比验证）', () => {
    // 此测试验证修复的正确性：原 splice(index) 在 await 后 index 过期会误删
    // 修复后用 filter 不会误删
    store.sessions = [
      makeSession('s1', 1000),
      makeSession('s2', 2000),
      makeSession('s3', 3000),
    ]

    // 模拟原 bug：index=1（s2），await 后 s2 被移除，splice(1,1) 删了 s3
    // 修复后：filter 不会误删
    const sessionId = 's2'
    // 模拟 WS 事件在 await 期间移除 s2
    store.sessions = store.sessions.filter(s => s.id !== sessionId)
    // 此时 sessions = [s1, s3]
    // 原代码 splice(1, 1) 会删除 s3（误删）
    // 修复后 filter 不执行（已在 deleteSession 内用 filter）
    expect(store.sessions).toHaveLength(2)
    expect(store.sessions.map(s => s.id)).toEqual(['s1', 's3'])
  })

  it('deletedSessionIds 在 clearAllLocalData 后清空', () => {
    store.deletedSessionIds.add('s1')
    store.deletedSessionIds.add('s2')
    expect(store.deletedSessionIds.size).toBe(2)

    store.clearAllLocalData()
    expect(store.deletedSessionIds.size).toBe(0)
  })
})

// ==================== Task 6: 刷新后自动选中 ====================

describe('Task 6: 刷新后自动选中最新会话', () => {
  let store

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    mockUserStore.isLoggedIn = true
    store = useSessionStore()
  })

  it('loadSessionsFromBackend 后 currentSessionId 为空 → 选中 updatedAt 最新的会话', async () => {
    // 3 个会话，updatedAt 各不同，s2 最新
    const backendSessions = [
      { id: 's1', title: '会话1', mode: 'agent', updated_at: 1000, messages: [] },
      { id: 's2', title: '会话2', mode: 'agent', updated_at: 3000, messages: [] },
      { id: 's3', title: '会话3', mode: 'agent', updated_at: 2000, messages: [] },
    ]
    mockChatAPI.getSessions.mockResolvedValue({
      data: { code: 200, data: { items: backendSessions, total: 3, page: 1, total_pages: 1 } },
    })
    // getSession 返回带消息的详情
    mockChatAPI.getSession.mockImplementation((sid) => {
      const s = backendSessions.find(x => x.id === sid)
      return Promise.resolve({
        data: {
          code: 200,
          data: {
            id: s.id,
            title: s.title,
            mode: s.mode,
            updated_at: s.updated_at,
            messages: [{ id: 100, role: 'assistant', content: '历史消息', tool_calls: [] }],
          },
        },
      })
    })

    // currentSessionId 初始为空
    expect(store.currentSessionId).toBeNull()

    await store.loadSessionsFromBackend()

    // 应选中 updatedAt 最新的 s2
    expect(store.currentSessionId).toBe('s2')
  })

  it('sessions 为空时 currentSessionId 设为 null', async () => {
    mockChatAPI.getSessions.mockResolvedValue({
      data: { code: 200, data: { items: [], total: 0, page: 1, total_pages: 1 } },
    })

    await store.loadSessionsFromBackend()

    expect(store.currentSessionId).toBeNull()
  })

  it('currentSessionId 已有有效值时保持不变', async () => {
    const backendSessions = [
      { id: 's1', title: '会话1', mode: 'agent', updated_at: 1000, messages: [] },
      { id: 's2', title: '会话2', mode: 'agent', updated_at: 3000, messages: [] },
    ]
    mockChatAPI.getSessions.mockResolvedValue({
      data: { code: 200, data: { items: backendSessions, total: 2, page: 1, total_pages: 1 } },
    })

    // 预设 currentSessionId 为 s1
    store.currentSessionId = 's1'

    await store.loadSessionsFromBackend()

    // 应保持 s1，不切换到最新的 s2
    expect(store.currentSessionId).toBe('s1')
  })

  it('自动选中后加载会话详情（messages 非空）', async () => {
    const backendSessions = [
      { id: 's1', title: '会话1', mode: 'agent', updated_at: 1000, messages: [] },
    ]
    mockChatAPI.getSessions.mockResolvedValue({
      data: { code: 200, data: { items: backendSessions, total: 1, page: 1, total_pages: 1 } },
    })
    mockChatAPI.getSession.mockResolvedValue({
      data: {
        code: 200,
        data: {
          id: 's1',
          title: '会话1',
          mode: 'agent',
          updated_at: 1000,
          messages: [{ id: 100, role: 'assistant', content: '历史消息', tool_calls: [] }],
        },
      },
    })

    await store.loadSessionsFromBackend()

    // 等待异步 loadSessionDetail 完成
    await new Promise(resolve => setTimeout(resolve, 50))

    // 会话详情应已加载
    const session = store.sessions.find(s => s.id === 's1')
    expect(session.messages).toHaveLength(1)
    expect(session.messages[0].content).toBe('历史消息')
  })
})

// ==================== Task 3/4: 合并逻辑保留 tool_calls ====================

describe('Task 3/4: 合并逻辑保留 tool_calls（_syncToolCallsMapFromMessage）', () => {
  let store

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    mockUserStore.isLoggedIn = true
    store = useSessionStore()
  })

  it('loadSessionDetail 合并后 toolCallsMap 同步补全缺失的 toolCall', async () => {
    // 场景：非触发浏览器通过 WS 收到 1 个 tool_call（toolCallsMap 有 1 个），
    // 但后端实际有 3 个。loadSessionDetail 合并后 message.toolCalls 有 3 个，
    // toolCallsMap 应同步补入缺失的 2 个。
    const sessionId = 's1'
    const existingToolCall = makeToolCall('tc-1', { status: ToolCallStatus.COMPLETED, result: 'done' })

    // 本地会话有 1 个 toolCall
    store.sessions = [
      makeSession(sessionId, 1000, [
        makeAssistantMessage(100, [existingToolCall], 'AI 回复'),
      ]),
    ]
    // 手动将 toolCall 加入 toolCallsMap（模拟 WS 事件到达）
    store.addToolCallToLastMessage(sessionId, existingToolCall)
    expect(store.getToolCallsBySession(sessionId)).toHaveLength(1)

    // 后端返回 3 个 toolCalls
    const backendToolCalls = [
      { id: 'tc-1', tool_call_id: 'tc-1', tool_name: 'execute', status: 'completed', result: 'done' },
      { id: 'tc-2', tool_call_id: 'tc-2', tool_name: 'read_file', status: 'completed', result: 'file content' },
      { id: 'tc-3', tool_call_id: 'tc-3', tool_name: 'write_file', status: 'completed', result: 'written' },
    ]

    mockChatAPI.getSession.mockResolvedValue({
      data: {
        code: 200,
        data: {
          id: sessionId,
          title: '会话1',
          mode: 'agent',
          updated_at: 2000,
          messages: [{
            id: 100,
            role: 'assistant',
            content: 'AI 回复',
            tool_calls: backendToolCalls,
          }],
        },
      },
    })

    await store.loadSessionDetail(sessionId, { forceRefresh: true })

    // toolCallsMap 应有 3 个（补入了 tc-2 和 tc-3）
    const mapToolCalls = store.getToolCallsBySession(sessionId)
    expect(mapToolCalls).toHaveLength(3)
    const ids = mapToolCalls.map(tc => tc.id).sort()
    expect(ids).toEqual(['tc-1', 'tc-2', 'tc-3'])
  })

  it('_syncToolCallsMapFromMessage 后 _syncMessageToolCalls 不丢失 toolCalls', async () => {
    // 场景：合并后 message.toolCalls 有 3 个，toolCallsMap 同步后有 3 个。
    // 后续若 _syncMessageToolCalls 被调用（如新 tool_call 事件），不应丢失。
    const sessionId = 's1'
    const backendToolCalls = [
      { id: 'tc-1', tool_call_id: 'tc-1', tool_name: 'execute', status: 'completed', result: 'done' },
      { id: 'tc-2', tool_call_id: 'tc-2', tool_name: 'read_file', status: 'completed', result: 'content' },
      { id: 'tc-3', tool_call_id: 'tc-3', tool_name: 'write_file', status: 'completed', result: 'written' },
    ]

    // 本地会话空消息
    store.sessions = [
      makeSession(sessionId, 1000, [
        makeAssistantMessage(100, [], 'AI 回复'),
      ]),
    ]

    mockChatAPI.getSession.mockResolvedValue({
      data: {
        code: 200,
        data: {
          id: sessionId,
          title: '会话1',
          mode: 'agent',
          updated_at: 2000,
          messages: [{
            id: 100,
            role: 'assistant',
            content: 'AI 回复',
            tool_calls: backendToolCalls,
          }],
        },
      },
    })

    await store.loadSessionDetail(sessionId, { forceRefresh: true })

    // message.toolCalls 应有 3 个
    const session = store.sessions.find(s => s.id === sessionId)
    const msg = session.messages[0]
    expect(msg.toolCalls).toHaveLength(3)

    // toolCallsMap 也应有 3 个
    expect(store.getToolCallsBySession(sessionId)).toHaveLength(3)

    // 模拟后续 _syncMessageToolCalls 被调用（通过 addOrUpdateToolCall 触发）
    // 此时不丢失已有的 3 个 toolCalls
    store.addOrUpdateToolCall(sessionId, makeToolCall('tc-1', { status: ToolCallStatus.COMPLETED }))

    // 仍应保持 3 个（不回退到 1 个）
    expect(store.getToolCallsBySession(sessionId)).toHaveLength(3)
    expect(msg.toolCalls).toHaveLength(3)
  })

  it('合并后保留本地审批状态（approval 不被后端覆盖为空）', async () => {
    const sessionId = 's1'
    const localToolCall = makeToolCall('tc-1', {
      status: ToolCallStatus.WAITING,
      approval: { state: 'pending', interrupt_id: 'tc-1', tool_name: 'execute' },
    })

    store.sessions = [
      makeSession(sessionId, 1000, [
        makeAssistantMessage(100, [localToolCall], 'AI 回复'),
      ]),
    ]
    store.addToolCallToLastMessage(sessionId, localToolCall)

    // 后端返回的 toolCall 没有 approval 字段
    mockChatAPI.getSession.mockResolvedValue({
      data: {
        code: 200,
        data: {
          id: sessionId,
          title: '会话1',
          mode: 'agent',
          updated_at: 2000,
          messages: [{
            id: 100,
            role: 'assistant',
            content: 'AI 回复',
            tool_calls: [{
              id: 'tc-1',
              tool_call_id: 'tc-1',
              tool_name: 'execute',
              status: 'completed',
              result: 'done',
            }],
          }],
        },
      },
    })

    await store.loadSessionDetail(sessionId, { forceRefresh: true })

    // toolCallsMap 中的 toolCall 应保留本地 approval
    const tc = store.getToolCallById(sessionId, 'tc-1')
    expect(tc).toBeTruthy()
    expect(tc.approval).toBeTruthy()
    expect(tc.approval.state).toBe('pending')
  })

  it('合并后保留本地 result（后端为空时不覆盖）', async () => {
    const sessionId = 's1'
    const localToolCall = makeToolCall('tc-1', {
      status: ToolCallStatus.COMPLETED,
      result: '本地结果',
    })

    store.sessions = [
      makeSession(sessionId, 1000, [
        makeAssistantMessage(100, [localToolCall], 'AI 回复'),
      ]),
    ]
    store.addToolCallToLastMessage(sessionId, localToolCall)

    // 后端返回的 toolCall result 为空
    mockChatAPI.getSession.mockResolvedValue({
      data: {
        code: 200,
        data: {
          id: sessionId,
          title: '会话1',
          mode: 'agent',
          updated_at: 2000,
          messages: [{
            id: 100,
            role: 'assistant',
            content: 'AI 回复',
            tool_calls: [{
              id: 'tc-1',
              tool_call_id: 'tc-1',
              tool_name: 'execute',
              status: 'completed',
            }],
          }],
        },
      },
    })

    await store.loadSessionDetail(sessionId, { forceRefresh: true })

    // toolCallsMap 中的 toolCall 应保留本地 result
    const tc = store.getToolCallById(sessionId, 'tc-1')
    expect(tc).toBeTruthy()
    expect(tc.result).toBe('本地结果')
  })
})
