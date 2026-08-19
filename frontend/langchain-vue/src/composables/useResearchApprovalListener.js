import { useSyncStore } from '@/stores/sync'
import { useSessionStore } from '@/stores/session'
import { useChatDeepResearchStore } from '@/stores/chatDeepResearch'
import { deepResearchAPI } from '@/api/research'
import { readSSEStream } from '@/utils/sse'

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
 * - 自动重连（指数退避，最多 3 次）
 *
 * @returns {{ connectResearchSSE: (taskId: string) => Promise<void>, disconnectResearchSSE: () => void }}
 */
export function useResearchApprovalListener() {
  const syncStore = useSyncStore()
  const sessionStore = useSessionStore()
  const chatDeepResearchStore = useChatDeepResearchStore()

  let abortController = null
  let retryCount = 0

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

    abortController = new AbortController()
    try {
      const response = await deepResearchAPI.streamFetch(taskId, {
        signal: abortController.signal,
      })
      if (!response.ok) return

      // 连接成功，重置重连计数
      retryCount = 0

      const processChunk = async () => {
        try {
          await readSSEStream(response, (parsed) => {
            // 命名边界：readSSEStream 已统一调用 parseProtocolEvent 完成转换
            // （toCamelCase + type 还原为后端原始 snake_case 协议路由标识符）。
            if (parsed.type === 'approval' || parsed.type === 'approval_timeout' || parsed.type === 'approval_processed') {
              // 审批事件经 sync 层转发（视图层收敛），语义与原直调 approvalStore 完全一致
              syncStore.handleApprovalAction('handleApprovalEvent', parsed.data || parsed, {
                source: 'deep_research',
                taskId,
                sessionId: sessionStore.currentSessionId,
              })
            } else if (parsed.type === 'approval_history') {
              const effectiveTaskId = parsed.taskId || taskId
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
                abortController?.abort()
              }
            }
          }, abortController.signal)
        } catch (e) {
          if (e.name !== 'AbortError') {
            console.warn('[ResearchApprovalListener] 深度研究 SSE 连接异常:', e)
            // 指数退避重连
            if (retryCount < MAX_SSE_RETRY) {
              retryCount++
              const delay = SSE_RETRY_BASE_DELAY * Math.pow(2, retryCount - 1)
              console.log(`[ResearchApprovalListener] ${delay}ms 后重连深度研究 SSE (第${retryCount}次)`)
              setTimeout(() => connectResearchSSE(taskId), delay)
            }
          }
        }
      }
      processChunk() // 不 await，后台运行
    } catch (e) {
      if (e.name !== 'AbortError') {
        console.warn('[ResearchApprovalListener] 深度研究 SSE 连接失败:', e)
        // 连接失败也尝试重连
        if (retryCount < MAX_SSE_RETRY) {
          retryCount++
          const delay = SSE_RETRY_BASE_DELAY * Math.pow(2, retryCount - 1)
          console.log(`[ResearchApprovalListener] ${delay}ms 后重连深度研究 SSE (第${retryCount}次)`)
          setTimeout(() => connectResearchSSE(taskId), delay)
        }
      }
    }
  }

  const disconnectResearchSSE = () => {
    if (abortController) {
      abortController.abort()
      abortController = null
    }
    retryCount = 0
  }

  return {
    connectResearchSSE,
    disconnectResearchSSE,
  }
}
