import { logger } from '../utils/logger'

const DEFAULT_API_BASE_URL = '/api/v1'
const API_BASE_URL_STORAGE_KEY = 'dx-study-agent-api-url'

// 同源相对路径仅放行此前缀（与 vite dev proxy 的 /api 及后端统一挂载前缀一致）
const RELATIVE_API_BASE_URL_PREFIX = '/api'

/**
 * 显式放行的绝对 API BaseURL 域名白名单（host 含端口、小写精确匹配）。
 * 默认为空 = 仅允许同源相对路径；前端与后端分域部署时在此登记（如 ['api.example.com']）。
 * 数组保持可变以便测试临时注入，业务代码不应在运行期修改。
 */
export const ALLOWED_API_BASE_HOSTS = []

/**
 * API BaseURL 白名单校验——localStorage 读取与 axios updateBaseURL 写入的唯一判定口径：
 * 仅允许 ① RELATIVE_API_BASE_URL_PREFIX 开头的同源相对路径
 *      ② http/https 绝对 URL 且 host 命中 ALLOWED_API_BASE_HOSTS。
 * 协议相对地址（//host，跨源）、javascript:/data: 等危险 scheme、未登记域名一律拒绝。
 *
 * @param {unknown} value - 待校验值
 * @returns {boolean} 是否可作为 API BaseURL
 */
export function isAllowedApiBaseUrl(value) {
  if (typeof value !== 'string') return false
  const trimmed = value.trim()
  if (!trimmed || trimmed.startsWith('//')) return false
  if (trimmed.startsWith(RELATIVE_API_BASE_URL_PREFIX)) return true
  try {
    const url = new URL(trimmed)
    return (
      (url.protocol === 'https:' || url.protocol === 'http:')
      && ALLOWED_API_BASE_HOSTS.includes(url.host.toLowerCase())
    )
  } catch {
    return false
  }
}

// 解析缓存：以原始存储值为键，同一非法值仅首次告警一次，避免 getter 高频访问刷屏
let resolvedBaseUrl = { raw: null, value: DEFAULT_API_BASE_URL }

const getApiBaseUrl = () => {
  if (typeof window === 'undefined') return DEFAULT_API_BASE_URL
  const saved = window.localStorage.getItem(API_BASE_URL_STORAGE_KEY)
  if (!saved) return DEFAULT_API_BASE_URL
  if (saved === resolvedBaseUrl.raw) return resolvedBaseUrl.value
  if (isAllowedApiBaseUrl(saved)) {
    resolvedBaseUrl = { raw: saved, value: saved.trim() }
  } else {
    logger.warn(
      `[settings] localStorage 中 API BaseURL 不在白名单内，已回退默认值: ${JSON.stringify(saved)}`
    )
    resolvedBaseUrl = { raw: saved, value: DEFAULT_API_BASE_URL }
  }
  return resolvedBaseUrl.value
}

const settings = {
  host: '',
  get apiBaseUrl() {
    return getApiBaseUrl()
  },
  set apiBaseUrl(value) {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(API_BASE_URL_STORAGE_KEY, value)
    }
  },

  homePage: {
    redirectToChat: false,
    enableQuickStart: true
  },

  apiValidation: {
    // 用户输入框单条消息上限（streamMessage 请求体 message 字段）
    messageMaxLength: 10000,
    // 后端持久化的历史消息内容上限（聊天历史校验 / updateMessage 截断），
    // 与用户输入口径不同：历史条目可能包含流式累积的长回复，放宽至 50000
    historyMessageMaxLength: 50000,
    chatHistoryMaxItems: 50,
    sessionIdPattern: /^[a-f0-9-]{36}$/,
    allowedModes: ['agent', 'deep-research'],
  },

  /**
   * 子代理「等待你的确认」卡片自动展开开关（默认关闭）。
   * 开启时：仅状态 = 等待你的确认 的卡片自动展开，其余保持摘要；嵌套内层永不自动展开。
   */
  autoExpandPendingConfirm: false
}

export default settings
