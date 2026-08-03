export function cn(...classes) {
  return classes.filter(Boolean).join(' ')
}

export function formatFileSize(bytes) {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i]
}

export function formatDuration(seconds) {
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) {
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}m ${secs}s`
  }
  const hours = Math.floor(seconds / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  return `${hours}h ${mins}m`
}

export function formatDate(date) {
  if (!date) return '-'
  if (typeof date === 'string') date = new Date(date)
  return date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
}

export function formatTimestamp(date) {
  if (typeof date === 'string') date = new Date(date)
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

export function truncateText(text, maxLength = 50) {
  if (!text) return ''
  if (text.length <= maxLength) return text
  return text.slice(0, maxLength) + '...'
}

export function truncateMiddle(str, maxLength = 50) {
  if (!str || str.length <= maxLength) return str
  const charsToShow = maxLength - 3
  const prefixLength = Math.ceil(charsToShow / 2)
  const suffixLength = Math.floor(charsToShow / 2)
  return str.substring(0, prefixLength) + '...' + str.substring(str.length - suffixLength)
}

export function getModeLabel(mode) {
  const labels = {
    'agent': '代理',
    'deep-research': '深度研究',
    // 旧模式兼容映射
    'chat': '代理',
    'basic-agent': '代理',
    'advanced-agent': '代理',
    'research-agent': '代理',
    'rag-agent': '代理',
    'deep-thinking': '代理',
  }
  return labels[mode] || mode
}

/**
 * 安全读取 route.query 中的 snake_case 参数（URL 协议标识符例外隔离点）
 * 命名边界说明：URL query 参数名由后端控制（snake_case），
 * 前端通过此函数集中访问，其余代码不得直接使用 route.query.snake_case_key。
 *
 * @param {import('vue-router').RouteLocationNormalizedLoaded} route
 * @param {string} key - snake_case 查询参数名（如 'session_id', 'task_id'）
 * @returns {string|undefined}
 */
export function getQueryParam(route, key) {
  return route.query[key]
}
