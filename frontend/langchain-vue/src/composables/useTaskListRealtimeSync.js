import { useRealtimeSync } from '@/composables/useRealtimeSync'
import { logger } from '@/utils/logger'

/**
 * 任务列表实时同步 composable（学习工作流 / 深度研究公共能力）
 *
 * 订阅 user 频道 task_created / task_status_changed / task_deleted 事件，
 * 收到后自动刷新任务列表，实现跨浏览器实时同步（新任务出现、状态更新、删除移除）。
 *
 * 与后端对齐：
 * - 深度研究模块：research/views.py（start/delete）+ writeback.py（终态）发布
 * - 学习工作流模块：learning/signals.py 监听 WorkflowSession post_save/post_delete 发布
 *
 * @param {string} sourceType - 视图标识，用于日志区分（如 'DeepResearch' / 'Workflow'）
 * @param {() => object|null} getTaskListRef - 返回 TaskList 组件 ref（含 refreshTasks 方法）
 * @param {Object} [options]
 * @param {(payload: object) => void} [options.onTaskDeleted] - 收到 task_deleted 时的额外处理
 *   （如当前详情正是被删除任务时关闭详情视图）
 * @returns {{ start: () => void, stop: () => void }}
 */
export function useTaskListRealtimeSync(sourceType, getTaskListRef, options = {}) {
  const realtimeSync = useRealtimeSync()
  let unsubscribe = null

  const handleTaskEvent = (event) => {
    if (event.type === 'task_deleted') {
      const deletedTaskId = event.payload?.taskId
      logger.info(`[${sourceType}] 收到 task_deleted 事件，自动刷新任务列表: taskId=${deletedTaskId}`)
      options.onTaskDeleted?.(event.payload)
      const listRef = getTaskListRef()
      if (listRef?.refreshTasks) listRef.refreshTasks()
      return
    }
    if (event.type !== 'task_created' && event.type !== 'task_status_changed') return
    logger.info(`[${sourceType}] 收到 ${event.type} 事件，自动刷新任务列表`)
    const listRef = getTaskListRef()
    if (listRef?.refreshTasks) listRef.refreshTasks()
  }

  /**
   * 开始监听 user 频道任务事件；附带一次兜底刷新
   * （任务列表首次加载可能在事件到达前已完成，需补一次确保最新）
   */
  const start = () => {
    stop()
    unsubscribe = realtimeSync.subscribeUserEvents(handleTaskEvent)
    const listRef = getTaskListRef()
    if (listRef?.refreshTasks) listRef.refreshTasks()
  }

  /** 停止监听并释放订阅（组件卸载 / keep-alive 停用时调用） */
  const stop = () => {
    if (unsubscribe) {
      unsubscribe()
      unsubscribe = null
    }
  }

  return { start, stop }
}
