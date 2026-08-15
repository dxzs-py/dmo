/**
 * 图层正文内联切段纯函数（Agent 图层嵌套规范 Task 5.2）
 *
 * 将单图层正文（content）按工具调用的 position 切分为内容段与工具段：
 *   [{ type: 'content', text }, { type: 'tool', toolCall }]
 *
 * position 语义（D3）：工具调用触发瞬间该图层已输出 content 长度，首次写入
 * 后永久不变。切段规则：
 * - 工具段按 (position, seq) 升序排列（compareToolCallOrder 同一权威排序）；
 * - position 处插入工具段，前后正文按字符偏移切分；
 * - 同一 position 多个工具连续插入（不重复插入空正文）；
 * - 无 position（历史消息 / 未注入）的工具追加末尾；
 * - position 超界（> content 长度）按末尾处理；
 * - 乱序/重复 position 幂等跳过。
 *
 * 本模块为纯函数，无 Vue 依赖，可独立单元测试：
 *   node src/utils/__tests__/inlineContent.test.js
 */
import { compareToolCallOrder, getToolCallId } from './agentLayerTree.js'

/**
 * 将单图层正文按工具 position 切段
 *
 * @param {string} [content] - 该图层正文
 * @param {Array} [toolCalls] - 该图层工具数组（任意顺序，内部排序）
 * @returns {Array<{type: string, text?: string, toolCall?: Object}>} 切段结果
 */
export function splitContentByToolPositions(content, toolCalls) {
  const text = content || ''
  const allTools = Array.isArray(toolCalls) ? toolCalls : []
  if (allTools.length === 0) {
    // 无工具：纯正文单段（空正文也返回空数组，避免空段渲染）
    return text ? [{ type: 'content', text }] : []
  }

  const sorted = [...allTools].sort(compareToolCallOrder)
  const segments = []
  let cursor = 0

  // 有 position 的工具按序插入
  for (const tc of sorted) {
    const pos = tc.position
    if (typeof pos !== 'number') continue // 无 position 末尾统一处理
    // 乱序/重复保护：position 已在游标前则跳过（幂等，不重复插入）
    if (pos < cursor) continue
    // 超界保护：position 超过正文长度按末尾处理（不切分，工具卡放尾部）
    if (pos > text.length) continue
    if (pos > cursor) {
      segments.push({ type: 'content', text: text.slice(cursor, pos) })
    }
    segments.push({ type: 'tool', toolCall: tc })
    cursor = pos
  }

  // 收尾正文（position 之后的剩余内容）
  if (cursor < text.length) {
    segments.push({ type: 'content', text: text.slice(cursor) })
  }

  // 无 position / 超界工具追加末尾（保持排序后的相对顺序）
  for (const tc of sorted) {
    const pos = tc.position
    if (typeof pos !== 'number' || pos > text.length) {
      segments.push({ type: 'tool', toolCall: tc })
    }
  }

  return segments
}

// getToolCallId re-export（供调用方统一从段内 toolCall 取 id，避免重复实现）
export { getToolCallId }
