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

/**
 * 格式化为完整日期时间（zh-CN 本地化，含日期与时分秒）
 * @param {string|Date|null} dateStr - 日期字符串或 Date 对象
 * @returns {string} 形如 '2026/10/02 14:30:00'，空值返回 '-'
 */
export function formatDateTime(dateStr) {
  if (!dateStr) return '-'
  return new Date(dateStr).toLocaleString('zh-CN')
}

/**
 * 自动选择单位（B/KB/MB/GB）格式化字节数
 * 口径：0 或 falsy 返回 '0 B'；B 级取整，其余单位保留 2 位小数
 * @param {number|null} bytes - 字节数
 * @returns {string} 形如 '1.50 MB'
 */
export function formatSizeAuto(bytes) {
  if (!bytes || bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let size = bytes
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024
    i++
  }
  return `${size.toFixed(i === 0 ? 0 : 2)} ${units[i]}`
}

/**
 * 以固定 MB 单位格式化字节数
 * @param {number} bytes - 字节数
 * @returns {string} 形如 '10.25 MB'
 */
export function formatMB(bytes) {
  if (!bytes || isNaN(bytes)) return '0 MB'
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

/**
 * 以固定 GB 单位格式化字节数
 * @param {number} bytes - 字节数
 * @returns {string} 形如 '1.5 GB'
 */
export function formatGB(bytes) {
  if (!bytes || isNaN(bytes)) return '0 GB'
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`
}

/**
 * 按文件类型后缀返回图标字符
 * @param {string} type - 文件类型/后缀名
 * @returns {string} emoji 图标，未知类型返回 '📎'
 */
export function getFileIcon(type) {
  const iconMap = {
    pdf: '📄', doc: '📝', docx: '📝', txt: '📃',
    png: '🖼️', jpg: '🖼️', jpeg: '🖼️', gif: '🖼️', webp: '🖼️', svg: '🖼️',
    xls: '📊', xlsx: '📊', csv: '📊',
    py: '🐍', js: '📜', ts: '📜', html: '🌐', css: '🎨',
    json: '📋', xml: '📋', md: '📝',
  }
  return iconMap[type] || '📎'
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
