/**
 * 消息角色枚举
 * @enum {string}
 */
export const MessageRole = {
  USER: 'user',
  ASSISTANT: 'assistant',
  SYSTEM: 'system',
}

/**
 * 工具调用状态
 * @enum {string}
 */
export const ToolCallStatus = {
  PENDING: 'pending',
  RUNNING: 'running',
  COMPLETED: 'completed',
  FAILED: 'failed',
}

/**
 * 计划步骤状态
 * @enum {string}
 */
export const PlanStepStatus = {
  PENDING: 'pending',
  IN_PROGRESS: 'in_progress',
  COMPLETED: 'completed',
  FAILED: 'failed',
}

/**
 * 聊天消息对象结构
 * @typedef {Object} ChatMessage
 * @property {string} id - 唯一标识符
 * @property {string} role - 消息角色 (user|assistant|system)
 * @property {string} content - 消息内容（支持Markdown）
 * @property {string} [timestamp] - ISO格式时间戳
 * @property {Array<Object>} [sources] - 引用来源列表
 * @property {Object} [plan] - 执行计划
 * @property {string} [plan.title] - 计划标题
 * @property {string} [plan.description] - 计划描述
 * @property {Array<Object>} [plan.steps] - 计划步骤
 * @property {Array<Object>} [chainOfThought] - 思维链步骤
 * @property {Array<Object>} [toolCalls] - 工具调用列表
 * @property {Object} [reasoning] - 推理信息
 * @property {string} [reasoning.content] - 推理内容
 * @property {number} [reasoning.duration] - 推理耗时(ms)
 * @property {Array<Object>} [versions] - 消息版本历史
 */

/**
 * 工具调用对象
 * @typedef {Object} ToolCall
 * @property {string} name - 工具名称
 * @property {Object} input - 输入参数
 * @property {Object|string} [output] - 输出结果
 * @property {string} [status='completed'] - 执行状态 (pending|running|completed|failed)
 */

/**
 * 来源引用对象
 * @typedef {Object} Source
 * @property {string} id - 来源ID
 * @property {string} title - 标题
 * @property {string} [url] - 链接地址
 * @property {string} [content] - 内容摘要
 * @property {number} [relevance] - 相关度评分(0-1)
 */

/**
 * 可用聊天模式配置
 * @typedef {Object.<string, string>} AvailableModes
 * @example
 * {
 *   'basic-agent': '基础代理',
 *   'rag': 'RAG 检索',
 *   'workflow': '学习工作流'
 * }
 */

/**
 * Props验证工具函数
 * @param {*} value - 待验证值
 * @param {string} typeName - 期望类型名称
 * @param {boolean} [required=false] - 是否必填
 * @returns {boolean} 验证结果
 */
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

/**
 * 验证消息对象结构
 * @param {ChatMessage} msg - 消息对象
 * @returns {boolean} 是否有效
 */
export function validateMessage(msg) {
  if (!msg || typeof msg !== 'object') return false
  if (!msg.id || typeof msg.id !== 'string') return false
  if (!Object.values(MessageRole).includes(msg.role)) return false
  if (msg.content === undefined || typeof msg.content !== 'string') return false
  return true
}
