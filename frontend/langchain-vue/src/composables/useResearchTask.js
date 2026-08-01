import { ref, reactive, computed, nextTick } from 'vue'
import { deepResearchAPI } from '@/api'
import { ElMessage } from 'element-plus'
import { logger } from '@/utils/logger'

/**
 * 深度研究任务生命周期 composable
 *
 * 封装任务的启动 / 查看 / 删除 / 续研 / 在聊天中讨论 / 文件搜索 / 计时器等生命周期逻辑，
 * 以及任务进度与待审批 computed。
 *
 * 依赖注入（bridge）：
 *   由于 useResearchStream / useResearchPolling 依赖本 composable 的 stopElapsedTimer，
 *   而本 composable 依赖前者的 connectSSE / closeSSE / stopPolling，构成循环。
 *   通过 setBridge 在 view 中完成组装后注入，避免循环依赖。
 *
 * @param {Object} deps
 * @param {import('vue').Ref<Object|null>} deps.task - 当前任务 ref（与其它 composable 共享）
 * @param {import('vue').Ref<Object|null>} deps.fileBrowserRef - FileBrowser 组件 ref
 * @param {Object} deps.modelStore - useModelStore 实例
 * @param {Object} deps.approvalStore - useApprovalStore 实例
 * @param {Object} deps.router - useRouter 实例
 * @param {Object} deps.route - useRoute 实例
 * @returns {{
 *   isLoading: import('vue').Ref<boolean>,
 *   showTaskDetail: import('vue').Ref<boolean>,
 *   elapsedSeconds: import('vue').Ref<number>,
 *   researchForm: Object,
 *   continueForm: Object,
 *   continueDialogVisible: import('vue').Ref<boolean>,
 *   continueParentTask: import('vue').Ref<Object|null>,
 *   showFileSearch: import('vue').Ref<boolean>,
 *   fileSearchQuery: import('vue').Ref<string>,
 *   fileSearchResults: import('vue').Ref<Array<Object>>,
 *   fileSearchLoading: import('vue').Ref<boolean>,
 *   fileSearchSearched: import('vue').Ref<boolean>,
 *   progressPercentage: import('vue').ComputedRef<number>,
 *   taskPendingApprovals: import('vue').ComputedRef<Map<string, Object>>,
 *   startElapsedTimer: () => void,
 *   stopElapsedTimer: () => void,
 *   onModelChange: ({ providerId: string, modelName: string }) => void,
 *   onContinueModelChange: ({ providerId: string, modelName: string }) => void,
 *   startResearch: () => Promise<void>,
 *   viewTask: (selectedTask: Object) => Promise<void>,
 *   deleteTask: () => void,
 *   openContinueDialog: (taskData: Object) => void,
 *   submitContinueResearch: () => Promise<void>,
 *   handleContinueTask: (taskData: Object) => void,
 *   openInChat: () => void,
 *   handleFileSearch: () => Promise<void>,
 *   setBridge: (b: Object) => void,
 * }}
 */
export function useResearchTask({ task, fileBrowserRef, modelStore, approvalStore, router, route: _route }) {
  const isLoading = ref(false)
  const showTaskDetail = ref(false)
  const elapsedSeconds = ref(0)

  const showFileSearch = ref(false)
  const fileSearchQuery = ref('')
  const fileSearchResults = ref([])
  const fileSearchLoading = ref(false)
  const fileSearchSearched = ref(false)

  const continueDialogVisible = ref(false)
  const continueParentTask = ref(null)

  const researchForm = reactive({
    query: '',
    enable_web_search: true,
    knowledge_base_ids: [],
    provider_id: null,
    model_name: null,
    use_mcp: false,
    selected_mcp_servers: [],
    selected_tools: [],
  })

  const continueForm = reactive({
    additional_query: '',
    enable_web_search: true,
    knowledge_base_ids: [],
    provider_id: null,
    model_name: null,
    use_mcp: false,
    selected_mcp_servers: [],
    selected_tools: [],
  })

  /** @type {number | null} */
  let elapsedTimer = null

  const startElapsedTimer = () => {
    stopElapsedTimer()
    elapsedTimer = setInterval(() => {
      elapsedSeconds.value++
    }, 1000)
  }

  const stopElapsedTimer = () => {
    if (elapsedTimer) {
      clearInterval(elapsedTimer)
      elapsedTimer = null
    }
  }

  const progressPercentage = computed(() => {
    if (!task.value) return 0
    if (task.value.status === 'completed') return 100
    if (task.value.status === 'failed') return 0
    if (task.value.status === 'pending') return 10
    if (task.value.status === 'running') {
      const maxSeconds = 600
      const pct = Math.min(90, 10 + (elapsedSeconds.value / maxSeconds) * 80)
      return Math.round(pct)
    }
    return 0
  })

  /** 当前任务的待审批列表（过滤出 source=deep_research 且 taskId 匹配的审批） */
  const taskPendingApprovals = computed(() => {
    const result = new Map()
    const currentTaskId = task.value?.task_id
    if (!currentTaskId) return result
    for (const [id, entry] of approvalStore.pendingApprovals) {
      if (entry.source === 'deep_research' && entry.taskId === currentTaskId) {
        result.set(id, entry)
      }
    }
    return result
  })

  // ---------------------------------------------------------------------------
  // bridge：后注入的依赖（useResearchStream / useResearchPolling /
  // useResearchRealtime / useDocAnalysis 的函数），解决循环依赖
  // ---------------------------------------------------------------------------
  const bridge = {
    connectSSE: null,
    closeSSE: null,
    stopPolling: null,
    resetPolling: null,
    subscribeRealtimeForTask: null,
    clearRealtimeSubscriptions: null,
    checkDocAnalysisFile: null,
    autoLoadDocAnalysis: null,
  }

  const setBridge = (b) => Object.assign(bridge, b)

  const onModelChange = ({ providerId, modelName }) => {
    researchForm.provider_id = providerId
    researchForm.model_name = modelName
  }

  const onContinueModelChange = ({ providerId, modelName }) => {
    continueForm.provider_id = providerId
    continueForm.model_name = modelName
  }

  const startResearch = async () => {
    if (!researchForm.query.trim()) {
      ElMessage.warning('请输入研究主题')
      return
    }

    isLoading.value = true
    task.value = null
    bridge.resetPolling?.()
    elapsedSeconds.value = 0
    bridge.checkDocAnalysisFile?.()

    try {
      const modelConfig = modelStore.getModelConfig()
      const response = await deepResearchAPI.start({
        query: researchForm.query,
        enable_web_search: researchForm.enable_web_search,
        enable_doc_analysis: researchForm.knowledge_base_ids.length > 0,
        knowledge_base_ids: researchForm.knowledge_base_ids,
        use_mcp: researchForm.use_mcp,
        selected_mcp_servers: researchForm.selected_mcp_servers,
        selected_tools: researchForm.selected_tools,
        provider_id: researchForm.provider_id || modelConfig.provider_id,
        model_name: researchForm.model_name || modelConfig.model_name,
        enable_deep_thinking: modelStore.thinkingEnabled,
        temperature: modelConfig.temperature,
        max_tokens: modelConfig.max_tokens,
        special_params: modelConfig.special_params,
      })
      task.value = response.data.data || response.data
      showTaskDetail.value = true
      ElMessage.success('研究任务已启动')

      bridge.stopPolling?.()
      startElapsedTimer()
      // 启动新任务后立即订阅 WebSocket 实时事件，避免在用户切走再回来前丢失事件
      bridge.subscribeRealtimeForTask?.(task.value)
      bridge.connectSSE?.(task.value.task_id)
    } catch (error) {
      logger.error('启动研究任务失败:', error)
      const detail = error.response?.data?.data || error.response?.data?.message
      if (detail) {
        ElMessage.error(detail)
      } else {
        ElMessage.error('启动研究任务失败，请稍后重试')
      }
    } finally {
      isLoading.value = false
    }
  }

  const viewTask = async (selectedTask) => {
    bridge.closeSSE?.()
    bridge.stopPolling?.()
    stopElapsedTimer()

    task.value = selectedTask
    showTaskDetail.value = true
    bridge.checkDocAnalysisFile?.()
    // 接入统一 WebSocket 实时同步：入口先订阅一次（基于 selectedTask 当前已知字段）
    bridge.subscribeRealtimeForTask?.(selectedTask)

    if (selectedTask.status === 'running' || selectedTask.status === 'pending') {
      startElapsedTimer()
      bridge.connectSSE?.(selectedTask.task_id)
    } else if (selectedTask.task_id) {
      try {
        const resp = await deepResearchAPI.getStatus(selectedTask.task_id)
        const fresh = resp.data?.data || resp.data
        if (fresh) {
          task.value = { ...selectedTask, ...fresh }
          // fresh 可能补充 chat_session_id 字段，重新订阅（幂等：若已订阅同 task+session 则跳过）
          bridge.subscribeRealtimeForTask?.(task.value)
          if (fresh.status === 'running' || fresh.status === 'pending') {
            startElapsedTimer()
            bridge.connectSSE?.(fresh.task_id)
          } else if (fresh.status === 'completed') {
            nextTick(() => {
              if (fileBrowserRef.value) {
                fileBrowserRef.value.loadFiles()
              }
            })
          }
        }
      } catch (e) {
        logger.warn('获取任务最新状态失败:', e)
      }
    }
  }

  const deleteTask = () => {
    // 停止轮询，避免对已删除任务持续请求
    bridge.stopPolling?.()
    // 断开 SSE 连接
    bridge.closeSSE?.()
    // 取消 WebSocket 实时订阅，避免对已删除任务继续接收事件
    bridge.clearRealtimeSubscriptions?.()
    // 清理该任务关联的审批条目
    if (task.value?.task_id) {
      approvalStore.clearByTaskId(task.value.task_id)
    }
    task.value = null
    showTaskDetail.value = false
    bridge.checkDocAnalysisFile?.()
  }

  const openContinueDialog = (taskData) => {
    continueParentTask.value = taskData
    continueForm.additional_query = ''
    continueForm.enable_web_search = taskData.enable_web_search ?? true
    continueForm.knowledge_base_ids = taskData.knowledge_base_ids || []
    continueForm.provider_id = taskData.provider_id || null
    continueForm.model_name = taskData.model_name || null
    continueForm.use_mcp = taskData.use_mcp ?? false
    continueForm.selected_mcp_servers = taskData.selected_mcp_servers || []
    continueForm.selected_tools = taskData.selected_tools || []
    if (continueForm.provider_id && continueForm.model_name) {
      modelStore.selectProvider(continueForm.provider_id, continueForm.model_name)
    }
    continueDialogVisible.value = true
  }

  const submitContinueResearch = async () => {
    if (!continueParentTask.value) return
    isLoading.value = true
    try {
      const modelConfig = modelStore.getModelConfig()
      const response = await deepResearchAPI.continueResearch(
        continueParentTask.value.task_id,
        {
          additional_query: continueForm.additional_query,
          enable_web_search: continueForm.enable_web_search,
          enable_doc_analysis: continueForm.knowledge_base_ids.length > 0,
          knowledge_base_ids: continueForm.knowledge_base_ids,
          use_mcp: continueForm.use_mcp,
          selected_mcp_servers: continueForm.selected_mcp_servers,
          selected_tools: continueForm.selected_tools,
          provider_id: continueForm.provider_id || modelConfig.provider_id,
          model_name: continueForm.model_name || modelConfig.model_name,
          enable_deep_thinking: modelStore.thinkingEnabled,
          temperature: modelConfig.temperature,
          max_tokens: modelConfig.max_tokens,
          special_params: modelConfig.special_params,
        }
      )
      task.value = response.data.data || response.data
      showTaskDetail.value = true
      continueDialogVisible.value = false
      ElMessage.success('续研任务已启动')
      bridge.stopPolling?.()
      startElapsedTimer()
      // 续研任务同样作为新任务启动，立即订阅 WebSocket 实时事件
      bridge.subscribeRealtimeForTask?.(task.value)
      bridge.connectSSE?.(task.value.task_id)
    } catch (error) {
      logger.error('启动续研任务失败:', error)
      const detail = error.response?.data?.data || error.response?.data?.message
      ElMessage.error(detail || '启动续研任务失败')
    } finally {
      isLoading.value = false
    }
  }

  const handleContinueTask = (taskData) => {
    openContinueDialog(taskData)
  }

  const openInChat = () => {
    if (!task.value?.query || !task.value?.task_id) {
      ElMessage.warning('研究任务信息不完整，无法在聊天中讨论')
      return
    }
    const taskId = task.value.task_id
    const sessionId = task.value.session_id
    const researchQuery = task.value.query
    // 如果研究任务关联了聊天会话，跳转到该会话；否则创建新会话
    const query = {
      research_task_id: taskId,
      q: `关于"${researchQuery}"的深度研究，请帮我进一步分析`,
      session_id: sessionId || undefined,
      research_query: researchQuery,
    }
    console.log('[DeepResearch] 跳转聊天:', {
      task_id: taskId,
      session_id: sessionId || '(未关联)',
      has_session_id: !!sessionId,
      research_query: researchQuery,
    })
    router.push({ path: '/chat', query })
  }

  const handleFileSearch = async () => {
    if (!fileSearchQuery.value.trim()) return
    fileSearchLoading.value = true
    fileSearchSearched.value = true
    try {
      const response = await deepResearchAPI.searchFiles(fileSearchQuery.value)
      if (response.data?.code === 200) {
        fileSearchResults.value = response.data.data?.items || response.data.data?.files || []
      }
    } catch (error) {
      logger.error('文件搜索失败:', error)
      ElMessage.error('文件搜索失败')
    } finally {
      fileSearchLoading.value = false
    }
  }

  return {
    isLoading,
    showTaskDetail,
    elapsedSeconds,
    researchForm,
    continueForm,
    continueDialogVisible,
    continueParentTask,
    showFileSearch,
    fileSearchQuery,
    fileSearchResults,
    fileSearchLoading,
    fileSearchSearched,
    progressPercentage,
    taskPendingApprovals,
    startElapsedTimer,
    stopElapsedTimer,
    onModelChange,
    onContinueModelChange,
    startResearch,
    viewTask,
    deleteTask,
    openContinueDialog,
    submitContinueResearch,
    handleContinueTask,
    openInChat,
    handleFileSearch,
    setBridge,
  }
}
