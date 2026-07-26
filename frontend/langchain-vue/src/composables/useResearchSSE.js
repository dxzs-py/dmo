import { ref, onScopeDispose, getCurrentScope } from 'vue'
import { useApprovalStore } from '@/stores/approval'
import { useSSEConnection } from '@/composables/useSSEConnection'
import { logger } from '@/utils/logger'

/**
 * 连接状态枚举
 * @readonly
 * @enum {string}
 */
const ResearchConnectionStatus = {
  DISCONNECTED: 'disconnected',
  CONNECTING: 'connecting',
  CONNECTED: 'connected',
  ERROR: 'error',
}

/**
 * 重连配置
 */
const RECONNECT_CONFIG = {
  /** 初始重连延迟(ms) */
  INITIAL_DELAY: 1000,
  /** 最大重连延迟(ms) */
  MAX_DELAY: 10000,
  /** 最大重试次数 */
  MAX_RETRIES: 5,
}

/**
 * 统一深度研究 SSE composable
 *
 * 抽取 ChatView.vue 的 connectResearchSSE 与 DeepResearchView.vue 的 handleSSEEvent 重复逻辑。
 *
 * 基于 useSSEConnection 实现 SSE 连接生命周期管理，
 * 仅处理 approval_history 事件（委托给 approvalStore.restoreFromSSEHistory），
 * 实时审批/工具事件统一通过 WebSocket（task 频道）推送，由 sync.js 处理。
 *
 * 支持自动重连（指数退避），重连失败超过最大次数后触发 fallback 到轮询。
 *
 * 暴露回调：
 * - onApproval(approvalData): 历史审批恢复事件（approvalStore 处理后调用）
 * - onStatus(parsed): status_change 事件
 * - onMessage(parsed): 其他事件（connected/step_update/done/timeout 等）
 * - onError(errorData): error 事件
 * - onFallbackToPolling(): 重连失败超过最大次数后触发，提示上层切换到轮询模式
 */
export function useResearchSSE() {
  const approvalStore = useApprovalStore()
  const sseConnection = useSSEConnection('research-sse')

  /** 是否已连接 */
  const isConnected = ref(false)
  /** 当前连接的任务 ID */
  const currentTaskId = ref(null)
  /** SSE 连接状态 */
  const connectionStatus = ref(ResearchConnectionStatus.DISCONNECTED)

  /** 保存最近一次连接的 options，用于重连时恢复事件监听 */
  let lastOptions = null
  /** 标记是否为主动断开，主动断开不触发重连 */
  let isManualDisconnect = false
  /** 标记任务是否已正常完成（completed/failed），完成后不重连 */
  let isTaskCompleted = false
  /** 当前重连次数 */
  let reconnectAttempts = 0
  /** 重连定时器 */
  let reconnectTimer = null

  /**
   * 计算指数退避延迟
   * @param {number} attempt - 当前重试次数（从0开始）
   * @returns {number} 延迟毫秒数
   */
  function getExponentialBackoffDelay(attempt) {
    const delay = RECONNECT_CONFIG.INITIAL_DELAY * Math.pow(2, attempt)
    return Math.min(delay, RECONNECT_CONFIG.MAX_DELAY)
  }

  /**
   * 清除重连定时器
   */
  function clearReconnectTimer() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  /**
   * 执行重连
   */
  function attemptReconnect() {
    if (isManualDisconnect) {
      logger.info('[useResearchSSE] 主动断开，不执行重连')
      return
    }

    if (isTaskCompleted) {
      logger.info('[useResearchSSE] 任务已正常完成，不执行重连')
      return
    }

    if (reconnectAttempts >= RECONNECT_CONFIG.MAX_RETRIES) {
      logger.warn(`[useResearchSSE] 重连次数已达上限 (${RECONNECT_CONFIG.MAX_RETRIES})，触发 fallback 到轮询`)
      connectionStatus.value = ResearchConnectionStatus.ERROR
      lastOptions?.onFallbackToPolling?.()
      return
    }

    reconnectAttempts++
    const delay = getExponentialBackoffDelay(reconnectAttempts - 1)
    logger.info(`[useResearchSSE] 将在 ${delay}ms 后执行第 ${reconnectAttempts} 次重连`)

    connectionStatus.value = ResearchConnectionStatus.CONNECTING

    reconnectTimer = setTimeout(async () => {
      if (isManualDisconnect) return
      if (isTaskCompleted) return
      if (!currentTaskId.value) return

      try {
        await performConnect(currentTaskId.value, lastOptions || {}, true)
        reconnectAttempts = 0
        logger.info('[useResearchSSE] 重连成功')
      } catch (error) {
        if (error?.name !== 'AbortError') {
          logger.error(`[useResearchSSE] 第 ${reconnectAttempts} 次重连失败`, error)
          attemptReconnect()
        }
      }
    }, delay)
  }

  /**
   * 实际执行连接的内部方法
   * @param {string} taskId - 任务 ID
   * @param {Object} options - 连接选项
   * @param {boolean} [isReconnect=false] - 是否为重连
   * @returns {Promise<void>}
   */
  async function performConnect(taskId, options, isReconnect = false) {
    if (!isReconnect) {
      isManualDisconnect = false
      isTaskCompleted = false
      reconnectAttempts = 0
    }

    connectionStatus.value = ResearchConnectionStatus.CONNECTING

    await sseConnection.connect(
      `/research/${taskId}/stream/`,
      {
        connectionId: `research-${taskId}`,
        fetchOptions: {
          injectTokenQuery: true,
        },
        onEvent: (parsed) => handleEvent(parsed, options),
        onStatusChange: (status, error) => {
          if (isManualDisconnect) return

          switch (status) {
            case 'connected':
              connectionStatus.value = ResearchConnectionStatus.CONNECTED
              isConnected.value = true
              logger.info(`[useResearchSSE] 连接建立成功: taskId=${taskId}${isReconnect ? ' (重连)' : ''}`)
              break
            case 'disconnected':
              connectionStatus.value = ResearchConnectionStatus.DISCONNECTED
              isConnected.value = false
              if (!isTaskCompleted) {
                logger.info('[useResearchSSE] 连接异常断开，准备重连')
                attemptReconnect()
              } else {
                logger.info('[useResearchSSE] 任务完成，连接正常关闭')
              }
              break
            case 'error':
              connectionStatus.value = ResearchConnectionStatus.ERROR
              isConnected.value = false
              logger.error('[useResearchSSE] 连接错误，尝试重连', error)
              attemptReconnect()
              break
          }
        },
      }
    )
  }

  /**
   * 连接深度研究 SSE
   * @param {string} taskId - 深度研究任务 ID
   * @param {Object} [options] - 回调选项
   * @param {Function} [options.onApproval] - 审批事件回调
   * @param {Function} [options.onStatus] - 状态变更回调
   * @param {Function} [options.onMessage] - 普通消息事件回调
   * @param {Function} [options.onError] - 错误回调
   * @param {Function} [options.onFallbackToPolling] - 重连失败超过最大次数后回调，切换到轮询
   * @param {Function} [options.onMessageUpdated] - 消息更新回调
   * @param {Function} [options.onStreamCompleted] - 流式完成回调
   * @param {string} [options.sessionId] - 关联的聊天会话 ID（用于 approvalStore 同步，优先级低于 payload.session_id）
   * @returns {Promise<void>}
   */
  async function connect(taskId, options = {}) {
    if (!taskId) return

    if (currentTaskId.value === taskId && isConnected.value) {
      logger.info(`[useResearchSSE] 任务 ${taskId} 已连接，跳过`)
      return
    }

    await disconnect()

    currentTaskId.value = taskId
    lastOptions = { ...options }

    try {
      await performConnect(taskId, lastOptions)
      logger.info(`[useResearchSSE] SSE 流已结束: taskId=${taskId}`)
    } catch (error) {
      if (error?.name !== 'AbortError') {
        logger.error(`[useResearchSSE] 连接异常: taskId=${taskId}`, error)
      } else {
        logger.info(`[useResearchSSE] 连接被主动中断: taskId=${taskId}`)
      }
      isConnected.value = false
      connectionStatus.value = ResearchConnectionStatus.DISCONNECTED
      currentTaskId.value = null
      lastOptions = null
      throw error
    }
  }

  /**
   * 处理 SSE 事件
   *
   * SSE 仅推送任务状态变更与历史审批恢复（approval_history），
   * 实时审批/工具事件统一通过 WebSocket 推送（task 频道）。
   *
   * @param {Object} parsed - SSE 解析后的事件
   * @param {Object} options - 回调选项
   * @returns {boolean|undefined} 返回 false 中断流读取
   */
  function handleEvent(parsed, options) {
    const type = parsed.type
    const data = parsed.data || parsed

    switch (type) {
      case 'approval_history':
        handleApprovalHistory(parsed, options)
        break
      case 'status_change':
        return handleStatusChange(parsed, options)
      case 'error':
        options.onError?.(data)
        break
      case 'message_updated':
        options.onMessageUpdated?.(data)
        break
      case 'stream_completed':
        options.onStreamCompleted?.(data)
        break
      case 'connected':
      case 'step_update':
      case 'done':
      case 'timeout':
        options.onMessage?.(parsed)
        break
      default:
        options.onMessage?.(parsed)
    }
  }

  /**
   * 处理审批历史事件
   *
   * 兼容两种数据结构：
   * - 单条：{ type: "approval_history", data: {...}, task_id: "..." }
   * - 批量：{ type: "approval_history", data: [{...}, ...], task_id: "..." }
   *
   * @param {Object} parsed - SSE 完整事件（含 task_id）
   * @param {Object} options - 回调选项
   */
  function handleApprovalHistory(parsed, options) {
    const historyData = parsed.data
    const historyTaskId = parsed.task_id || currentTaskId.value
    if (!historyData) return

    const sessionId = options.sessionId

    const restoreItem = (item) => {
      const itemSessionId = item?.session_id || sessionId
      approvalStore.restoreFromSSEHistory(item, historyTaskId, itemSessionId)
    }

    if (Array.isArray(historyData)) {
      historyData.forEach(restoreItem)
    } else {
      restoreItem(historyData)
    }
    options.onApproval?.({ type: 'history', data: historyData })
  }

  /**
   * 处理状态变更事件
   * @param {Object} parsed - SSE 完整事件
   * @param {Object} options - 回调选项
   * @returns {boolean|undefined} 返回 false 中断流读取
   */
  function handleStatusChange(parsed, options) {
    const status = parsed.status
    const data = parsed.data || parsed

    // resuming 状态：审批已确认，任务恢复执行
    if (status === 'resuming' || data?.state === 'resuming') {
      logger.info(`[useResearchSSE] 深度研究任务恢复执行: taskId=${currentTaskId.value}`)
    }

    options.onStatus?.(parsed)
    // 任务完成/失败时标记完成状态，中断流读取（不触发重连）
    if (status === 'completed' || status === 'failed') {
      isTaskCompleted = true
      return false
    }
  }

  /**
   * 断开 SSE 连接
   * @param {boolean} [manual=true] - 是否为主动断开，主动断开不触发重连
   */
  async function disconnect(manual = true) {
    isManualDisconnect = manual
    isTaskCompleted = false
    clearReconnectTimer()

    if (currentTaskId.value) {
      sseConnection.disconnectAll()
      isConnected.value = false
      connectionStatus.value = ResearchConnectionStatus.DISCONNECTED
      currentTaskId.value = null
      reconnectAttempts = 0
      if (manual) {
        lastOptions = null
      }
      logger.info('[useResearchSSE] 已断开连接')
    }
  }

  if (getCurrentScope()) {
    onScopeDispose(() => {
      disconnect(true)
    })
  }

  return {
    isConnected,
    currentTaskId,
    connectionStatus,
    connect,
    disconnect,
  }
}
