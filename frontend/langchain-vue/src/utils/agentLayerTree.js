/**
 * 工具调用排序 / ID 提取纯函数
 *
 * 供 inlineContent.js 的 splitContentByToolPositions 复用同一权威排序，
 * 避免在多处重复实现工具调用顺序比较逻辑（Agent 图层嵌套规范 Task 5.2）。
 *
 * 输入 toolCalls 每项字段（来自 toolCallHandler.js，WebSocket 事件 toCamelCase 后）：
 *   - id / toolCallId：工具调用唯一 ID（= LLM tool_call.id）
 *   - position：工具在该图层正文中的字符偏移（可选，历史消息缺失）
 *   - seq：模块内全局递增序号（排序兜底）
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

/**
 * 图层内工具排序比较函数（与 inlineContent.js 共用同一权威排序）
 *
 * 排序键：position（工具在该图层自身正文中的字符偏移）升序优先；
 * 同 position 按 seq（模块内全局递增序号）升序；两者皆缺时保持原顺序
 * （Array.prototype.sort 稳定，ES2019+）。
 *
 * 无 position（历史消息 / 前端未注入）排末尾（Number.POSITIVE_INFINITY）。
 *
 * @param {Object} a
 * @param {Object} b
 * @returns {number}
 */
export function compareToolCallOrder(a, b) {
  const pa = typeof a?.position === 'number' ? a.position : Number.POSITIVE_INFINITY
  const pb = typeof b?.position === 'number' ? b.position : Number.POSITIVE_INFINITY
  if (pa !== pb) return pa - pb
  const sa = typeof a?.seq === 'number' ? a.seq : Number.MAX_SAFE_INTEGER
  const sb = typeof b?.seq === 'number' ? b.seq : Number.MAX_SAFE_INTEGER
  if (sa !== sb) return sa - sb
  return 0
}
