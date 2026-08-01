import { ref } from 'vue'
import { useRealtimeSync } from '@/composables/useRealtimeSync'
import { useSyncStore } from '@/stores/sync'
import { logger } from '@/utils/logger'

/**
 * 深度研究实时同步（WebSocket）composable
 *
 * 封装 task / session 通道的 WebSocket 订阅与清理：
 *   - subscribeRealtimeForTask：为指定任务订阅 task 频道（及关联 chat session 频道），
 *     幂等：task_id 与 session_id 均与当前已订阅一致时跳过
 *   - clearRealtimeSubscriptions：取消所有订阅，避免泄漏
 *
 * 实时审批 / 工具调用 / stream_completed 等事件统一由 syncStore.handleRealtimeEvent 消费；
 * SSE 仅负责流式输出与 approval_history，二者互不干扰。
 *
 * @returns {{
 *   realtimeUnsubscribers: import('vue').Ref<Array<() => void>>,
 *   subscribeRealtimeForTask: (taskObj: { task_id?: string, chat_session_id?: string, session_id?: string } | null) => void,
 *   clearRealtimeSubscriptions: () => void,
 * }}
 */
export function useResearchRealtime() {
  const realtime = useRealtimeSync()
  const syncStore = useSyncStore()

  /** @type {import('vue').Ref<Array<() => void>>} */
  const realtimeUnsubscribers = ref([])
  let subscribedTaskId = null
  let subscribedSessionId = null

  const clearRealtimeSubscriptions = () => {
    realtimeUnsubscribers.value.forEach(fn => {
      try { fn() } catch (e) { logger.warn('[DeepResearchView] 取消订阅失败', e) }
    })
    realtimeUnsubscribers.value = []
    subscribedTaskId = null
    subscribedSessionId = null
  }

  /**
   * 为指定任务订阅 WebSocket 实时事件
   * 幂等：若 task_id 与 session_id 均与当前已订阅一致，则跳过
   * @param {{ task_id?: string, chat_session_id?: string, session_id?: string } | null} taskObj
   */
  const subscribeRealtimeForTask = (taskObj) => {
    if (!taskObj) return
    const taskId = taskObj.task_id
    const chatSessionId = taskObj.chat_session_id || taskObj.session_id

    // 幂等判断：task 和 session 均已订阅则跳过（fresh 刷新后重复调用场景）
    if (taskId && taskId === subscribedTaskId && chatSessionId === subscribedSessionId) {
      return
    }

    // 清理上一任务的订阅，避免泄漏
    clearRealtimeSubscriptions()
    subscribedTaskId = taskId || null
    subscribedSessionId = chatSessionId || null

    if (taskId) {
      const unsubTask = realtime.subscribeTask(taskId, syncStore.handleRealtimeEvent, { replayFromSeq: 0 })
      realtimeUnsubscribers.value.push(unsubTask)
      if (chatSessionId) {
        const unsubSession = realtime.subscribeSession(chatSessionId, syncStore.handleRealtimeEvent, { replayFromSeq: 0 })
        realtimeUnsubscribers.value.push(unsubSession)
        logger.info(`[DeepResearchView] 订阅 task=${taskId} + session=${chatSessionId}`)
      } else {
        logger.info(`[DeepResearchView] 订阅 task=${taskId}（无关联 chat session）`)
      }
    }
  }

  return {
    realtimeUnsubscribers,
    subscribeRealtimeForTask,
    clearRealtimeSubscriptions,
  }
}
