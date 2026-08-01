import { describe, it, expect, beforeEach, vi } from 'vitest'
import { ref, nextTick } from 'vue'

const { mockElMessage } = vi.hoisted(() => ({
  mockElMessage: { success: vi.fn(), info: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

const mockSubmitAnswers = vi.fn()
vi.mock('@/api', () => ({
  workflowAPI: {
    submitAnswers: (...args) => mockSubmitAnswers(...args),
  },
}))

vi.mock('element-plus', () => ({ ElMessage: mockElMessage }))

vi.mock('@/utils/logger', () => ({
  logger: { log: () => {}, info: () => {}, warn: () => {}, error: () => {}, debug: () => {} },
}))

import { useWorkflowQuiz } from '../useWorkflowQuiz'

describe('useWorkflowQuiz', () => {
  let execution
  let fileBrowserRef
  let connectSSE
  let closeSSE
  let stopPolling
  let autoLoadKeyFile

  beforeEach(() => {
    vi.clearAllMocks()
    execution = ref({
      thread_id: 'wf-1',
      quiz: {
        questions: [
          { id: 'q1', type: 'multiple_choice', question: 'Q1', points: 10, options: ['A', 'B'] },
          { id: 'q2', type: 'short_answer', question: 'Q2', points: 20 },
        ],
      },
    })
    fileBrowserRef = ref({ loadFiles: vi.fn() })
    connectSSE = vi.fn()
    closeSSE = vi.fn()
    stopPolling = vi.fn()
    autoLoadKeyFile = vi.fn()
  })

  function createQuiz() {
    const q = useWorkflowQuiz({ execution, fileBrowserRef })
    q.setBridge({ connectSSE, closeSSE, stopPolling, autoLoadKeyFile })
    return q
  }

  describe('initAnswersFromQuiz', () => {
    it('表单为空时初始化所有题目答案为空字符串', () => {
      const { answersForm, initAnswersFromQuiz } = createQuiz()
      initAnswersFromQuiz(execution.value.quiz)
      expect(answersForm).toEqual({ q1: '', q2: '' })
    })

    it('表单非空时跳过初始化（避免覆盖用户已填答案）', () => {
      const { answersForm, initAnswersFromQuiz } = createQuiz()
      answersForm.q1 = 'user answer'
      initAnswersFromQuiz(execution.value.quiz)
      expect(answersForm.q1).toBe('user answer')
      expect(answersForm.q2).toBeUndefined()
    })

    it('quiz 为空时安全跳过', () => {
      const { answersForm, initAnswersFromQuiz } = createQuiz()
      initAnswersFromQuiz(null)
      expect(Object.keys(answersForm)).toHaveLength(0)
    })
  })

  describe('resetAnswers', () => {
    it('清空所有答案', () => {
      const { answersForm, initAnswersFromQuiz, resetAnswers } = createQuiz()
      initAnswersFromQuiz(execution.value.quiz)
      expect(Object.keys(answersForm)).toHaveLength(2)
      resetAnswers()
      expect(Object.keys(answersForm)).toHaveLength(0)
    })
  })

  describe('submitAnswers', () => {
    it('存在未作答题目时提示并中止', async () => {
      const { answersForm, submitAnswers } = createQuiz()
      answersForm.q1 = ''
      answersForm.q2 = ''
      await submitAnswers()
      expect(mockElMessage.warning).toHaveBeenCalledWith('请完成所有题目')
      expect(mockSubmitAnswers).not.toHaveBeenCalled()
    })

    it('提交成功且无需重试时刷新文件与学习资料', async () => {
      mockSubmitAnswers.mockResolvedValue({
        data: { data: { score: 90, feedback: '优秀', should_retry: false } },
      })
      const { answersForm, initAnswersFromQuiz, submitAnswers } = createQuiz()
      initAnswersFromQuiz(execution.value.quiz)
      answersForm.q1 = 'A'
      answersForm.q2 = 'answer'
      await submitAnswers()

      expect(mockSubmitAnswers).toHaveBeenCalledWith('wf-1', answersForm)
      expect(execution.value.score).toBe(90)
      expect(execution.value.feedback).toBe('优秀')
      expect(mockElMessage.success).toHaveBeenCalledWith('答案已提交')
      await nextTick()
      expect(fileBrowserRef.value.loadFiles).toHaveBeenCalled()
      expect(autoLoadKeyFile).toHaveBeenCalled()
      expect(connectSSE).not.toHaveBeenCalled()
    })

    it('提交成功且需重试时清空答案、停止轮询、重连 SSE', async () => {
      mockSubmitAnswers.mockResolvedValue({
        data: { data: { should_retry: true } },
      })
      const { answersForm, initAnswersFromQuiz, submitAnswers } = createQuiz()
      initAnswersFromQuiz(execution.value.quiz)
      answersForm.q1 = 'A'
      answersForm.q2 = 'answer'
      await submitAnswers()

      expect(execution.value.should_retry).toBe(true)
      expect(Object.keys(answersForm)).toHaveLength(0)
      expect(stopPolling).toHaveBeenCalled()
      expect(connectSSE).toHaveBeenCalledWith('wf-1')
      expect(autoLoadKeyFile).not.toHaveBeenCalled()
    })

    it('提交失败时显示错误消息', async () => {
      mockSubmitAnswers.mockRejectedValue({
        response: { data: { message: '服务端校验失败' } },
      })
      const { answersForm, initAnswersFromQuiz, submitAnswers, isSubmitting } = createQuiz()
      initAnswersFromQuiz(execution.value.quiz)
      answersForm.q1 = 'A'
      answersForm.q2 = 'answer'
      await submitAnswers()

      expect(mockElMessage.error).toHaveBeenCalledWith('服务端校验失败')
      expect(isSubmitting.value).toBe(false)
    })

    it('提交失败且无 message 时使用默认错误文案', async () => {
      mockSubmitAnswers.mockRejectedValue(new Error('网络异常'))
      const { answersForm, initAnswersFromQuiz, submitAnswers } = createQuiz()
      initAnswersFromQuiz(execution.value.quiz)
      answersForm.q1 = 'A'
      answersForm.q2 = 'answer'
      await submitAnswers()

      expect(mockElMessage.error).toHaveBeenCalledWith('网络异常')
    })

    it('提交失败且无任何消息时使用兜底文案', async () => {
      mockSubmitAnswers.mockRejectedValue({})
      const { answersForm, initAnswersFromQuiz, submitAnswers } = createQuiz()
      initAnswersFromQuiz(execution.value.quiz)
      answersForm.q1 = 'A'
      answersForm.q2 = 'answer'
      await submitAnswers()

      expect(mockElMessage.error).toHaveBeenCalledWith('提交答案失败，请稍后重试')
    })

    it('提交过程中 isSubmitting 状态正确切换', async () => {
      mockSubmitAnswers.mockResolvedValue({ data: { data: { should_retry: false } } })
      const { answersForm, initAnswersFromQuiz, submitAnswers, isSubmitting } = createQuiz()
      initAnswersFromQuiz(execution.value.quiz)
      answersForm.q1 = 'A'
      answersForm.q2 = 'answer'
      expect(isSubmitting.value).toBe(false)
      const promise = submitAnswers()
      expect(isSubmitting.value).toBe(true)
      await promise
      expect(isSubmitting.value).toBe(false)
    })
  })

  describe('setBridge', () => {
    it('未设置 bridge 时 submitAnswers 重试分支安全跳过（不抛错）', async () => {
      mockSubmitAnswers.mockResolvedValue({ data: { data: { should_retry: true } } })
      const q = useWorkflowQuiz({ execution, fileBrowserRef })
      const { answersForm, initAnswersFromQuiz, submitAnswers } = q
      initAnswersFromQuiz(execution.value.quiz)
      answersForm.q1 = 'A'
      answersForm.q2 = 'answer'
      // 未调用 setBridge，bridge 函数均为 null
      await expect(submitAnswers()).resolves.toBeUndefined()
      // resetAnswers 仍执行
      expect(Object.keys(answersForm)).toHaveLength(0)
    })
  })
})
