export const AgentMode = {
  BASIC_AGENT: 'basic-agent',
  RAG: 'rag',
  WORKFLOW: 'workflow',
  DEEP_RESEARCH: 'deep-research',
  GUARDED: 'guarded',
}

export const MessageRole = {
  USER: 'user',
  ASSISTANT: 'assistant',
  SYSTEM: 'system',
}

export const ToolCallStatus = {
  PENDING: 'pending',
  RUNNING: 'running',
  COMPLETED: 'completed',
  FAILED: 'failed',
}

export const PlanStepStatus = {
  PENDING: 'pending',
  IN_PROGRESS: 'in_progress',
  COMPLETED: 'completed',
  FAILED: 'failed',
}

export const ChainOfThoughtStepStatus = {
  PENDING: 'pending',
  ACTIVE: 'active',
  COMPLETE: 'complete',
}

export const QueueItemStatus = {
  PENDING: 'pending',
  COMPLETED: 'completed',
}

export const StreamChunkType = {
  START: 'start',
  CHUNK: 'chunk',
  TOOL: 'tool',
  TOOL_RESULT: 'tool_result',
  REASONING: 'reasoning',
  SOURCE: 'source',
  SOURCES: 'sources',
  PLAN: 'plan',
  TASK: 'task',
  QUEUE: 'queue',
  CONTEXT: 'context',
  CITATION: 'citation',
  CHAIN_OF_THOUGHT: 'chainOfThought',
  SUGGESTIONS: 'suggestions',
  END: 'end',
  ERROR: 'error',
}

export function createMessage(id, role, content, extra = {}) {
  return {
    id: id || Date.now().toString(),
    role,
    content,
    timestamp: new Date().toISOString(),
    ...extra,
  }
}

export function createUserMessage(content) {
  return createMessage(Date.now().toString(), MessageRole.USER, content)
}

export function createAssistantMessage(content = '', extra = {}) {
  return createMessage((Date.now() + 1).toString(), MessageRole.ASSISTANT, content, extra)
}

export function generateId() {
  return Date.now().toString() + Math.random().toString(36).substr(2, 9)
}

export function formatTimestamp(date) {
  if (typeof date === 'string') {
    date = new Date(date)
  }
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

export function formatDate(date) {
  if (typeof date === 'string') {
    date = new Date(date)
  }
  return date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
}

export function truncateText(text, maxLength = 50) {
  if (!text) return ''
  if (text.length <= maxLength) return text
  return text.slice(0, maxLength) + '...'
}

export function parseStreamChunk(data) {
  try {
    return typeof data === 'string' ? JSON.parse(data) : data
  } catch (e) {
    console.error('Failed to parse stream chunk:', e)
    return null
  }
}

export function isValidMode(mode) {
  return Object.values(AgentMode).includes(mode)
}

export function getModeLabel(mode) {
  const labels = {
    [AgentMode.BASIC_AGENT]: '基础代理',
    [AgentMode.RAG]: 'RAG 检索',
    [AgentMode.WORKFLOW]: '学习工作流',
    [AgentMode.DEEP_RESEARCH]: '深度研究',
    [AgentMode.GUARDED]: '安全代理',
  }
  return labels[mode] || mode
}

export function debounce(fn, delay = 300) {
  let timeoutId = null
  return function (...args) {
    if (timeoutId) {
      clearTimeout(timeoutId)
    }
    timeoutId = setTimeout(() => {
      fn.apply(this, args)
    }, delay)
  }
}

export function throttle(fn, limit = 100) {
  let inThrottle = false
  return function (...args) {
    if (!inThrottle) {
      fn.apply(this, args)
      inThrottle = true
      setTimeout(() => {
        inThrottle = false
      }, limit)
    }
  }
}
