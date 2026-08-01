import { ref } from 'vue'
import { deepResearchAPI } from '@/api'
import { ElMessage } from 'element-plus'
import { logger } from '@/utils/logger'
import { useResearchSSE } from '@/composables/useResearchSSE'

/**
 * 深度研究 SSE 流式编排 composable
 *
 * 复用 useResearchSSE（统一 SSE 连接管理 + 自动重连 + approval_history 处理），
 * 在其回调上编排 DeepResearchView 特有的业务逻辑：
 *   - status_change：更新 task 状态 / 进度提示 / 最终报告，完成时刷新文件与文档分析
 *   - connected / step_update：更新进度提示
 *   - done / timeout：主动断开（阻止重连）并回退到轮询
 *   - error：ElMessage 提示
 *   - onFallbackToPolling：useResearchSSE 重连耗尽后回退轮询
 *
 * 与 useResearchSSE 的关系：
 *   useResearchSSE 提供通用 SSE 连接生命周期（connect / disconnect / 指数退避重连 /
 *   approval_history 委托 approvalStore）。本 composable 不重复实现连接管理，仅在其回调
 *   中注入 DeepResearchView 业务逻辑，并保留 view 原有的"done/timeout 主动断开 + 轮询兜底"
 *   语义（通过 disconnect(true) 阻止 useResearchSSE 自动重连，转由轮询接管状态获取）。
 *
 * @param {Object} deps
 * @param {import('vue').Ref<Object|null>} deps.task - 当前任务 ref
 * @param {import('vue').Ref<Object|null>} deps.fileBrowserRef - FileBrowser 组件 ref
 * @param {() => void} deps.stopElapsedTimer - 停止计时器
 * @param {() => void} deps.checkDocAnalysisFile - 重置文档分析状态
 * @param {() => Promise<void>} deps.autoLoadDocAnalysis - 自动加载文档分析
 * @param {() => void} deps.startPolling - 启动轮询（useResearchPolling.startPolling）
 * @returns {{
 *   progressMessage: import('vue').Ref<string>,
 *   connectSSE: (taskId: string) => Promise<void>,
 *   closeSSE: () => void,
 * }}
 */
export function useResearchStream({ task, fileBrowserRef, stopElapsedTimer, checkDocAnalysisFile, autoLoadDocAnalysis, startPolling }) {
  const sse = useResearchSSE()
  const progressMessage = ref('')

  /** 标记本轮 SSE 是否已触发轮询兜底，避免 done/timeout 与流结束后重复启动 */
  let pollingStarted = false

  const handleStatusChange = (parsed) => {
    if (task.value) {
      task.value.status = parsed.status
      progressMessage.value = parsed.message || ''
      if (parsed.final_report) {
        task.value.final_report = parsed.final_report
      }
    }
    if (parsed.status === 'completed' || parsed.status === 'failed') {
      stopElapsedTimer()
      // 主动获取完整任务数据，确保 final_report、files 等字段不丢失
      if (parsed.status === 'completed' && task.value?.task_id) {
        deepResearchAPI.getStatus(task.value.task_id).then(resp => {
          const fresh = resp.data?.data || resp.data
          if (fresh) {
            task.value = { ...task.value, ...fresh }
          }
        }).catch(() => {})
      }
      checkDocAnalysisFile()
      if (fileBrowserRef.value) {
        fileBrowserRef.value.loadFiles()
      }
      autoLoadDocAnalysis()
    }
  }

  const handleMessage = (parsed) => {
    switch (parsed.type) {
      case 'connected':
        progressMessage.value = '已连接，等待研究启动...'
        break
      case 'step_update':
        progressMessage.value = parsed.step || ''
        break
      case 'done':
        // SSE 流结束但任务可能尚未完成（如连接超时），主动断开阻止重连，启动轮询检查
        stopElapsedTimer()
        sse.disconnect(true)
        if (!pollingStarted && task.value && task.value.status !== 'completed' && task.value.status !== 'failed') {
          pollingStarted = true
          startPolling()
        }
        break
      case 'timeout':
        progressMessage.value = '连接超时，正在回退到轮询模式...'
        sse.disconnect(true)
        if (!pollingStarted && task.value && task.value.status !== 'completed' && task.value.status !== 'failed') {
          pollingStarted = true
          startPolling()
        }
        break
    }
  }

  const handleError = (data) => {
    ElMessage.error(data?.message || '研究执行出错')
    stopElapsedTimer()
  }

  const handleFallbackToPolling = () => {
    if (!pollingStarted && task.value && task.value.status !== 'completed' && task.value.status !== 'failed') {
      pollingStarted = true
      startPolling()
    }
  }

  /**
   * 连接 SSE 流
   * 复用 useResearchSSE.connect，流结束后确保断开并按需启动轮询兜底
   * @param {string} taskId
   */
  const connectSSE = async (taskId) => {
    pollingStarted = false
    try {
      await sse.connect(taskId, {
        onStatus: handleStatusChange,
        onMessage: handleMessage,
        onError: handleError,
        onFallbackToPolling: handleFallbackToPolling,
        sessionId: task.value?.session_id || task.value?.chat_session_id,
      })
      // 流正常结束（后端关闭流或 done/timeout 主动断开后的自然结束）
      // 确保 useResearchSSE 不重连；任务未完成且未启动轮询则兜底启动
      await sse.disconnect(true)
      if (!pollingStarted && task.value && task.value.status !== 'completed' && task.value.status !== 'failed') {
        pollingStarted = true
        startPolling()
      }
    } catch (error) {
      if (error?.name === 'AbortError') {
        // 主动断开导致的 AbortError，轮询已由 handleMessage 启动
        return
      }
      logger.error('SSE连接失败，回退到轮询:', error)
      if (!pollingStarted && task.value && task.value.status !== 'completed' && task.value.status !== 'failed') {
        pollingStarted = true
        startPolling()
      }
    }
  }

  const closeSSE = () => {
    sse.disconnect(true)
  }

  return {
    progressMessage,
    connectSSE,
    closeSSE,
  }
}
