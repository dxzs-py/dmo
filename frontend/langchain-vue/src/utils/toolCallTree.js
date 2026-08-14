/**
 * 工具调用树构建纯函数（Task 6：子代理递归嵌套式容器树，参考 3.md）
 *
 * 深度研究任务工具调用（toolCalls）按「子代理分组卡片 + 组内递归工具树」渲染：
 * - 外层：按 agentPath 分组（每个唯一链路 = 一个分组卡片，含聚合统计/整体状态）
 * - 内层：组内按 parentToolCallId 递归建树（工具嵌套调用关系）
 * - 子代理：触发它的父工具节点下内嵌该子代理的分组卡片
 *
 * 输入 toolCalls 每项字段（来自 toolCallHandler.js，WebSocket 事件 toCamelCase 后）：
 *   - id / toolCallId：工具调用唯一 ID（= LLM tool_call.id）
 *   - name：工具名
 *   - status：pending/waiting/running/completed/failed/rejected/timeout
 *   - parentToolCallId：父工具调用 ID（子 agent 内工具指向触发它的 task 工具）
 *   - agentPath：完整调用链路（如 ["main", "web-researcher"]，缺失时归入 ["main"]）
 *   - agentName / depth：子 agent 信息（仅子 agent 内工具携带）
 *   - createdAt / completedAt：起始/终态 ISO 时间戳（后端事件透传，组头总耗时数据源）
 *   - description：子代理任务目标描述（仅子代理工具事件携带）
 *
 * 本模块为纯函数，无 Vue 依赖，可独立单元测试。
 */

/** 工具终态集合（用于分组整体状态判定） */
const TOOL_TERMINAL = new Set(['completed', 'failed', 'rejected', 'timeout'])

/** 工具状态优先级：数值越小越优先（分组整体状态取最高优先级） */
const STATUS_PRIORITY = {
  running: 0,
  waiting: 1,
  pending: 2,
  failed: 3,
  rejected: 4,
  timeout: 5,
  completed: 6,
}

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
 * 提取工具 agentPath（缺失/非法时归入 ["main"] 根组）
 * @param {Object} tc
 * @returns {string[]}
 */
export function getAgentPath(tc) {
  if (tc && Array.isArray(tc.agentPath) && tc.agentPath.length > 0) {
    return tc.agentPath.map((p) => String(p))
  }
  return ['main']
}

/**
 * 计算组内工具总耗时（毫秒）：min(createdAt) 与 max(completedAt) 之差
 *
 * createdAt / completedAt 为 ISO 8601 时间戳字符串（后端事件透传，
 * toolCallHandler 持久化，sessionTransformers toCamelCase 后）。
 * 任一时间戳缺失（如组内工具尚未全部进入终态）或差值非法时返回 null。
 *
 * @param {Array<Object>} toolCalls
 * @returns {number|null} 总耗时毫秒数；无法计算时返回 null
 */
export function computeGroupDurationMs(toolCalls) {
  if (!Array.isArray(toolCalls) || toolCalls.length === 0) return null
  let minStart = Infinity
  let maxEnd = -Infinity
  for (const tc of toolCalls) {
    const start = tc.createdAt ? new Date(tc.createdAt).getTime() : NaN
    const end = tc.completedAt ? new Date(tc.completedAt).getTime() : NaN
    if (!Number.isNaN(start) && start < minStart) minStart = start
    if (!Number.isNaN(end) && end > maxEnd) maxEnd = end
  }
  if (minStart === Infinity || maxEnd === -Infinity || maxEnd < minStart) return null
  return maxEnd - minStart
}

/**
 * 格式化耗时字符串：不足 1s 显示 "<1s"，否则保留 1 位小数（如 "12.3s"）
 *
 * @param {number|null} ms - 毫秒数
 * @returns {string|null} 格式化结果；非法输入返回 null
 */
export function formatDuration(ms) {
  if (typeof ms !== 'number' || Number.isNaN(ms) || ms < 0) return null
  if (ms < 1000) return '<1s'
  return `${(ms / 1000).toFixed(1)}s`
}

/**
 * 计算工具调用统计
 * @param {Array<Object>} toolCalls
 * @returns {{ total: number, completed: number, failed: number, running: number, pending: number, durationMs: number|null }}
 */
export function computeGroupStats(toolCalls) {
  const stats = { total: toolCalls.length, completed: 0, failed: 0, running: 0, pending: 0 }
  for (const tc of toolCalls) {
    const status = tc.status || 'pending'
    if (status === 'completed') stats.completed += 1
    else if (status === 'running') stats.running += 1
    else if (status === 'failed' || status === 'rejected' || status === 'timeout') stats.failed += 1
    else stats.pending += 1
  }
  // 组头总耗时（Task 4.1）：min(createdAt) 与 max(completedAt) 之差，无法计算时为 null
  stats.durationMs = computeGroupDurationMs(toolCalls)
  return stats
}

/**
 * 计算分组整体状态（优先级：running > waiting > pending > failed > rejected > timeout > completed；
 * 失败类状态优先于 completed，避免掩盖组内异常）
 * @param {Array<Object>} toolCalls
 * @returns {string}
 */
export function computeGroupStatus(toolCalls) {
  if (!Array.isArray(toolCalls) || toolCalls.length === 0) return 'pending'
  let best = 'completed'
  for (const tc of toolCalls) {
    const status = tc.status || 'pending'
    const p = STATUS_PRIORITY[status]
    const bestP = STATUS_PRIORITY[best]
    if (p !== undefined && p < bestP) best = status
  }
  return best
}

/**
 * 按 agentPath 分组（保持工具首次出现顺序，稳定性排序）
 *
 * @param {Array<Object>} toolCalls
 * @returns {Array<{
 *   key: string,
 *   agentPath: string[],
 *   agentName: string,
 *   description: string,
 *   toolCalls: Array<Object>,
 *   stats: Object,
 *   status: string,
 * }>}
 */
export function buildAgentGroups(toolCalls) {
  if (!Array.isArray(toolCalls)) return []
  const order = []
  const groups = new Map()
  for (const tc of toolCalls) {
    const path = getAgentPath(tc)
    const key = path.join('>')
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        agentPath: path,
        agentName: path[path.length - 1] || 'main',
        toolCalls: [],
      })
      order.push(key)
    }
    groups.get(key).toolCalls.push(tc)
  }
  return order.map((key) => {
    const group = groups.get(key)
    return {
      ...group,
      // 子代理任务目标描述（Task 4.5）：组级共享，取组内首个携带 description 的工具值
      description: group.toolCalls.find((tc) => tc.description)?.description || '',
      stats: computeGroupStats(group.toolCalls),
      status: computeGroupStatus(group.toolCalls),
    }
  })
}

/**
 * 组内按 parentToolCallId 递归建树
 *
 * 返回根节点数组；每个节点为原工具调用 + `children`（直接子工具调用）。
 * 孤儿节点（parentToolCallId 无对应工具/指向自身）提升为根，避免丢失。
 *
 * @param {Array<Object>} toolCalls
 * @returns {Array<Object>} 根节点数组（含 children 字段）
 */
export function buildToolTree(toolCalls) {
  if (!Array.isArray(toolCalls)) return []
  const byId = new Map()
  const roots = []
  for (const tc of toolCalls) {
    const id = getToolCallId(tc)
    if (!id) {
      // 无 ID 工具无法建立父子关系，直接作为根节点
      roots.push({ ...tc, children: [] })
      continue
    }
    byId.set(id, { ...tc, children: [] })
  }
  for (const node of byId.values()) {
    const parentId = node.parentToolCallId || ''
    const parent = parentId ? byId.get(parentId) : null
    if (parent && parent !== node) {
      parent.children.push(node)
    } else {
      roots.push(node)
    }
  }
  return roots
}

/**
 * 过滤出顶层根组（3.md：根主代理生成一级外层分组卡片）
 *
 * 子代理组（组内任一工具的 parentToolCallId 指向组外工具）由父工具节点
 * 内嵌展示（见 findChildGroupsByTool），不应重复出现在顶层列表。
 *
 * @param {Array<Object>} toolCalls
 * @returns {Array<Object>} 根分组数组
 */
export function buildRootGroups(toolCalls) {
  const groups = buildAgentGroups(toolCalls)
  const embeddedKeys = new Set()
  for (const g of groups) {
    const ids = new Set(g.toolCalls.map((tc) => getToolCallId(tc)))
    if (g.toolCalls.some((tc) => tc.parentToolCallId && !ids.has(tc.parentToolCallId))) {
      embeddedKeys.add(g.key)
    }
  }
  return groups.filter((g) => !embeddedKeys.has(g.key))
}

/**
 * 查找触发工具 X（toolId）所启动的子代理分组
 *
 * 子代理组 = 组内任一工具的 parentToolCallId === toolId。
 * 用于在父工具节点下内嵌子代理分组卡片（3.md："工具触发新子 Agent 时，
 * 在该工具节点内部内嵌独立子代理卡片"）。
 *
 * @param {Array<Object>} groups - buildAgentGroups 返回值
 * @param {string} toolId - 父工具调用 ID
 * @returns {Array<Object>}
 */
export function findChildGroupsByTool(groups, toolId) {
  if (!Array.isArray(groups) || !toolId) return []
  return groups.filter((g) =>
    g.toolCalls.some((tc) => (tc.parentToolCallId || '') === toolId)
  )
}

/**
 * 判断工具是否为终态（供折叠规则使用）
 * @param {string} status
 * @returns {boolean}
 */
export function isTerminalToolStatus(status) {
  return TOOL_TERMINAL.has(status)
}
