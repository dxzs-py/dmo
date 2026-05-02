export const AgentMode = {
  BASIC_AGENT: 'basic-agent',
  RAG: 'rag',
  WORKFLOW: 'workflow',
  DEEP_RESEARCH: 'deep-research',
  DEEP_THINKING: 'deep-thinking',
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

export function parseStreamChunk(data) {
  try {
    return typeof data === 'string' ? JSON.parse(data) : data
  } catch (e) {
    return null
  }
}

export function isValidMode(mode) {
  return Object.values(AgentMode).includes(mode)
}

export function validateProp(value, typeName, required = false) {
  if (required && (value === undefined || value === null)) return false
  const typeMap = {
    String: (v) => typeof v === 'string',
    Number: (v) => typeof v === 'number' && !isNaN(v),
    Boolean: (v) => typeof v === 'boolean',
    Array: (v) => Array.isArray(v),
    Object: (v) => v !== null && typeof v === 'object' && !Array.isArray(v),
    Function: (v) => typeof v === 'function',
  }
  const validator = typeMap[typeName]
  return validator ? validator(value) : true
}

export function validateMessage(msg) {
  if (!msg || typeof msg !== 'object') return false
  if (!msg.id || typeof msg.id !== 'string') return false
  if (!Object.values(MessageRole).includes(msg.role)) return false
  if (msg.content === undefined || typeof msg.content !== 'string') return false
  return true
}


