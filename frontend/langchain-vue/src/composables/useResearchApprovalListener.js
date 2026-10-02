import { useSyncStore } from '@/stores/sync'
import { useSessionStore } from '@/stores/session'
import { useChatDeepResearchStore } from '@/stores/chatDeepResearch'
import { deepResearchAPI } from '@/api/research'
import { useSSEStream } from '@/composables/useSSEStream'
import { logger } from '@/utils/logger'

const MAX_SSE_RETRY = 3
const SSE_RETRY_BASE_DELAY = 2000 // 2s, 4s, 8s

/**
 * 深度研究 SSE 审批监听 composable
 *
 * 负责建立/断开深度研究任务的 SSE 连接，恢复审批事件监听。
 * 从 ChatView.vue 提取，解耦聊天视图与深度研究 API 层的直接耦合。
 *
 * 功能：
 * - connectResearchSSE：连接指定任务的 SSE 流，监听审批/状态变化事件
 * - disconnectResearchSSE：断开当前连接并重置状态
 * - 自动重连（指数退避，最多 3 次；连接成功后计数清零，重试策略留在本层）
 *
 * SSE 骨架（AbortController/活跃标志/读流循环/清理）委托 useSSEStream；
 * resolveOnBadResponse：连接非 2xx 时静默结束（与原实现一致，不提取错误、不重连）。
 *
 * @returns {{ connectResearchSSE: (taskId: string) => Promise<void>, disconnectResearchSSE: () => void }}
 */
export function useResearchApprovalListener() {
  const syncStore = useSyncStore()
  const sessionStore = useSessionStore()
  const chatDeepResearchStore = useChatDeepResearchStore()

  let retryCount = 0

  // onEvent 惰性引用 sseStream.abort（status_change 终态时中断读流）
  const sseStream = useSSEStream({
    resolveOnBadResponse: true,
    onEvent: (parsed) => {
      // 命名边界：readSSEStream 已统一调用 parseProtocolEvent 完成转换
      // （toCamelCase + type 还原为后端原始 snake_case 协议路由标识符）。
      // 后端 views_stream.py SSE 仅发送 approval_history / connected /
      // timeout / status_change / done / error 事件；实时审批事件走
      // WebSocket 通道（approvalHandler），此处不监听审批实时事件
      //（ARCH-15/16：原 approval/approval_timeout/approval_processed
      // 过滤分支为死代码，后端从不发送，已下线）。
      if (parsed.type === 'approval_history') {
        const effectiveTaskId = parsed.taskId || currentTaskId
        if (parsed.data) {
          syncStore.handleApprovalAction('restoreFromSSEHistory', parsed.data, {
            taskId: effectiveTaskId,
            sessionId: sessionStore.currentSessionId,
          })
        }
      } else if (parsed.type === 'status_change') {
        const status = parsed.status
        if (status === 'completed' || status === 'failed') {
          // abort 后 readSSEStream 在下一轮 while 检测 signal.aborted 退出
          sseStream.abort()
        }
      }
    },
  })

  let currentTaskId = null

  /**
   * 连接深度研究 SSE 流（刷新/新浏览器恢复审批监听）
   *
   * @param {string} taskId - 深度研究任务 ID
   */
  const connectResearchSSE = async (taskId) => {
    if (!taskId) return
    // 先检查任务状态，只有 running 才连接
    try {
      const resp = await deepResearchAPI.getStatus(taskId)
      const taskData = resp.data?.data || resp.data
      if (!taskData || taskData.status !== 'running') return
    } catch (error) {
      // 任务不存在（404）时清空 researchTaskId，避免后续重复请求已删除任务
      if (error?.response?.status === 404 && chatDeepResearchStore.researchTaskId === taskId) {
        chatDeepResearchStore.researchTaskId = null
      }
      return // 查询失败则不连接
    }

    currentTaskId = taskId

    const result = await sseStream.start((signal) =>
      deepResearchAPI.streamFetch(taskId, { signal }).then((response) => {
        // 连接成功（2xx）即重置重连计数（对齐原实现 response.ok 后 retryCount = 0）
        if (response.ok) retryCount = 0
        return response
      })
    )

    // completed / aborted（含非 2xx 静默结束）：与原实现一致，不重连
    if (result.status !== 'error') return

    logger.warn('[ResearchApprovalListener] 深度研究 SSE 连接异常:', result.error)
    // 指数退避重连
    if (retryCount < MAX_SSE_RETRY) {
      retryCount++
      const delay = SSE_RETRY_BASE_DELAY * Math.pow(2, retryCount - 1)
      logger.log(`[ResearchApprovalListener] ${delay}ms 后重连深度研究 SSE (第${retryCount}次)`)
      setTimeout(() => connectResearchSSE(taskId), delay)
    }
  }

  const disconnectResearchSSE = () => {
    sseStream.abort()
    retryCount = 0
  }

  return {
    connectResearchSSE,
    disconnectResearchSSE,
  }
}
