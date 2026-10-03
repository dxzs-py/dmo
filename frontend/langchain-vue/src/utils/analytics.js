/**
 * 轻量行为埋点工具（fire-and-forget）
 *
 * 设计意图：
 * - trackFeatureUse / trackEvent 均为「发出即忘」：不返回 Promise、不 await、
 *   内部捕获全部异常并静默（仅 console.debug），任何失败绝不影响业务主流程。
 * - 刻意不复用 apiClient：apiClient 响应拦截器对 401 会触发 token 刷新，
 *   刷新失败将 forceLogout 跳转登录页——埋点请求若走该通道，token 过期时
 *   埋点本身可能成为触发登出的请求，违背「埋点失败不影响主流程」原则；
 *   且 apiClient 每次请求会驱动全局 loading 计数。故此处用裸 axios 直连并
 *   手动附带 token；无 token（未登录）时直接跳过上报，避免 401 刷屏。
 * - 请求体发送前经 toSnakeCase 转换（sessionTransformers 为唯一命名边界），
 *   源码内保持 camelCase 键名，不出现网络边界外的 snake_case。
 */
import axios from 'axios'
import settings from '../config/settings'
import { useUserStore } from '@/stores/user'
import { toSnakeCase } from '@/utils/sessionTransformers'
import { logger } from '@/utils/logger'

const TRACK_TIMEOUT_MS = 10000

/**
 * 发送埋点请求（fire-and-forget 内核）
 * @param {string} url - 相对 apiBaseUrl 的端点路径
 * @param {Object} payload - camelCase 请求体（发送前统一转 snake_case）
 */
function sendTrackReport(url, payload) {
  try {
    const userStore = useUserStore()
    if (!userStore.token) return
    axios.post(`${settings.apiBaseUrl}${url}`, toSnakeCase(payload), {
      headers: { Authorization: `Bearer ${userStore.token}` },
      timeout: TRACK_TIMEOUT_MS,
    }).catch((error) => {
      logger.debug('[Analytics] 埋点上报失败（已静默）:', error?.message)
    })
  } catch (error) {
    logger.debug('[Analytics] 埋点上报异常（已静默）:', error?.message)
  }
}

/**
 * 上报功能使用（页面维度，router afterEach 调用）
 * @param {string} feature - 功能标识（route.name 或路径）
 * @param {Object} [meta] - 附加元数据
 */
export function trackFeatureUse(feature, meta = {}) {
  if (!feature) return
  sendTrackReport('/analytics/track/feature-use/', { feature, metadata: meta })
}

/**
 * 上报关键用户事件（会话创建、跨库搜索等，≤3 个接入点）
 * @param {string} event - 事件类型标识
 * @param {Object} [meta] - 事件元数据（透传至后端 metadata DictField）
 * @param {Object} [options] - 可选项
 * @param {string} [options.category='system'] - 事件分类（后端 EventCategory 枚举值）
 */
export function trackEvent(event, meta = {}, options = {}) {
  if (!event) return
  sendTrackReport('/analytics/track/event/', {
    eventType: event,
    eventCategory: options.category || 'system',
    metadata: meta,
  })
}
