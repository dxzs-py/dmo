import { ref } from 'vue'
import { useRealtimeSync } from '@/composables/useRealtimeSync'
import { useSyncStore } from '@/stores/sync'
import { logger } from '@/utils/logger'
import { toCamelCase } from '@/utils/session-transformers'

/**
 * 任务实时同步（WebSocket）composable
 *
 * 封装 task / session 通道的 WebSocket 订阅与清理：
 *   - subscribeRealtimeForTask：为指定任务订阅 task 频道（及关联 chat session 频道），
 *     幂等：task_id / thread_id 与 session_id 均与当前已订阅一致时跳过
 *   - clearRealtimeSubscriptions：取消所有订阅，避免泄漏
 *
 * 实时审批 / 工具调用 / stream_completed 等事件统一由 syncStore.handleRealtimeEvent 消费；
 * SSE 仅负责流式输出与 approval_history，二者互不干扰。
 *
 * @param {string} sourceType - 视图标识，用于日志区分配（如 'DeepResearch' / 'Workflow'）
 * @param {string} taskIdField - 任务对象中用作 task ID 的字段名（如 'task_id' / 'thread_id'）
 * @returns {{
 *   subscribeRealtimeForTask: (taskObj: object | null) => void,
 *   clearRealtimeSubscriptions: () => void,
 * }}
 */
export function useTaskRealtimeSync(sourceType, taskIdField) {
  const realtime = useRealtimeSync()
  const syncStore = useSyncStore()

  /** @type {import('vue').Ref<Array<() => void>>} */
  const realtimeUnsubscribers = ref([])
  let subscribedTaskId = null
  let subscribedSessionId = null

  const clearRealtimeSubscriptions = () => {
    realtimeUnsubscribers.value.forEach(fn => {
      try { fn() } catch (e) { logger.warn(`[${sourceType}] 取消订阅失败`, e) }
    })
    realtimeUnsubscribers.value = []
    subscribedTaskId = null
    subscribedSessionId = null
  }

  /**
   * 为指定任务订阅 WebSocket 实时事件
   * 幂等：若 task_id/thread_id 与 session_id 均与当前已订阅一致，则跳过
   * @param {object | null} taskObj
   */
  const subscribeRealtimeForTask = (taskObj) => {
    if (!taskObj) return
    const taskId = taskObj[taskIdField]
    const chatSessionId = taskObj.chatSessionId || taskObj.sessionId

    // 幂等判断：task 和 session 均已订阅则跳过（fresh 刷新后重复调用场景）
    if (taskId && taskId === subscribedTaskId && chatSessionId === subscribedSessionId) {
      return
    }

    // 清理上一任务的订阅，避免泄漏
    clearRealtimeSubscriptions()
    subscribedTaskId = taskId || null
    subscribedSessionId = chatSessionId || null

    if (taskId) {
      // 频道路由优化（Task 16）：后端 _resolve_channels 在有关联 chat_session_id 时
      // 同时广播到 session + task 频道，前端无需双重订阅。
      // - 有 chat_session_id：只订阅 session 频道（包含完整 tool_call/approval/stream_completed 事件）
      // - 无 chat_session_id（独立深度研究）：只订阅 task 频道
      if (chatSessionId) {
        const unsubSession = realtime.subscribeSession(chatSessionId, syncStore.handleRealtimeEvent, { replayFromSeq: 0 })
        realtimeUnsubscribers.value.push(unsubSession)
        logger.info(`[${sourceType}] 订阅 session=${chatSessionId}（关联 chat，task=${taskId} 事件由双频道广播）`)
      } else {
        const unsubTask = realtime.subscribeTask(taskId, syncStore.handleRealtimeEvent, { replayFromSeq: 0 })
        realtimeUnsubscribers.value.push(unsubTask)
        logger.info(`[${sourceType}] 订阅 task=${taskId}（无关联 chat session）`)
      }
    }
  }

  return { subscribeRealtimeForTask, clearRealtimeSubscriptions }
}
