import { reactive, ref, nextTick } from 'vue'
import { workflowAPI } from '@/api/workflow'
import { ElMessage } from 'element-plus'
import { logger } from '@/utils/logger'

/**
 * 学习工作流测验 composable
 *
 * 封装练习题答题表单与提交逻辑：
 *   - answersForm：reactive 对象，key=question.id，value=用户答案
 *   - initAnswersFromQuiz / resetAnswers：供 useWorkflowExecution 在 SSE/轮询/store 同步
 *     收到 quiz 时初始化答案表单，在 startWorkflow/resetWorkflow/deleteTask 时清空
 *   - submitAnswers：提交答案，should_retry 时重连 SSE 重新出题，否则加载文件与学习资料
 *
 * 依赖注入（bridge）：
 *   submitAnswers 在 should_retry 分支需要 connectSSE/closeSSE/stopPolling（来自
 *   useWorkflowExecution），在完成分支需要 autoLoadKeyFile（来自 useWorkflowFiles）。
 *   通过 setBridge 在 view 中完成组装后注入，避免与 useWorkflowExecution 循环依赖。
 *
 * @param {Object} deps
 * @param {import('vue').Ref<Object|null>} deps.execution - 当前工作流执行状态 ref
 * @param {import('vue').Ref<Object|null>} deps.fileBrowserRef - FileBrowser 组件 ref
 * @returns {{
 *   answersForm: Object,
 *   isSubmitting: import('vue').Ref<boolean>,
 *   initAnswersFromQuiz: (quiz: Object) => void,
 *   resetAnswers: () => void,
 *   submitAnswers: () => Promise<void>,
 *   setBridge: (b: Object) => void,
 * }}
 */
export function useWorkflowQuiz({ execution, fileBrowserRef }) {
  const answersForm = reactive({})
  const isSubmitting = ref(false)

  const bridge = {
    connectSSE: null,
    closeSSE: null,
    stopPolling: null,
    autoLoadKeyFile: null,
  }

  const setBridge = (b) => Object.assign(bridge, b)

  /**
   * 从 quiz 对象初始化答案表单（仅在表单为空时初始化，避免覆盖用户已填答案）
   * @param {Object} quiz - 测验对象，含 questions 数组
   */
  const initAnswersFromQuiz = (quiz) => {
    if (!quiz || Object.keys(answersForm).length) return
    quiz.questions.forEach(q => {
      answersForm[q.id] = ''
    })
  }

  /** 清空答案表单（保留 reactive 引用） */
  const resetAnswers = () => {
    Object.keys(answersForm).forEach(key => delete answersForm[key])
  }

  /**
   * 提交答案
   * - 有未作答题目时提示并中止
   * - should_retry=true：清空答案、停止轮询、重连 SSE 重新出题
   * - should_retry=false：加载生成文件与学习资料
   * @returns {Promise<void>}
   */
  const submitAnswers = async () => {
    const hasEmpty = execution.value.quiz.questions.some(q => !answersForm[q.id])
    if (hasEmpty) {
      ElMessage.warning('请完成所有题目')
      return
    }

    isSubmitting.value = true

    try {
      const response = await workflowAPI.submitAnswers(execution.value.threadId, answersForm)
      const responseData = response.data.data || response.data
      execution.value = { ...execution.value, ...responseData }
      ElMessage.success('答案已提交')

      if (responseData.shouldRetry) {
        resetAnswers()
        bridge.stopPolling?.()
        bridge.connectSSE?.(execution.value.threadId)
      } else {
        nextTick(() => {
          if (fileBrowserRef.value) {
            fileBrowserRef.value.loadFiles()
          }
          bridge.autoLoadKeyFile?.()
        })
      }
    } catch (error) {
      logger.error('提交答案失败:', error)
      const msg = error.response?.data?.message || error.message || '提交答案失败，请稍后重试'
      ElMessage.error(msg)
    } finally {
      isSubmitting.value = false
    }
  }

  return {
    answersForm,
    isSubmitting,
    initAnswersFromQuiz,
    resetAnswers,
    submitAnswers,
    setBridge,
  }
}
