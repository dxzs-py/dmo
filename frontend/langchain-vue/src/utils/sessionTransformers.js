import { generateId } from './id.js'

const API_SUCCESS_CODE = 200

// =============================================================================
// 通用深层键名转换函数
// 唯一命名风格转换边界：snake_case ↔ camelCase
// 使用 Object.prototype.toString.call 严格判断类型，防止 Date/RegExp 等被破坏
// =============================================================================

/**
 * 判断 value 是否为可遍历的普通对象（非 Date/RegExp/File/Blob/FormData/null 等）
 * @param {*} value
 * @returns {boolean}
 */
function _isPlainObject(value) {
  if (value === null || typeof value !== 'object') return false
  const tag = Object.prototype.toString.call(value)
  // 只处理纯 Object 字面量，排除 Date/RegExp/File/Blob/FormData/ArrayBuffer 等
  return tag === '[object Object]'
}

/**
 * snake_case 字符串 → camelCase 字符串（公共导出，用于转换单个字符串值）
 * @param {string} str
 * @returns {string}
 */
export function convertSnakeToCamel(str) {
  return str.replace(/_([a-z])/g, (_, c) => c.toUpperCase())
}

// 内部别名，保持向后兼容
const _convertSnakeToCamel = convertSnakeToCamel

/**
 * camelCase 字符串 → snake_case 字符串
 * @param {string} str
 * @returns {string}
 */
function _camelToSnake(str) {
  return str.replace(/([A-Z])/g, '_$1').toLowerCase()
}

/**
 * 协议标识符保护（值不参与键名转换）：
 * `subagent_contents` 的对象子键是 subagent_thread_id 协议路由标识符值
 * （如 `subagent_xxx`，与 toolCalls.subagentThreadId / SSE 事件顶层
 * subagent_thread_id 同值）。若被 toCamelCase 当作普通键名转换
 * （`subagent_xxx` → `subagentXxx`），刷新后按 threadId 取正文会失配，
 * 子代理卡正文丢失（仅触发浏览器实时事件正常）。此处保护子键原样，
 * 仅递归转换子对象内部字段（content/reasoning_content/agent_name/depth）。
 *
 * @param {*} obj - subagent_contents 对象
 * @returns {*}
 */
function _preserveProtocolKeys(obj) {
  if (obj === null || obj === undefined) return obj
  if (Array.isArray(obj)) return obj.map(v => _preserveProtocolKeys(v))
  if (!_isPlainObject(obj)) return obj
  const result = {}
  for (const key of Object.keys(obj)) {
    result[key] = toCamelCase(obj[key], 1)
  }
  return result
}

/**
 * 递归将对象所有键从 snake_case 转为 camelCase
 * 跳过 Date/RegExp/File/Blob/FormData/null/基本类型
 * @param {*} obj
 * @returns {*}
 */
export function toCamelCase(obj, _depth = 0) {
  if (obj === null || obj === undefined) return obj
  if (Array.isArray(obj)) return obj.map(v => toCamelCase(v, _depth + 1))
  if (!_isPlainObject(obj)) return obj

  const result = {}
  for (const key of Object.keys(obj)) {
    const camelKey = _convertSnakeToCamel(key)
    // subagent_contents：子键为协议标识符值，保持原样（仅转换内部字段）
    result[camelKey] = key === 'subagent_contents'
      ? _preserveProtocolKeys(obj[key])
      : toCamelCase(obj[key], _depth + 1)
  }
  return result
}

/**
 * 递归将对象所有键从 camelCase 转为 snake_case
 * 跳过 Date/RegExp/File/Blob/FormData/null/基本类型
 * @param {*} obj
 * @returns {*}
 */
export function toSnakeCase(obj, _depth = 0) {
  if (obj === null || obj === undefined) return obj
  if (Array.isArray(obj)) return obj.map(v => toSnakeCase(v, _depth + 1))
  if (!_isPlainObject(obj)) return obj

  const result = {}
  for (const key of Object.keys(obj)) {
    const snakeKey = _camelToSnake(key)
    result[snakeKey] = toSnakeCase(obj[key], _depth + 1)
  }
  return result
}

// =============================================================================
// 会话转换函数（从后端 snake_case 转前端 camelCase）
// 注：这些函数处理的是 REST API 响应数据。由于 axios 响应拦截器已调用 toCamelCase，
// 此处收到的数据已是 camelCase 格式。函数负责数据规范化（ID 生成、日期解析等），
// 不做键名转换。
// =============================================================================

export function isApiSuccess(response) {
  return response?.data?.code === API_SUCCESS_CODE
}

export function transformBackendSessionToFrontend(session) {
  if (!session) {
    return null
  }

  const sessionObj = session || {}
  const rawMessages = sessionObj.messages || []
  const validMessages = Array.isArray(rawMessages) ? rawMessages : []

  // 合并连续的 assistant 消息
  // 注：由于 axios 响应拦截器已调用 toCamelCase，此处收到的数据已是 camelCase。
  // _mergeConsecutiveAssistantMessages 操作原始 camelCase 数据。
  const mergedMessages = _mergeConsecutiveAssistantMessages(validMessages)

  const sanitized = {
    id: sessionObj.sessionId || sessionObj.id,
    sessionId: sessionObj.sessionId || sessionObj.id,
    title: sessionObj.title || '新对话',
    mode: sessionObj.mode || 'agent',
    selectedKnowledgeBase: sessionObj.selectedKnowledgeBase || null,
    selectedKnowledgeBases: sessionObj.selectedKnowledgeBases || [],
    messageCount: sessionObj.messageCount || 0,
    messages: mergedMessages
      .map(transformBackendMessageToFrontend)
      .filter(msg => msg !== null),
    createdAt: sessionObj.createdAt ? new Date(sessionObj.createdAt).getTime() : Date.now(),
    updatedAt: sessionObj.updatedAt ? new Date(sessionObj.updatedAt).getTime() : Date.now(),
  }

  if (!Array.isArray(sanitized.messages)) {
    sanitized.messages = []
  }

  if (!sanitized.id && sessionObj.id) {
    sanitized.id = sessionObj.id
    sanitized.sessionId = sessionObj.id
  }

  return sanitized
}

/**
 * 合并连续的 assistant 消息。
 * 当 syncLastMessageToBackend 竞态导致同一 AI 回复被保存为多条消息时，
 * 将含 tool_calls 的消息与后续含 content 的消息合并。
 */
function _mergeConsecutiveAssistantMessages(messages) {
  if (!messages || messages.length <= 1) return messages

  const result = []
  let i = 0
  while (i < messages.length) {
    const msg = messages[i]

    if (msg.role === 'assistant' && i + 1 < messages.length) {
      const nextMsg = messages[i + 1]

      // 情况1：占位消息（无内容无 toolCalls）或 有 toolCalls 但 content 为空，下一条也是 assistant
      const isPlaceholder = !_hasContent(msg) && !_hasToolCalls(msg)
      if ((isPlaceholder || (_hasToolCalls(msg) && !_hasContent(msg))) && nextMsg.role === 'assistant') {
        const merged = { ...msg }
        // 合并 content
        merged.content = nextMsg.content || msg.content || ''
        // 合并 tool_calls：将两条消息的 tool_calls 去重合并（修复：原逻辑因 merged
        // 浅拷贝了 msg.tool_calls 导致 !_hasToolCalls(merged) 永远为 false，
        // nextMsg.tool_calls 被丢弃，刷新后只保留第一条消息的 tool_calls）
        if (_hasToolCalls(nextMsg)) {
          const existingIds = new Set((merged.toolCalls || []).map(tc => tc.id || tc.toolCallId))
          const nextToolCalls = (nextMsg.toolCalls || []).filter(
            tc => !existingIds.has(tc.id || tc.toolCallId)
          )
          merged.toolCalls = [...(merged.toolCalls || []), ...nextToolCalls]
        }
        // 合并其他字段（plan, chainOfThought, reasoning 等）
        for (const field of ['researchTaskId', 'reasoning', 'suggestions', 'sources', 'plan',
          'chainOfThought', 'model', 'streamState', 'isStreaming', 'researchContext', 'subagentContents']) {
          if (!merged[field] && nextMsg[field]) {
            merged[field] = nextMsg[field]
          }
        }
        // 合并 versions
        if (nextMsg.versions && nextMsg.versions.length > 0) {
          if (!merged.versions || merged.versions.length === 0) {
            merged.versions = nextMsg.versions
          } else {
            // 将下一条消息的 content 合并到当前消息的 versions 中
            for (const ver of merged.versions) {
              if (!ver.content && nextMsg.content) {
                ver.content = nextMsg.content
              }
            }
          }
        }
        // 将合并后的 toolCalls 同步到 versions 中，确保 transformBackendMessageToFrontend
        // 从 activeVersion.toolCalls 取值时不会丢失工具调用数据
        if (merged.toolCalls && merged.toolCalls.length > 0) {
          if (merged.versions && merged.versions.length > 0) {
            for (const ver of merged.versions) {
              ver.toolCalls = merged.toolCalls
            }
          } else {
            merged.versions = [{
              id: merged.id || '',
              content: merged.content || '',
              toolCalls: merged.toolCalls,
            }]
          }
        }
        result.push(merged)
        i += 2
        continue
      }
    }

    result.push(msg)
    i++
  }

  return result
}

function _hasToolCalls(msg) {
  return msg.toolCalls && Array.isArray(msg.toolCalls) && msg.toolCalls.length > 0
}

function _hasContent(msg) {
  return msg.content && msg.content.trim().length > 0
}

export function transformBackendMessageToFrontend(msg) {
  if (!msg) {
    return null
  }

  const msgObj = msg || {}

  let role = msgObj.role
  if (!role || !['user', 'assistant', 'system'].includes(role)) {
    role = 'user'
  }

  const backendVersions = msgObj.versions
  const hasValidVersions = Array.isArray(backendVersions) && backendVersions.length > 0

  let versions
  if (hasValidVersions) {
    versions = backendVersions.map(v => ({
      id: v.id?.toString() || generateId(),
      content: v.content || '',
      sources: v.sources || [],
      plan: v.plan || null,
      chainOfThought: v.chainOfThought || null,
      toolCalls: v.toolCalls || [],
      subagentContents: v.subagentContents || {},
      reasoning: v.reasoning || null,
      suggestions: v.suggestions || null,
      context: v.context || null,
    }))
  } else {
    const version = {
      id: msgObj.id?.toString() || generateId(),
      content: msgObj.content || '',
      sources: msgObj.sources || [],
      plan: msgObj.plan || null,
      chainOfThought: msgObj.chainOfThought || null,
      toolCalls: msgObj.toolCalls || [],
      subagentContents: msgObj.subagentContents || {},
      reasoning: msgObj.reasoning || null,
      suggestions: msgObj.suggestions || null,
      context: msgObj.context || null,
    }
    versions = [version]
  }

  const currentVersion = typeof msgObj.currentVersion === 'number' && msgObj.currentVersion >= 0
    ? Math.min(msgObj.currentVersion, versions.length - 1)
    : 0

  const activeVersion = versions[currentVersion]

  // 顶层字段是权威数据源（后端 persist_stream_result 增量维护顶层
  // content/tool_calls/reasoning；versions 是消息创建时的初始快照，
  // 审批中断/流式演进后不随顶层更新，导致 versions[0] 内容回退、
  // 工具缺失/状态陈旧）。统一顶层优先，versions 仅兜底（根因修复）。
  const msgToolCalls = Array.isArray(msgObj.toolCalls) ? msgObj.toolCalls : []
  const versionToolCalls = (activeVersion && Array.isArray(activeVersion.toolCalls))
    ? activeVersion.toolCalls
    : []
  const toolCalls = msgToolCalls.length > 0 ? msgToolCalls : versionToolCalls

  return {
    id: msgObj.id?.toString() || generateId(),
    backendId: msgObj.id,
    role: role,
    content: msgObj.content || activeVersion.content || '',
    sources: msgObj.sources || activeVersion.sources || [],
    plan: msgObj.plan || activeVersion.plan || null,
    chainOfThought: msgObj.chainOfThought || activeVersion.chainOfThought || null,
    toolCalls: toolCalls,
    // 顶层 subagent_contents 是后端持久化权威（persist_stream_result 维护），
    // versions 快照兜底（消息创建时初始快照）
    subagentContents: msgObj.subagentContents || activeVersion.subagentContents || {},
    approval: msgObj.approval || null,
    approvalState: msgObj.approval?.state
      || toolCalls.find(tc => tc.approval)?.approval?.state
      || null,
    reasoning: msgObj.reasoning || activeVersion.reasoning || null,
    suggestions: msgObj.suggestions || activeVersion.suggestions || null,
    context: activeVersion.context || msgObj.context || null,
    attachmentIds: msgObj.attachmentIds || [],
    attachments: msgObj.attachments || [],
    researchContext: msgObj.researchContext || null,
    versions: versions,
    currentVersion: currentVersion,
    isFinalized: !!msgObj.isFinalized,
    timestamp: msgObj.createdAt ? new Date(msgObj.createdAt).getTime() : Date.now(),
    model: msgObj.model || null,
    tokenCount: msgObj.tokenCount || 0,
    tokenDetail: msgObj.tokenDetail || null,
    responseTime: msgObj.responseTime || 0,
    researchTaskId: msgObj.researchTaskId || null,
    researchTaskStatus: msgObj.researchTaskStatus || null,
    researchTaskDeleted: msgObj.researchTaskDeleted || null,
    streamState: msgObj.isStreaming ? 'streaming' : (msgObj.streamState || undefined),
  }
}

export function transformFrontendMessageToBackend(msg) {
  const backendVersions = Array.isArray(msg.versions) && msg.versions.length > 0
    ? msg.versions.map(v => ({
        id: v.id,
        content: v.content || '',
        sources: v.sources || [],
        plan: v.plan || null,
        chainOfThought: v.chainOfThought || null,
        toolCalls: v.toolCalls || [],
        subagentContents: v.subagentContents || {},
        reasoning: v.reasoning || null,
        suggestions: v.suggestions || null,
        context: v.context || null,
      }))
    : [{
        id: msg.id,
        content: msg.content || '',
        sources: msg.sources || [],
        plan: msg.plan || null,
        chainOfThought: msg.chainOfThought || null,
        toolCalls: msg.toolCalls || [],
        subagentContents: msg.subagentContents || {},
        reasoning: msg.reasoning || null,
        suggestions: msg.suggestions || null,
        context: msg.context || null,
      }]

  // 输出 camelCase，由 axios 请求拦截器的 toSnakeCase 统一转换为 snake_case
  // 注意：顶层不发送 tool_calls —— 后端 ChatMessageSerializer.tool_calls 为 read_only
  // （serializers.py），POST/PATCH 均不接收，发送纯属无效负载（spec REMOVED Requirements）。
  // toolCalls 的持久化权威是后端流式/审批事件；versions[].toolCalls 保留用于
  // transformBackendMessageToFrontend 刷新后从 activeVersion 恢复工具调用数据。
  // subagent_contents 同样为 read_only，顶层不发送；versions[].subagentContents
  // 用于刷新后从 activeVersion 恢复子代理图层正文快照。
  return {
    role: msg.role,
    content: msg.content,
    sources: msg.sources || [],
    plan: msg.plan || null,
    chainOfThought: msg.chainOfThought || null,
    approval: msg.approval || null,
    reasoning: msg.reasoning || null,
    suggestions: msg.suggestions || null,
    context: msg.context || null,
    attachmentIds: msg.attachmentIds || [],
    attachments: msg.attachments || [],
    researchContext: msg.researchContext || null,
    versions: backendVersions,
    currentVersion: typeof msg.currentVersion === 'number' ? msg.currentVersion : 0,
    isFinalized: !!msg.isFinalized,
    model: msg.model || null,
    tokenCount: msg.tokenCount || 0,
    tokenDetail: msg.tokenDetail || {},
    responseTime: msg.responseTime || 0,
    researchTaskId: msg.researchTaskId || null,
  }
}
