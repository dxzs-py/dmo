/**
 * 引用角标（inline citation）属性安全工具——MarkdownRenderer 渲染管线专用。
 *
 * 背景：MarkdownRenderer 用正则将正文中的 [n] 替换为 <sup><a href="…" title="…">，
 * href/title 来自后端返回的 citations 数组（外部数据），以字符串拼接进 HTML 属性位。
 * 拼接后的整体 HTML 虽会再过 DOMPurify（可剥离 on* 事件属性与危险 URL），
 * 但属性值含引号时会在净化前破坏属性边界、产生非预期 DOM 结构，且"依赖下游
 * 净化兜底"属隐式契约。本模块在拼接前显式做白名单过滤与属性转义，
 * 保证进入 DOMPurify 前的 HTML 语义即正确。
 */

const HTML_ATTR_ESCAPE_MAP = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}

/**
 * HTML 属性值转义（& < > " '），用于 title 等文本型属性位。
 *
 * @param {unknown} value - 原始属性值
 * @returns {string} 转义后的安全文本（可直接拼入双引号属性位）
 */
export function escapeHtmlAttr(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => HTML_ATTR_ESCAPE_MAP[ch])
}

/**
 * 引用 href 白名单过滤：仅放行 ① http/https 绝对 URL ② / 开头的同源相对路径
 * ③ # 开头的页内锚点。协议相对地址（//host，跨源）、javascript:/data:/vbscript:
 * 等危险 scheme、空值一律返回空串，调用方据此降级为不带链接的 <sup> 角标。
 *
 * @param {unknown} href - 原始引用链接（来自后端 citations，可能为任意值）
 * @returns {string} 可直接拼入 href 属性位的安全值（已做属性转义）；不可用时为空串
 */
export function sanitizeCitationHref(href) {
  if (typeof href !== 'string') return ''
  const trimmed = href.trim()
  if (!trimmed || trimmed.startsWith('//')) return ''
  const isAllowed = trimmed.startsWith('#')
    || trimmed.startsWith('/')
    || /^https?:\/\//i.test(trimmed)
  return isAllowed ? escapeHtmlAttr(trimmed) : ''
}
