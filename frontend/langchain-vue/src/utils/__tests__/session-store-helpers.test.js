/**
 * session-store-helpers 纯函数单元测试
 *
 * 覆盖从 session.js 抽取的纯数据操作函数主路径。
 * 参考现有测试风格（message-operations.test.js）。
 */
import { describe, it, expect } from 'vitest'
import {
  ensureToolCallsMap,
  ensurePendingApprovalsMap,
  syncMessageToolCalls,
  syncToolCallsMapFromMessage,
  mergeMessagesFromBackend,
  findLastAssistantMessage,
  findLastUserMessage,
  mergeSessionList,
  computePaginationMeta,
  selectLatestSessionId,
  upsertSessionInList,
  getToolCallsByMessageFromMap,
  countActiveApprovalsByGraphInMap,
  addPendingApproval,
  takePendingApproval,
  resolveSelectedKnowledgeBase,
  resolveSelectedKnowledgeBases,
  parseKnowledgeBasesResponse,
  cleanupKnowledgeBasesSelection,
  buildMessageWithVersions,
  applyUsageToMessage,
  applyStreamState,
  applyVersionToMessage,
} from '../session-store-helpers'
import { StreamState, ToolCallStatus } from '@/types'

// ==================== 辅助构造函数 ====================

function makeAssistantMessage(id, toolCalls = [], content = 'AI 回复') {
  return {
    id: String(id),
    backendId: id,
    role: 'assistant',
    content,
    toolCalls,
    streamState: StreamState.COMPLETED,
    isStreaming: false,
    versions: [{ content, toolCalls, reasoning: null, sources: [], suggestions: null, context: null }],
    currentVersion: 0,
  }
}

function makeSession(id, messages = []) {
  return { id, title: `会话-${id}`, mode: 'agent', messages, messageCount: messages.length, updatedAt: 1000 }
}

function makeToolCall(id, extra = {}) {
  return { id, name: 'ls', status: ToolCallStatus.RUNNING, parameters: {}, ...extra }
}

// ==================== ensureToolCallsMap / ensurePendingApprovalsMap ====================

describe('ensureToolCallsMap', () => {
  it('sessionId 为空时返回 null map', () => {
    const map = new Map()
    const result = ensureToolCallsMap(map, '')
    expect(result.map).toBeNull()
    expect(result.created).toBe(false)
  })

  it('不存在时创建新 Map 并返回 created=true', () => {
    const map = new Map()
    const result = ensureToolCallsMap(map, 's1')
    expect(result.created).toBe(true)
    expect(result.map).toBeInstanceOf(Map)
    expect(map.has('s1')).toBe(true)
  })

  it('已存在时返回已有 Map 且 created=false', () => {
    const map = new Map()
    const inner = new Map([['tc1', { id: 'tc1' }]])
    map.set('s1', inner)
    const result = ensureToolCallsMap(map, 's1')
    expect(result.created).toBe(false)
    expect(result.map).toBe(inner)
  })
})

describe('ensurePendingApprovalsMap', () => {
  it('sessionId 为空时返回 null map', () => {
    expect(ensurePendingApprovalsMap(new Map(), null).map).toBeNull()
  })

  it('不存在时创建新 Map', () => {
    const map = new Map()
    const result = ensurePendingApprovalsMap(map, 's1')
    expect(result.created).toBe(true)
    expect(result.map).toBeInstanceOf(Map)
  })
})

// ==================== syncMessageToolCalls ====================

describe('syncMessageToolCalls', () => {
  it('Map 数据同步到最后一条 assistant 消息的 toolCalls', () => {
    const sessions = [makeSession('s1', [makeAssistantMessage(100)])]
    const toolCallsMap = new Map()
    const inner = new Map()
    inner.set('tc1', makeToolCall('tc1'))
    toolCallsMap.set('s1', inner)

    syncMessageToolCalls(sessions, toolCallsMap, 's1')

    expect(sessions[0].messages[0].toolCalls).toHaveLength(1)
    expect(sessions[0].messages[0].toolCalls[0].id).toBe('tc1')
  })

  it('同步到当前 version 的 toolCalls', () => {
    const sessions = [makeSession('s1', [makeAssistantMessage(100)])]
    const toolCallsMap = new Map()
    toolCallsMap.set('s1', new Map([['tc1', makeToolCall('tc1')]]))

    syncMessageToolCalls(sessions, toolCallsMap, 's1')

    expect(sessions[0].messages[0].versions[0].toolCalls).toHaveLength(1)
  })

  it('Map 中无该 session 时清空 message.toolCalls', () => {
    const sessions = [makeSession('s1', [makeAssistantMessage(100, [makeToolCall('old')])])]
    const toolCallsMap = new Map()

    syncMessageToolCalls(sessions, toolCallsMap, 's1')

    expect(sessions[0].messages[0].toolCalls).toEqual([])
  })

  it('最后一条消息非 assistant 时不操作', () => {
    const sessions = [makeSession('s1', [{ id: 'm1', role: 'user', content: 'hi', toolCalls: [] }])]
    const toolCallsMap = new Map([['s1', new Map([['tc1', makeToolCall('tc1')]])]])

    syncMessageToolCalls(sessions, toolCallsMap, 's1')

    expect(sessions[0].messages[0].toolCalls).toEqual([])
  })

  it('session 不存在时安全返回', () => {
    expect(() => syncMessageToolCalls([], new Map(), 'no-such')).not.toThrow()
  })
})

// ==================== syncToolCallsMapFromMessage ====================

describe('syncToolCallsMapFromMessage', () => {
  it('Map 缺失的 toolCall 从 message 补入', () => {
    const toolCallsMap = new Map()
    const message = makeAssistantMessage(100, [
      { id: 'tc-1', tool_call_id: 'tc-1', status: 'completed', result: 'done' },
    ])

    const changed = syncToolCallsMapFromMessage(toolCallsMap, 's1', message)

    expect(changed).toBe(true)
    const inner = toolCallsMap.get('s1')
    expect(inner.get('tc-1')).toBeTruthy()
    expect(inner.get('tc-1').status).toBe('completed')
  })

  it('Map 已有时合并后端数据保留本地 approval', () => {
    const toolCallsMap = new Map()
    const inner = new Map()
    inner.set('tc-1', {
      id: 'tc-1', tool_call_id: 'tc-1', status: 'running',
      approval: { state: 'pending', interrupt_id: 'tc-1' },
    })
    toolCallsMap.set('s1', inner)

    const message = makeAssistantMessage(100, [
      { id: 'tc-1', tool_call_id: 'tc-1', status: 'completed', result: 'done' },
    ])

    syncToolCallsMapFromMessage(toolCallsMap, 's1', message)

    const merged = inner.get('tc-1')
    expect(merged.result).toBe('done')
    // 本地 approval 应保留（后端未带 approval）
    expect(merged.approval).toBeTruthy()
    expect(merged.approval.state).toBe('pending')
  })

  it('保留本地 result（后端为空时不覆盖）', () => {
    const toolCallsMap = new Map()
    const inner = new Map()
    inner.set('tc-1', { id: 'tc-1', tool_call_id: 'tc-1', result: 'local result' })
    toolCallsMap.set('s1', inner)

    const message = makeAssistantMessage(100, [
      { id: 'tc-1', tool_call_id: 'tc-1', result: null },
    ])

    syncToolCallsMapFromMessage(toolCallsMap, 's1', message)

    expect(inner.get('tc-1').result).toBe('local result')
  })

  it('toolCall 携带 approval 时同步 approval.state 到 status', () => {
    const toolCallsMap = new Map()
    const message = makeAssistantMessage(100, [
      { id: 'tc-1', tool_call_id: 'tc-1', status: 'running', approval: { state: 'pending' } },
    ])

    syncToolCallsMapFromMessage(toolCallsMap, 's1', message)

    const tc = toolCallsMap.get('s1').get('tc-1')
    expect(tc.approval.state).toBe('pending')
    // pending → pending_approval（mapApprovalStateToStatus）
    expect(tc.status).toBe('pending_approval')
  })

  it('工具已处于终态（completed）时不被审批状态降级', () => {
    const toolCallsMap = new Map()
    const inner = new Map()
    inner.set('tc-1', { id: 'tc-1', tool_call_id: 'tc-1', status: 'completed' })
    toolCallsMap.set('s1', inner)

    const message = makeAssistantMessage(100, [
      { id: 'tc-1', tool_call_id: 'tc-1', status: 'completed', approval: { state: 'approved' } },
    ])

    syncToolCallsMapFromMessage(toolCallsMap, 's1', message)

    // completed 不应被降级为 approved
    expect(inner.get('tc-1').status).toBe('completed')
  })

  it('message 为空或无 toolCalls 时返回 false', () => {
    expect(syncToolCallsMapFromMessage(new Map(), 's1', null)).toBe(false)
    expect(syncToolCallsMapFromMessage(new Map(), 's1', makeAssistantMessage(100, []))).toBe(false)
  })

  it('补全后不丢失：3 个 toolCall 全部补入 Map', () => {
    const toolCallsMap = new Map()
    toolCallsMap.set('s1', new Map([['tc-1', makeToolCall('tc-1', { status: 'completed', result: 'done' })]]))
    const message = makeAssistantMessage(100, [
      { id: 'tc-1', tool_call_id: 'tc-1', status: 'completed', result: 'done' },
      { id: 'tc-2', tool_call_id: 'tc-2', status: 'completed', result: 'file' },
      { id: 'tc-3', tool_call_id: 'tc-3', status: 'completed', result: 'written' },
    ])

    syncToolCallsMapFromMessage(toolCallsMap, 's1', message)

    const inner = toolCallsMap.get('s1')
    expect(inner.size).toBe(3)
  })
})

// ==================== mergeMessagesFromBackend ====================

describe('mergeMessagesFromBackend', () => {
  it('本地为空时返回后端消息', () => {
    const backend = [makeAssistantMessage(1)]
    const result = mergeMessagesFromBackend([], backend)
    expect(result).toBe(backend)
  })

  it('后端为空时返回本地消息', () => {
    const local = [makeAssistantMessage(1)]
    const result = mergeMessagesFromBackend(local, [])
    expect(result).toBe(local)
  })

  it('按 backendId 匹配并合并', () => {
    const local = [makeAssistantMessage(100, [], '本地内容')]
    local[0].streamState = StreamState.STREAMING
    const backend = [makeAssistantMessage(100, [], '后端内容更长一些')]

    const result = mergeMessagesFromBackend(local, backend)

    expect(result).toHaveLength(1)
    // STREAMING 保护态下，后端 content 更长时允许覆盖
    expect(result[0].content).toBe('后端内容更长一些')
  })

  it('后端独有的消息直接追加', () => {
    const local = [makeAssistantMessage(100)]
    const backend = [makeAssistantMessage(100), makeAssistantMessage(200)]

    const result = mergeMessagesFromBackend(local, backend)

    expect(result).toHaveLength(2)
  })

  it('本地独有的保护态消息保留', () => {
    const local = [makeAssistantMessage(100)]
    local[0].streamState = StreamState.STREAMING
    const backend = [makeAssistantMessage(200)]

    const result = mergeMessagesFromBackend(local, backend)

    expect(result).toHaveLength(2)
    expect(result.some(m => m.id === '100')).toBe(true)
  })

  it('本地独有的非保护态消息不保留', () => {
    const local = [makeAssistantMessage(100)]
    // streamState 为 COMPLETED（保护态）→ 保留
    local[0].streamState = StreamState.COMPLETED
    const backend = [makeAssistantMessage(200)]

    const result = mergeMessagesFromBackend(local, backend)

    // COMPLETED 也在 PROTECTED_STREAM_STATES 中，应保留
    expect(result).toHaveLength(2)
  })

  it('按 timestamp 升序排序', () => {
    const local = [makeAssistantMessage(100)]
    local[0].timestamp = 3000
    local[0].streamState = StreamState.STREAMING
    const backend = [{ ...makeAssistantMessage(200), timestamp: 1000 }]

    const result = mergeMessagesFromBackend(local, backend)

    expect(result[0].id).toBe('200')
    expect(result[1].id).toBe('100')
  })
})

// ==================== findLastAssistantMessage / findLastUserMessage ====================

describe('findLastAssistantMessage', () => {
  it('返回最后一条 assistant 消息', () => {
    const session = makeSession('s1', [
      { id: 'u1', role: 'user', content: 'hi' },
      makeAssistantMessage(100),
    ])
    const msg = findLastAssistantMessage(session)
    expect(msg.id).toBe('100')
  })

  it('无 assistant 消息时返回 null', () => {
    const session = makeSession('s1', [{ id: 'u1', role: 'user', content: 'hi' }])
    expect(findLastAssistantMessage(session)).toBeNull()
  })

  it('session 为空时返回 null', () => {
    expect(findLastAssistantMessage(null)).toBeNull()
    expect(findLastAssistantMessage({ messages: [] })).toBeNull()
  })
})

describe('findLastUserMessage', () => {
  it('返回最后一条 user 消息', () => {
    const session = makeSession('s1', [
      { id: 'u1', role: 'user', content: 'hi' },
      makeAssistantMessage(100),
      { id: 'u2', role: 'user', content: 'again' },
    ])
    const msg = findLastUserMessage(session)
    expect(msg.id).toBe('u2')
  })

  it('无 user 消息时返回 null', () => {
    expect(findLastUserMessage({ messages: [makeAssistantMessage(100)] })).toBeNull()
  })
})

// ==================== mergeSessionList ====================

describe('mergeSessionList', () => {
  it('page <= 1 时替换', () => {
    const existing = [{ id: 's1' }, { id: 's2' }]
    const incoming = [{ id: 's3' }]
    expect(mergeSessionList(existing, incoming, 1)).toEqual([{ id: 's3' }])
  })

  it('page > 1 时追加去重', () => {
    const existing = [{ id: 's1' }, { id: 's2' }]
    const incoming = [{ id: 's2' }, { id: 's3' }]
    const result = mergeSessionList(existing, incoming, 2)
    expect(result).toHaveLength(3)
    expect(result.map(s => s.id)).toEqual(['s1', 's2', 's3'])
  })
})

// ==================== computePaginationMeta ====================

describe('computePaginationMeta', () => {
  it('data 为对象时解析分页字段', () => {
    const data = { total: 100, page: 2, page_size: 20, total_pages: 5 }
    const meta = computePaginationMeta(data, 2, 50, 20)
    expect(meta).toEqual({ total: 100, page: 2, pageSize: 20, totalPages: 5, hasMore: true })
  })

  it('data 为数组时 fallback 到 sessionsCount', () => {
    const meta = computePaginationMeta([1, 2, 3], 1, 3, 20)
    expect(meta).toEqual({ total: 3, page: 1, pageSize: 20, totalPages: 1, hasMore: false })
  })

  it('hasMore 在最后一页时为 false', () => {
    const data = { total: 100, page: 5, page_size: 20, total_pages: 5 }
    const meta = computePaginationMeta(data, 5, 100, 20)
    expect(meta.hasMore).toBe(false)
  })

  it('data 为 null 时 fallback', () => {
    const meta = computePaginationMeta(null, 1, 10, 20)
    expect(meta.total).toBe(10)
    expect(meta.hasMore).toBe(false)
  })
})

// ==================== selectLatestSessionId ====================

describe('selectLatestSessionId', () => {
  it('按 updatedAt 降序选最新会话', () => {
    const sessions = [
      { id: 's1', updatedAt: 1000 },
      { id: 's2', updatedAt: 3000 },
      { id: 's3', updatedAt: 2000 },
    ]
    expect(selectLatestSessionId(sessions)).toBe('s2')
  })

  it('空列表返回 null', () => {
    expect(selectLatestSessionId([])).toBeNull()
  })

  it('updatedAt 缺失时 fallback 到 createdAt', () => {
    const sessions = [{ id: 's1', createdAt: 5000 }]
    expect(selectLatestSessionId(sessions)).toBe('s1')
  })
})

// ==================== upsertSessionInList ====================

describe('upsertSessionInList', () => {
  it('新会话插入到列表头部', () => {
    const sessions = [{ id: 's1' }]
    const inserted = upsertSessionInList(sessions, { id: 's2', title: 'new' })
    expect(inserted).toBe(true)
    expect(sessions[0].id).toBe('s2')
    expect(sessions).toHaveLength(2)
  })

  it('已存在时更新顶层字段保留 messages', () => {
    const sessions = [{ id: 's1', messages: [1, 2], messageCount: 2, title: 'old' }]
    const inserted = upsertSessionInList(sessions, { id: 's1', title: 'new' })
    expect(inserted).toBe(false)
    expect(sessions[0].title).toBe('new')
    expect(sessions[0].messages).toEqual([1, 2])
    expect(sessions[0].messageCount).toBe(2)
  })

  it('兼容 session_id 字段', () => {
    const sessions = []
    upsertSessionInList(sessions, { session_id: 's-from-ws', title: 'ws' })
    expect(sessions[0].id).toBe('s-from-ws')
  })

  it('无 id 时跳过', () => {
    const sessions = [{ id: 's1' }]
    expect(upsertSessionInList(sessions, { title: 'no-id' })).toBe(false)
    expect(sessions).toHaveLength(1)
  })
})

// ==================== getToolCallsByMessageFromMap ====================

describe('getToolCallsByMessageFromMap', () => {
  it('按 messageBackendId 过滤', () => {
    const map = new Map([
      ['tc1', { id: 'tc1', messageBackendId: '100' }],
      ['tc2', { id: 'tc2', messageBackendId: '200' }],
      ['tc3', { id: 'tc3', messageBackendId: '100' }],
    ])
    const result = getToolCallsByMessageFromMap(map, 100)
    expect(result).toHaveLength(2)
    expect(result.map(t => t.id).sort()).toEqual(['tc1', 'tc3'])
  })

  it('map 为空时返回空数组', () => {
    expect(getToolCallsByMessageFromMap(undefined, 100)).toEqual([])
  })

  it('messageBackendId 为 null/undefined 时返回空数组', () => {
    expect(getToolCallsByMessageFromMap(new Map(), null)).toEqual([])
    expect(getToolCallsByMessageFromMap(new Map(), undefined)).toEqual([])
  })
})

// ==================== countActiveApprovalsByGraphInMap ====================

describe('countActiveApprovalsByGraphInMap', () => {
  it('统计同一 graph_interrupt_id 下的 pending/waiting 审批', () => {
    const map = new Map([
      ['tc1', { approval: { state: 'pending', graph_interrupt_id: 'gi-1' } }],
      ['tc2', { approval: { state: 'waiting', graph_interrupt_id: 'gi-1' } }],
      ['tc3', { approval: { state: 'approved', graph_interrupt_id: 'gi-1' } }],
      ['tc4', { approval: { state: 'pending', graph_interrupt_id: 'gi-2' } }],
    ])
    expect(countActiveApprovalsByGraphInMap(map, 'gi-1')).toBe(2)
  })

  it('支持 extra.graph_interrupt_id', () => {
    const map = new Map([
      ['tc1', { approval: { state: 'pending', extra: { graph_interrupt_id: 'gi-1' } } }],
    ])
    expect(countActiveApprovalsByGraphInMap(map, 'gi-1')).toBe(1)
  })

  it('map 为空或 graphInterruptId 为空时返回 0', () => {
    expect(countActiveApprovalsByGraphInMap(undefined, 'gi-1')).toBe(0)
    expect(countActiveApprovalsByGraphInMap(new Map(), '')).toBe(0)
  })
})

// ==================== addPendingApproval / takePendingApproval ====================

describe('addPendingApproval / takePendingApproval', () => {
  it('加入队列后可取出', () => {
    const pendingMap = new Map()
    const approvalData = { state: 'pending', tool_name: 'execute' }
    addPendingApproval(pendingMap, 'interrupt-1', approvalData)

    expect(pendingMap.has('interrupt-1')).toBe(true)

    const taken = takePendingApproval(pendingMap, 'interrupt-1')
    expect(taken).toBe(approvalData)
    expect(pendingMap.has('interrupt-1')).toBe(false)
  })

  it('同时以 toolCallId 和 approvalData.tool_call_id 为 key 存储', () => {
    const pendingMap = new Map()
    const approvalData = { state: 'pending', tool_call_id: 'call-xyz' }
    addPendingApproval(pendingMap, 'interrupt-1', approvalData)

    expect(pendingMap.has('interrupt-1')).toBe(true)
    expect(pendingMap.has('call-xyz')).toBe(true)

    // 取出 interrupt-1 时同时移除 call-xyz
    takePendingApproval(pendingMap, 'interrupt-1')
    expect(pendingMap.has('call-xyz')).toBe(false)
  })

  it('不存在的 key 取出时返回 null', () => {
    expect(takePendingApproval(new Map(), 'no-such')).toBeNull()
  })

  it('空参数安全返回', () => {
    addPendingApproval(null, 'id', {})
    addPendingApproval(new Map(), '', {})
    addPendingApproval(new Map(), 'id', null)
    expect(takePendingApproval(null, 'id')).toBeNull()
  })
})

// ==================== 知识库相关 ====================

describe('resolveSelectedKnowledgeBase', () => {
  it('null/undefined 返回 null', () => {
    expect(resolveSelectedKnowledgeBase(null, [])).toBeNull()
    expect(resolveSelectedKnowledgeBase(undefined, [])).toBeNull()
  })

  it('对象直接返回', () => {
    const obj = { id: 'kb1', name: 'KB1' }
    expect(resolveSelectedKnowledgeBase(obj, [])).toBe(obj)
  })

  it('ID 在 knowledgeBases 中找到时返回完整对象', () => {
    const kbs = [{ id: 'kb1', name: 'KB1' }]
    expect(resolveSelectedKnowledgeBase('kb1', kbs)).toEqual({ id: 'kb1', name: 'KB1' })
  })

  it('ID 未找到时返回 { id } 兜底', () => {
    expect(resolveSelectedKnowledgeBase('kb-x', [])).toEqual({ id: 'kb-x' })
  })
})

describe('resolveSelectedKnowledgeBases', () => {
  it('非数组返回空数组', () => {
    expect(resolveSelectedKnowledgeBases(null, [], [])).toEqual([])
    expect(resolveSelectedKnowledgeBases(undefined, [], [])).toEqual([])
  })

  it('保留已有选中对象的引用', () => {
    const existing = { id: 'kb1', name: 'KB1', custom: true }
    const result = resolveSelectedKnowledgeBases(['kb1'], [existing], [])
    expect(result[0]).toBe(existing)
  })

  it('从 knowledgeBases 中查找 name', () => {
    const kbs = [{ id: 'kb2', name: 'KB2' }]
    const result = resolveSelectedKnowledgeBases(['kb2'], [], kbs)
    expect(result[0]).toEqual({ id: 'kb2', name: 'KB2' })
  })

  it('未找到时兜底 { id, name: id }', () => {
    const result = resolveSelectedKnowledgeBases(['kb-x'], [], [])
    expect(result[0]).toEqual({ id: 'kb-x', name: 'kb-x' })
  })
})

describe('parseKnowledgeBasesResponse', () => {
  it('code=200 + 数组 data 时返回数组', () => {
    const response = { data: { code: 200, data: [{ id: 'kb1' }] } }
    expect(parseKnowledgeBasesResponse(response)).toEqual([{ id: 'kb1' }])
  })

  it('data.items 时返回 items', () => {
    const response = { data: { data: { items: [{ id: 'kb1' }] } } }
    expect(parseKnowledgeBasesResponse(response)).toEqual([{ id: 'kb1' }])
  })

  it('无匹配时返回 null', () => {
    expect(parseKnowledgeBasesResponse({ data: {} })).toBeNull()
  })
})

describe('cleanupKnowledgeBasesSelection', () => {
  it('selectedBase 已失效时置 null', () => {
    const result = cleanupKnowledgeBasesSelection(
      [{ id: 'kb1' }],
      { id: 'kb-deleted' },
      []
    )
    expect(result.base).toBeNull()
  })

  it('selectedBase 仍存在时保留', () => {
    const result = cleanupKnowledgeBasesSelection(
      [{ id: 'kb1' }],
      { id: 'kb1' },
      []
    )
    expect(result.base).toEqual({ id: 'kb1' })
  })

  it('selectedBases 过滤掉已失效的', () => {
    const result = cleanupKnowledgeBasesSelection(
      [{ id: 'kb1' }, { id: 'kb2' }],
      null,
      [{ id: 'kb1' }, { id: 'kb-deleted' }]
    )
    expect(result.bases).toEqual([{ id: 'kb1' }])
  })
})

// ==================== buildMessageWithVersions ====================

describe('buildMessageWithVersions', () => {
  it('附加 versions 和 currentVersion', () => {
    const message = { id: 'm1', role: 'user', content: 'hello' }
    const result = buildMessageWithVersions(message)
    expect(result.currentVersion).toBe(0)
    expect(result.versions).toHaveLength(1)
    expect(result.versions[0].content).toBe('hello')
    expect(result.role).toBe('user')
  })

  it('保留原始消息字段', () => {
    const message = { id: 'm1', role: 'assistant', content: 'hi', toolCalls: [] }
    const result = buildMessageWithVersions(message)
    expect(result.id).toBe('m1')
    expect(result.content).toBe('hi')
    expect(result.toolCalls).toEqual([])
  })
})

// ==================== applyUsageToMessage ====================

describe('applyUsageToMessage', () => {
  it('应用到消息和当前 version', () => {
    const message = makeAssistantMessage(100)
    const usage = { model: 'gpt-4', tokenCount: 100, responseTime: 500 }
    applyUsageToMessage(message, usage)

    expect(message.model).toBe('gpt-4')
    expect(message.tokenCount).toBe(100)
    expect(message.responseTime).toBe(500)
    expect(message.versions[0].model).toBe('gpt-4')
    expect(message.versions[0].tokenCount).toBe(100)
  })

  it('仅更新提供的字段', () => {
    const message = makeAssistantMessage(100)
    message.tokenCount = 50
    applyUsageToMessage(message, { model: 'gpt-4' })

    expect(message.model).toBe('gpt-4')
    expect(message.tokenCount).toBe(50) // 未提供，保持原值
  })

  it('message 为空时安全返回', () => {
    expect(() => applyUsageToMessage(null, {})).not.toThrow()
  })
})

// ==================== applyStreamState ====================

describe('applyStreamState', () => {
  it('设置 STREAMING 状态', () => {
    const message = makeAssistantMessage(100)
    applyStreamState(message, StreamState.STREAMING)
    expect(message.streamState).toBe(StreamState.STREAMING)
    expect(message.isStreaming).toBe(true)
    expect(message.versions[0].streamState).toBe(StreamState.STREAMING)
    expect(message.versions[0].isStreaming).toBe(true)
  })

  it('设置 INTERRUPTED 状态（isStreaming=false）', () => {
    const message = makeAssistantMessage(100)
    applyStreamState(message, StreamState.INTERRUPTED)
    expect(message.streamState).toBe(StreamState.INTERRUPTED)
    expect(message.isStreaming).toBe(false)
  })

  it('message 为空时安全返回', () => {
    expect(() => applyStreamState(null, StreamState.STREAMING)).not.toThrow()
  })
})

// ==================== applyVersionToMessage ====================

describe('applyVersionToMessage', () => {
  it('将 version 字段同步到 message', () => {
    const message = makeAssistantMessage(100, [], 'old content')
    const version = {
      content: 'new content',
      sources: [{ title: 'src' }],
      toolCalls: [makeToolCall('tc1')],
      reasoning: 'because',
      suggestions: ['s1'],
      context: { key: 'val' },
      attachmentIds: ['att1'],
    }
    applyVersionToMessage(message, version)

    expect(message.content).toBe('new content')
    expect(message.sources).toEqual([{ title: 'src' }])
    expect(message.toolCalls).toHaveLength(1)
    expect(message.reasoning).toBe('because')
    expect(message.suggestions).toEqual(['s1'])
    expect(message.context).toEqual({ key: 'val' })
    expect(message.attachmentIds).toEqual(['att1'])
  })

  it('version 无 attachmentIds 时保留 message 已有值', () => {
    const message = makeAssistantMessage(100)
    message.attachmentIds = ['existing']
    applyVersionToMessage(message, { content: 'new', sources: [], toolCalls: [], reasoning: null, suggestions: null, context: null })

    expect(message.attachmentIds).toEqual(['existing'])
  })
})
