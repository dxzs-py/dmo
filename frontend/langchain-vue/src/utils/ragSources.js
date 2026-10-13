/**
 * RAG 来源展示格式化（C-4 前端适配）
 *
 * 后端 /knowledge/query/（REST）与 /knowledge/query/stream/（SSE sources 事件）
 * 的来源已统一为结构化对象：{ content, source(显示名), metadata, [degraded] }。
 * C-7 metadata 白名单后不再含服务器路径，显示名取顶层 source 字段；
 * REST 链修复前 sources 元素是 dict repr 字符串（`source.content || source`
 * 兜底渲染整段 repr 文本），结构化契约下该兜底不再需要。
 */

/**
 * 格式化来源折叠面板标题。
 * @param {{ source?: string }} [source] 结构化来源对象
 * @param {number} index 来源序号（0 起）
 * @returns {string}
 */
export function formatSourceTitle(source, index) {
  if (source && source.source) {
    return `来源 ${index + 1}: ${source.source}`
  }
  return `来源 ${index + 1}`
}
