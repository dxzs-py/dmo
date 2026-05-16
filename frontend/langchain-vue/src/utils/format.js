export function cn(...classes) {
  return classes.filter(Boolean).join(' ')
}

export function formatBytes(bytes, decimals = 2) {
  if (bytes === 0) return '0 Bytes'
  const k = 1024
  const dm = decimals < 0 ? 0 : decimals
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i]
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
    'basic-agent': '基础代理',
    'deep-thinking': '深度思考',
    'rag': 'RAG 检索',
    'workflow': '学习工作流',
    'deep-research': '深度研究',
    'guarded': '安全代理',
  }
  return labels[mode] || mode
}
