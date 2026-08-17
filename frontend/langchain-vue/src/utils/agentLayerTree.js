/**
 * 工具调用 ID 提取纯函数
 *
 * 供 inlineContent.js 的 splitContentByToolPositions 复用，避免在多处重复实现
 * 工具调用 ID 提取逻辑（Agent 图层嵌套规范 Task 5.2）。
 *
 * 输入 toolCalls 每项字段（来自 toolCallHandler.js，WebSocket 事件 toCamelCase 后）：
 *   - id / toolCallId：工具调用唯一 ID（= LLM tool_call.id）
 *
 * 工具调用顺序统一由 sortToolCallsForDisplay（utils/messageOperations.js，seq 升序）
 * 决定，本模块不再承担排序职责。
 *
 * 本模块为纯函数，无 Vue 依赖。
 */

/**
 * 提取工具调用 ID（兼容 id / toolCallId 双字段）
 * @param {Object} tc
 * @returns {string}
 */
export function getToolCallId(tc) {
  if (!tc || typeof tc !== 'object') return ''
  return tc.id || tc.toolCallId || ''
}
