/**
 * sessionStore 内部纯函数 helpers
 *
 * 抽取自 src/stores/session.js 的 _ 前缀 helper 与 action 内联纯变换逻辑。
 * 所有函数均为纯数据操作（不依赖 Vue 响应式 / Pinia / this），由 store 调用时传入 state。
 *
 * 设计原则：
 * - helper 不调用 triggerRef / 不操作 ref，仅变异传入的 Map/Array/Object
 * - 返回值告知调用方是否发生变更，由调用方决定是否 triggerRef
 * - 审批态与工具执行态解耦：approval.state 驱动审批面板，toolCall.status 由 tool_result 驱动
 */
import {
  mergeMessageFromBackend,
  updateApprovalStateInMap,
  updateToolCallStatusInMap,
  createMessageVersion,
} from './message-operations'
import { mapApprovalStateToStatus, PROTECTED_STREAM_STATES, StreamState } from '@/types'

// 非终态审批 state：保留真实 state（pending/processing/waiting）
// pending → pending_approval（渲染审批面板）；processing/waiting → 不渲染审批面板
const NON_TERMINAL_APPROVAL_STATES = ['pending', 'processing', 'waiting']
// 终态审批 state：保留真实 state（approved/rejected/timeout）
const TERMINAL_APPROVAL_STATES = ['approved', 'rejected', 'timeout']
// 工具终态 status：不被审批状态降级（completed/failed）
const TERMINAL_TOOL_STATUSES = ['completed', 'failed']

// ==================== ToolCalls Map 操作 ====================

/**
 * 获取或创建指定 session 的 toolCallMap
 * @param {Map<string, Map<string, Object>>} toolCallsMap - 顶层 Map（按 sessionId 索引）
 * @param {string} sessionId
 * @returns {{ map: Map<string, Object>|null, created: boolean }}
 */
export function ensureToolCallsMap(toolCallsMap, sessionId) {
  if (!sessionId) return { map: null, created: false }
  if (!toolCallsMap.has(sessionId)) {
    toolCallsMap.set(sessionId, new Map())
    return { map: toolCallsMap.get(sessionId), created: true }
  }
  return { map: toolCallsMap.get(sessionId), created: false }
}

/**
 * 获取或创建指定 session 的 pendingApprovalsMap
 * @returns {{ map: Map<string, {approvalData: Object, toolCallId: string}>|null, created: boolean }}
 */
export function ensurePendingApprovalsMap(pendingApprovals, sessionId) {
  if (!sessionId) return { map: null, created: false }
  if (!pendingApprovals.has(sessionId)) {
    pendingApprovals.set(sessionId, new Map())
    return { map: pendingApprovals.get(sessionId), created: true }
  }
  return { map: pendingApprovals.get(sessionId), created: false }
}

/**
 * 将 toolCallMap 同步到 messages 数组（最后一条 assistant 消息的 toolCalls）
 *
 * 单向数据流：Map → message.toolCalls（派生）。
 * 修改 toolCallMap 内对象属性后必须调用，确保 UI 刷新。
 * 同步范围：最后一条 assistant 消息的 toolCalls + 当前 version 的 toolCalls。
 */
export function syncMessageToolCalls(sessions, toolCallsMap, sessionId) {
  const session = sessions.find(s => s.id === sessionId)
  if (!session || session.messages.length === 0) return
  const lastMsg = session.messages[session.messages.length - 1]
  if (!lastMsg || lastMsg.role !== 'assistant') return
  const toolCallMap = toolCallsMap.get(sessionId)
  const toolCalls = toolCallMap ? Array.from(toolCallMap.values()) : []
  lastMsg.toolCalls = toolCalls
  const ver = lastMsg.versions?.[lastMsg.currentVersion]
  if (ver) ver.toolCalls = toolCalls
}

/**
 * 将 message.toolCalls 反向同步到 toolCallsMap（合并后修复 Map 与消息不一致）
 *
 * 触发场景：mergeMessageFromBackend 将后端 toolCalls 合并到 message.toolCalls，
 * 但不会更新 toolCallsMap（唯一真相源）。后续 syncMessageToolCalls 被调用时，
 * 会用 Map 中旧数据覆盖 message.toolCalls，导致工具调用丢失。
 *
 * 合并策略：
 * - Map 缺失的 toolCall：从 message 补入（后端权威数据）
 * - Map 已有：合并后端数据（result/status 等），保留本地审批状态与终态
 * - toolCall 携带 approval 时，同步 approval.state 与 status（直接操作 Map，避免循环调用 store action）
 *
 * @returns {boolean} 是否有变更（调用方据此决定是否 triggerRef）
 */
export function syncToolCallsMapFromMessage(toolCallsMap, sessionId, message) {
  if (!sessionId || !message) return false
  const { map: toolCallMap } = ensureToolCallsMap(toolCallsMap, sessionId)
  if (!toolCallMap) return false
  const toolCalls = Array.isArray(message.toolCalls) ? message.toolCalls : []
  if (toolCalls.length === 0) return false
  let changed = false
  for (const tc of toolCalls) {
    const key = tc.tool_call_id || tc.id
    if (!key) continue
    if (!toolCallMap.has(key)) {
      // Map 缺失：从后端合并的 toolCalls 补入
      toolCallMap.set(key, { ...tc })
      changed = true
    } else {
      // Map 已有：合并后端数据，保留本地审批状态与结果
      const existing = toolCallMap.get(key)
      const merged = { ...existing, ...tc }
      if (existing.approval && !tc.approval) merged.approval = existing.approval
      if (existing.result != null && tc.result == null) merged.result = existing.result
      toolCallMap.set(key, merged)
      changed = true
    }
    // toolCall 携带 approval 时，同步 approval.state → status
    // 保留真实非终态 state：pending→pending_approval（渲染审批面板），
    // processing→processing / waiting→waiting（不渲染审批面板，因工具已被审批）
    const approvalState = tc.approval?.state
    if (approvalState) {
      const targetState = NON_TERMINAL_APPROVAL_STATES.includes(approvalState)
        ? approvalState
        : (TERMINAL_APPROVAL_STATES.includes(approvalState) ? approvalState : null)
      if (targetState) {
        updateApprovalStateInMap(toolCallMap, key, targetState)
        const existing = toolCallMap.get(key)
        // 工具已处于终态（completed/failed）时不被审批状态降级
        if (!TERMINAL_TOOL_STATUSES.includes(existing?.status)) {
          updateToolCallStatusInMap(toolCallMap, key, mapApprovalStateToStatus(targetState))
        }
      }
    }
  }
  return changed
}

// ==================== 消息合并 ====================

/**
 * 将本地消息数组与后端消息数组合并（字段级合并，保护本地流式消息）
 *
 * 合并规则：
 * - 按 backendId / id 匹配本地与后端消息
 * - 匹配命中：调用 mergeMessageFromBackend 字段级合并（保护态消息保留本地 content/toolCalls）
 * - 后端独有：直接追加
 * - 本地独有：仅在 PROTECTED_STREAM_STATES 中时追加（流式中消息后端尚未持久化）
 * - 按 timestamp 升序排序
 *
 * @param {Array} localMessages - 本地消息数组
 * @param {Array} backendMessages - 后端消息数组（已转换）
 * @returns {Array} 合并后的消息数组
 */
export function mergeMessagesFromBackend(localMessages, backendMessages) {
  if (!localMessages || localMessages.length === 0) return backendMessages || []
  if (!backendMessages || backendMessages.length === 0) return localMessages

  const merged = []
  const usedLocalIds = new Set()

  for (const backendMsg of backendMessages) {
    const backendId = backendMsg.backendId?.toString()
    const backendAltId = backendMsg.id?.toString()
    const local = localMessages.find(m =>
      (backendId && m.backendId?.toString() === backendId) ||
      (backendAltId && m.id?.toString() === backendAltId)
    )
    if (local) {
      mergeMessageFromBackend(local, backendMsg)
      merged.push(local)
      usedLocalIds.add(local.id)
    } else {
      merged.push(backendMsg)
    }
  }

  // 补回本地独有的保护态消息（流式中消息，后端可能尚未持久化）
  for (const local of localMessages) {
    if (!usedLocalIds.has(local.id) && PROTECTED_STREAM_STATES.has(local.streamState)) {
      merged.push(local)
    }
  }

  // 按时间戳升序排序
  merged.sort((a, b) => {
    const aTime = new Date(a.timestamp || a.createdAt || 0).getTime()
    const bTime = new Date(b.timestamp || b.createdAt || 0).getTime()
    return aTime - bTime
  })

  return merged
}

/**
 * 查找 session 中最后一条 assistant 消息
 * @returns {Object|null} 消息对象（不存在时返回 null）
 */
export function findLastAssistantMessage(session) {
  if (!session?.messages || session.messages.length === 0) return null
  for (let i = session.messages.length - 1; i >= 0; i--) {
    if (session.messages[i].role === 'assistant') return session.messages[i]
  }
  return null
}

/**
 * 查找 session 中最后一条 user 消息
 * @returns {Object|null} 消息对象（不存在时返回 null）
 */
export function findLastUserMessage(session) {
  if (!session?.messages) return null
  for (let i = session.messages.length - 1; i >= 0; i--) {
    if (session.messages[i].role === 'user') return session.messages[i]
  }
  return null
}

// ==================== Session 列表操作 ====================

/**
 * 合并分页加载的会话列表
 * @param {Array} existing - 已有会话
 * @param {Array} incoming - 新加载会话（已转换、已过滤 null）
 * @param {number} page - 当前页码（<=1 替换，>1 追加去重）
 * @returns {Array} 合并后的会话列表
 */
export function mergeSessionList(existing, incoming, page) {
  if (page <= 1) return incoming
  const existingIds = new Set(existing.map(s => s.id))
  const merged = [...existing]
  for (const s of incoming) {
    if (!existingIds.has(s.id)) merged.push(s)
  }
  return merged
}

/**
 * 计算分页元数据
 * @param {*} data - 后端响应 data 字段（可能是数组或对象）
 * @param {number} page - 请求页码
 * @param {number} sessionsCount - 当前会话总数（fallback 用）
 * @param {number} defaultPageSize - 默认页大小
 * @returns {{ total: number, page: number, pageSize: number, totalPages: number, hasMore: boolean }}
 */
export function computePaginationMeta(data, page, sessionsCount, defaultPageSize) {
  if (data && typeof data === 'object' && !Array.isArray(data)) {
    return {
      total: data.total || 0,
      page: data.page || page,
      pageSize: data.page_size || defaultPageSize,
      totalPages: data.total_pages || 1,
      hasMore: (data.page || page) < (data.total_pages || 1),
    }
  }
  return {
    total: sessionsCount,
    page: 1,
    pageSize: defaultPageSize,
    totalPages: 1,
    hasMore: false,
  }
}

/**
 * 按 updatedAt 降序选出最新会话 ID
 * @param {Array} sessions
 * @returns {string|null} 最新会话 ID（空列表时返回 null）
 */
export function selectLatestSessionId(sessions) {
  if (!sessions || sessions.length === 0) return null
  const latest = [...sessions].sort((a, b) => {
    const aTime = new Date(a.updatedAt || a.createdAt || 0).getTime()
    const bTime = new Date(b.updatedAt || b.createdAt || 0).getTime()
    return bTime - aTime
  })[0]
  return latest?.id || null
}

/**
 * upsert session 到列表（不存在则插入头部，存在则更新顶层字段保留 messages）
 * 兼容后端 payload 的 session_id 字段（session_created 事件用 session_id 而非 id）。
 * @param {Array} sessions - 会话列表（变异写入）
 * @param {Object} sessionData - 后端返回的 session 数据
 * @returns {boolean} 是否执行了插入（true=新插入，false=更新或跳过）
 */
export function upsertSessionInList(sessions, sessionData) {
  const id = sessionData?.id || sessionData?.session_id
  if (!id) return false
  const idx = sessions.findIndex(s => s.id === id)
  if (idx === -1) {
    sessions.unshift({ ...sessionData, id })
    return true
  }
  const local = sessions[idx]
  Object.assign(local, {
    ...sessionData,
    id, // 确保 id 字段一致
    messages: local.messages, // 保留本地 messages
    messageCount: local.messageCount,
  })
  return false
}

// ==================== ToolCall 查询 ====================

/**
 * 按 messageBackendId 过滤 toolCall（用于按消息分组显示工具调用）
 * @param {Map<string, Object>|undefined} toolCallMap
 * @param {number|string} messageBackendId
 * @returns {Array} 匹配的 toolCall 数组
 */
export function getToolCallsByMessageFromMap(toolCallMap, messageBackendId) {
  if (!toolCallMap || messageBackendId === undefined || messageBackendId === null) return []
  const target = String(messageBackendId)
  return Array.from(toolCallMap.values()).filter(
    tc => tc.messageBackendId !== undefined && String(tc.messageBackendId) === target
  )
}

/**
 * 统计指定会话中同一 graph_interrupt_id 下的活跃审批数
 * 活跃状态包括：pending（待审批）/ waiting（已审批等待同批次）。
 * 基于 toolCallsMap（单一真相源）派生，用于"全部确认"按钮显示判断。
 * @param {Map<string, Object>|undefined} toolCallMap
 * @param {string} graphInterruptId - LangGraph 的 interrupt_id（同一批审批共享）
 * @returns {number} 活跃审批数量
 */
export function countActiveApprovalsByGraphInMap(toolCallMap, graphInterruptId) {
  if (!toolCallMap || !graphInterruptId) return 0
  const ACTIVE_STATES = new Set(['pending', 'waiting'])
  let count = 0
  for (const tc of toolCallMap.values()) {
    const approval = tc.approval
    if (!approval) continue
    const tcGid = approval.graph_interrupt_id || approval.extra?.graph_interrupt_id
    if (tcGid === graphInterruptId && ACTIVE_STATES.has(approval.state)) {
      count++
    }
  }
  return count
}

// ==================== 审批 pending 队列 ====================

/**
 * 将审批数据加入 pendingApprovals 队列（占位 toolCall 的兜底机制）
 *
 * 同时以 toolCallId 和 approvalData.tool_call_id 作为 key 存储，
 * 确保后续 tool 事件以任一 ID 到达时都能匹配。
 * @param {Map<string, {approvalData: Object, toolCallId: string}>} pendingMap
 * @param {string} toolCallId - 工具调用 ID（interrupt_id）
 * @param {Object} approvalData - 审批事件数据
 */
export function addPendingApproval(pendingMap, toolCallId, approvalData) {
  if (!pendingMap || !toolCallId || !approvalData) return
  const pendingIds = [toolCallId, approvalData.tool_call_id].filter(Boolean)
  for (const pid of pendingIds) {
    pendingMap.set(pid, { approvalData, toolCallId })
  }
}

/**
 * 从 pendingApprovals 队列中取出并移除审批数据
 *
 * 同时移除 altId（approvalData.tool_call_id）对应的条目。
 * @param {Map<string, {approvalData: Object, toolCallId: string}>} pendingMap
 * @param {string} toolCallId
 * @returns {Object|null} approvalData（不存在时返回 null）
 */
export function takePendingApproval(pendingMap, toolCallId) {
  if (!pendingMap || !toolCallId) return null
  const pending = pendingMap.get(toolCallId)
  if (!pending) return null
  pendingMap.delete(toolCallId)
  if (pending.approvalData?.tool_call_id && pending.approvalData.tool_call_id !== toolCallId) {
    pendingMap.delete(pending.approvalData.tool_call_id)
  }
  return pending.approvalData
}

// ==================== 知识库选择 ====================

/**
 * 解析 setSelectedKnowledgeBase 的入参为对象
 * @param {string|Object|null} kbIdOrObj
 * @param {Array} knowledgeBases - 全部知识库（用于按 ID 查找）
 * @returns {Object|null} 选中知识库对象
 */
export function resolveSelectedKnowledgeBase(kbIdOrObj, knowledgeBases) {
  if (!kbIdOrObj) return null
  if (typeof kbIdOrObj === 'object') return kbIdOrObj
  const found = knowledgeBases.find(kb => kb.id === kbIdOrObj)
  return found || { id: kbIdOrObj }
}

/**
 * 解析 setSelectedKnowledgeBases 的入参为对象数组（保留已有选中对象的 name）
 * @param {Array} kbIds - 知识库 ID 数组
 * @param {Array} currentSelected - 当前已选中的知识库对象数组
 * @param {Array} knowledgeBases - 全部知识库
 * @returns {Array} 选中知识库对象数组
 */
export function resolveSelectedKnowledgeBases(kbIds, currentSelected, knowledgeBases) {
  if (!kbIds || !Array.isArray(kbIds)) return []
  return kbIds.map(id => {
    const existing = currentSelected.find(kb => kb.id === id)
    if (existing) return existing
    const kb = knowledgeBases.find(k => k.id === id)
    return kb ? { id: kb.id, name: kb.name } : { id, name: id }
  })
}

/**
 * 解析 loadKnowledgeBases 的后端响应
 * @returns {Array|null} 知识库数组（无匹配时返回 null）
 */
export function parseKnowledgeBasesResponse(response) {
  if (response.data?.code === 200 && Array.isArray(response.data.data)) {
    return response.data.data
  }
  if (response.data?.data?.items) {
    return response.data.data.items
  }
  return null
}

/**
 * 加载知识库后清理已失效的选中状态
 * @returns {{ base: Object|null, bases: Array }} 清理后的选中状态
 */
export function cleanupKnowledgeBasesSelection(knowledgeBases, selectedBase, selectedBases) {
  let base = selectedBase
  let bases = selectedBases
  if (selectedBase?.id) {
    const stillExists = knowledgeBases.some(kb => kb.id === selectedBase.id)
    if (!stillExists) base = null
  }
  if (selectedBases.length > 0) {
    bases = selectedBases.filter(kb => knowledgeBases.some(k => k.id === (kb.id || kb)))
  }
  return { base, bases }
}

// ==================== 消息字段操作 ====================

/**
 * 为消息附加 versions / currentVersion 字段（addMessageToSession 用）
 * @param {Object} message - 原始消息
 * @returns {Object} 带 versions 的新消息对象
 */
export function buildMessageWithVersions(message) {
  return {
    ...message,
    versions: [createMessageVersion(message)],
    currentVersion: 0,
  }
}

/**
 * 将 usage 数据应用到消息（同步到当前 version）
 * @param {Object} message - 消息对象（变异写入）
 * @param {Object} usageData - { model, tokenCount, tokens, tokenDetail, responseTime }
 */
export function applyUsageToMessage(message, usageData) {
  if (!message || !usageData) return
  const fields = ['model', 'tokenCount', 'tokens', 'tokenDetail', 'responseTime']
  for (const f of fields) {
    if (usageData[f] !== undefined) message[f] = usageData[f]
  }
  const ver = message.versions?.[message.currentVersion]
  if (ver) {
    for (const f of fields) {
      if (usageData[f] !== undefined) ver[f] = usageData[f]
    }
  }
}

/**
 * 设置消息的流式状态（streamState + isStreaming），同步到当前 version
 * @param {Object} message - 消息对象（变异写入）
 * @param {string} state - StreamState 枚举值
 */
export function applyStreamState(message, state) {
  if (!message) return
  message.streamState = state
  message.isStreaming = (state === StreamState.STREAMING)
  const ver = message.versions?.[message.currentVersion]
  if (ver) {
    ver.streamState = state
    ver.isStreaming = message.isStreaming
  }
}

/**
 * 将 version 的字段同步到 message（switchMessageVersion 用）
 * @param {Object} message - 消息对象（变异写入）
 * @param {Object} version - 版本对象
 */
export function applyVersionToMessage(message, version) {
  if (!message || !version) return
  message.content = version.content
  message.sources = version.sources
  message.toolCalls = version.toolCalls
  message.reasoning = version.reasoning
  message.suggestions = version.suggestions
  message.context = version.context
  message.attachmentIds = version.attachmentIds || message.attachmentIds
}
