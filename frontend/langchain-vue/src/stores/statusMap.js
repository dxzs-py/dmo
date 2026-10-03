import { ref } from 'vue'
import { logger } from '@/utils/logger'

/**
 * 任务/工作流状态终态集合（不可被非终态覆盖）。
 *
 * research.js 与 workflow.js 原各自定义同值 Set（TASK_TERMINAL_STATUSES /
 * WORKFLOW_TERMINAL_STATUSES），spec D10 收敛为单源。
 */
export const TERMINAL_PROTECTED_STATUSES = new Set(['completed', 'failed', 'cancelled'])

/**
 * 创建终态保护的响应式状态 Map 操作集（spec D10）
 *
 * 收敛 research.js setTaskStatus 与 workflow.js setWorkflowState 的近逐字双实现：
 * 合并式更新 + 终态保护规则（终态是正确性关键逻辑，双实现漂移代价高）。
 *
 * 状态优先级保护规则：
 * - 旧 status 是终态 + 新 status 是非终态 → 保留旧 status（防止滞后快照覆盖）
 * - 新 status 是终态 → 直接覆盖（终态是权威）
 * - 其他情况 → 用新 status 覆盖
 *
 * 与 messageOperations.js 中 STATUS_PRIORITY 对 toolCall.status 的保护机制对齐。
 *
 * @param {Object} options
 * @param {string} options.logTag - 日志前缀（如 'Research' / 'Workflow'）
 * @param {string} options.actionName - 日志中的方法名（如 'setTaskStatus'）
 * @param {string} [options.firstSetUnknownFallback] - 首次设置日志中 status 为 falsy 时的
 *   回退文案（workflow 原实现为 'unknown'；research 原实现无回退，不传即保持原样）
 * @returns {{
 *   map: import('vue').Ref<Map<string, import('vue').Ref<Object|null>>>,
 *   ensure: (key: string) => import('vue').Ref<Object|null>|null,
 *   get: (key: string) => Object|null,
 *   set: (key: string, fresh: Object, options?: { force?: boolean }) => Object|null,
 * }}
 */
export function createTerminalProtectedStatusMap({ logTag, actionName, firstSetUnknownFallback } = {}) {
  /** 状态 Map（按状态键索引，值为 ref(Object|null)） */
  const map = ref(new Map())

  /**
   * 获取或创建指定键的状态 ref
   * @param {string} key - 状态键（taskId / threadId）
   * @returns {import('vue').Ref<Object|null>|null} 状态 ref（key 无效时返回 null）
   */
  const ensure = (key) => {
    if (!key) return null
    if (!map.value.has(key)) {
      map.value.set(key, ref(null))
    }
    return map.value.get(key)
  }

  /**
   * 读取指定键的状态（响应式，建议在 computed 中调用）
   * @param {string} key - 状态键
   * @returns {Object|null} 状态对象（key 无效或未设置时返回 null）
   */
  const get = (key) => {
    if (!key) return null
    const stateRef = map.value.get(key)
    return stateRef?.value || null
  }

  /**
   * 合并式更新指定键的状态（终态保护规则见工厂 JSDoc）
   *
   * @param {string} key - 状态键
   * @param {Object} fresh - 新的状态数据（部分字段即可，会与现有数据合并）
   * @param {Object} [options]
   * @param {boolean} [options.force=false] - 强制覆盖（跳过状态优先级保护）
   * @returns {Object|null} 更新后的状态（入参无效时返回 null）
   */
  const set = (key, fresh, options = {}) => {
    if (!key || !fresh || typeof fresh !== 'object') return null
    const stateRef = ensure(key)
    if (!stateRef) return null

    const current = stateRef.value
    const force = options.force === true

    // 首次设置：直接写入
    if (!current) {
      stateRef.value = { ...fresh }
      const statusText = firstSetUnknownFallback !== undefined
        ? (fresh.status || firstSetUnknownFallback)
        : fresh.status
      logger.info(`[${logTag}] ${actionName} 首次设置: taskId=${key}, status=${statusText}`)
      return stateRef.value
    }

    // 状态优先级保护（非 force 模式）
    const newStatus = fresh.status
    const oldStatus = current.status
    if (!force && newStatus && oldStatus
        && TERMINAL_PROTECTED_STATUSES.has(oldStatus)
        && !TERMINAL_PROTECTED_STATUSES.has(newStatus)) {
      logger.warn(
        `[${logTag}] ${actionName} 状态保护: taskId=${key}, ` +
        `旧 status=${oldStatus}(终态) 不被新 status=${newStatus}(非终态) 覆盖`
      )
      // 保留旧 status，但其他字段可以合并
      const otherFields = { ...fresh }
      delete otherFields.status
      stateRef.value = { ...current, ...otherFields, status: oldStatus }
      return stateRef.value
    }

    // 正常合并更新
    stateRef.value = { ...current, ...fresh }
    if (newStatus && newStatus !== oldStatus) {
      logger.info(
        `[${logTag}] ${actionName} 状态变更: taskId=${key}, ` +
        `oldStatus=${oldStatus}, newStatus=${newStatus}`
      )
    }
    return stateRef.value
  }

  return { map, ensure, get, set }
}
