/**
 * 图层正文内联切段纯函数（Agent 图层嵌套规范 Task 5.2）
 *
 * 将单图层正文（content）按工具调用的 position 切分为内容段与工具段：
 *   [{ type: 'content', text }, { type: 'tool', toolCall }]
 *
 * position 语义（D3）：工具调用触发瞬间该图层已输出 content 长度，首次写入
 * 后永久不变。切段规则：
 * - 顺序基准 = 传入数组顺序（调用方已由 sortToolCallsForDisplay 按 seq 排序，
 *   seq 是唯一排序权威）；position 仅用于内联切段定位，不参与排序；
 * - 有 position 且不超界的工具在对应字符偏移处插入，前后正文按偏移切分；
 * - 同一 position 多个工具连续插入（不重复插入空正文）；
 * - 无 position（历史消息 / 未注入）或超界（> content 长度）的工具追加末尾，
 *   追加顺序 = 传入数组顺序（即 seq 顺序）；
 * - 乱序/重复 position 幂等跳过（追加末尾，不重复插入）。
 *
 * 历史：曾用 compareToolCallOrder（position 优先）重排，与 seq 排序权威
 * （sortToolCallsForDisplay）双排序冲突，position 缺失时工具卡乱序
 * （如 wait_for_subagent 跑到 spawn 之前）。排序基准统一为传入顺序后根除。
 *
 * 本模块为纯函数，无 Vue 依赖，可独立单元测试：
 *   node src/utils/__tests__/inlineContent.test.js
 */
import { sortToolCallsForDisplay } from './messageOperations.js'

/**
 * 提取工具调用 ID（兼容 id / toolCallId 双字段）
 *
 * 供 splitContentByToolPositions 复用（原 agentLayerTree.js 单函数模块已并入本文件，
 * spec unify-agent-research-display-architecture Task 5.7）。
 *
 * @param {Object} tc
 * @returns {string}
 */
export function getToolCallId(tc) {
  if (!tc || typeof tc !== 'object') return ''
  return tc.id || tc.toolCallId || ''
}

/**
 * 过滤主 agent 工具调用并统一排序（spec unify-agent-research-display-architecture）
 *
 * 收敛 ChatMessage / ResearchTaskDetail 两处 mainToolCalls 的重复实现，
 * 消除排序职责不一致（原 ChatMessage 不排序 / ResearchTaskDetail 排序）。
 * 排序统一走 sortToolCallsForDisplay（seq 升序，唯一权威排序实现）。
 *
 * @param {Array} [toolCalls] - 平铺工具调用数组（含主/子代理）
 * @returns {Array} 主 agent 工具调用数组（已按 seq 排序）
 */
export function filterMainToolCalls(toolCalls) {
  const list = (Array.isArray(toolCalls) ? toolCalls : []).filter(tc => !tc.subagentThreadId)
  sortToolCallsForDisplay(list)
  return list
}

/**
 * 将单图层正文按工具 position 切段
 *
 * @param {string} [content] - 该图层正文
 * @param {Array} [toolCalls] - 该图层工具数组（须已按 seq 排序）
 * @returns {Array<{type: string, text?: string, toolCall?: Object}>} 切段结果
 */
export function splitContentByToolPositions(content, toolCalls) {
  const text = content || ''
  const allTools = Array.isArray(toolCalls) ? toolCalls : []
  if (allTools.length === 0) {
    // 无工具：纯正文单段（空正文也返回空数组，避免空段渲染）
    return text ? [{ type: 'content', text }] : []
  }

  const segments = []
  const appendTools = []
  let cursor = 0

  // 按传入顺序（已按 seq 排序）遍历；position 仅做内联定位，不参与排序
  for (const tc of allTools) {
    const pos = tc.position
    if (typeof pos !== 'number' || pos > text.length || pos < cursor) {
      // 无 position / 超界 / 乱序重复：追加末尾（保持传入顺序）
      appendTools.push(tc)
      continue
    }
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

  // 无 position / 超界 / 乱序工具追加末尾（保持传入顺序）
  for (const tc of appendTools) {
    segments.push({ type: 'tool', toolCall: tc })
  }

  return segments
}
