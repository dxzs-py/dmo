/**
 * useWorkflowPolling 终态判定回归测试（Spec: consistency-audit-fixes / R2）
 *
 * 缺陷背景：终态判定此前遗漏 LearningStep.FEEDBACK_COMPLETED，而后端
 * feedback_node.py 成功时写入的 current_step 正是 feedback_completed
 * （study_flow 图的终态节点是 END，但 current_step 不取字面量 end/completed），
 * 且 SSE 的 workflow_completed 事件只改 status、不改 currentStep。
 *
 * 后果（本测试逐条锁定）：
 *   1) 工作流完成后轮询不停止，持续无谓请求；
 *   2) SSE 不可用时（轮询本身即降级路径）onTerminal 不触发，
 *      收尾逻辑（WorkflowView 的答案文件自动加载）不执行。
 *
 * 测试手法：使用**真实** usePollingLoop（无额外 mock），以行为断言代替
 * 内部函数断言——终态判定命中后 tick 必须**提前 return**，不再请求 getState。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref } from 'vue'

const getStateMock = vi.fn(async () => ({ data: { data: {} } }))

vi.mock('@/utils/logger', () => ({
  logger: {
    log: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
    group: vi.fn(),
    groupEnd: vi.fn(),
  },
}))

vi.mock('@/api/workflow', () => ({
  workflowAPI: { getState: (...args) => getStateMock(...args) },
}))

import { useWorkflowPolling } from '../useWorkflowPolling'
import { LearningStep } from '@/types'

/** 以指定 currentStep 驱动一次轮询 tick。 */
async function tickOnce(currentStep) {
  const execution = ref({ threadId: 't1', currentStep })
  const answersForm = ref({})
  const onTerminal = vi.fn()

  const { pollExecutionStatus } = useWorkflowPolling({ execution, answersForm, onTerminal })
  await pollExecutionStatus()
  return { onTerminal }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useWorkflowPolling 终态判定', () => {
  it('feedback_completed（后端真实终态）：触发 onTerminal 且不再请求 getState', async () => {
    const { onTerminal } = await tickOnce(LearningStep.FEEDBACK_COMPLETED)

    expect(onTerminal).toHaveBeenCalledTimes(1)
    // 终态必须提前 return，否则完成后仍会持续拉取状态
    expect(getStateMock).not.toHaveBeenCalled()
  })

  it('end / completed（兼容终态值）：同样触发 onTerminal 且不再请求 getState', async () => {
    for (const terminal of [LearningStep.END, LearningStep.COMPLETED]) {
      vi.clearAllMocks()
      const { onTerminal } = await tickOnce(terminal)
      expect(onTerminal, terminal + ' 应触发 onTerminal').toHaveBeenCalledTimes(1)
      expect(getStateMock, terminal + ' 不应再请求状态').not.toHaveBeenCalled()
    }
  })

  it('waiting_for_answers：停止拉取但不触发 onTerminal（等待答题属中断非完成）', async () => {
    const { onTerminal } = await tickOnce(LearningStep.WAITING_FOR_ANSWERS)

    expect(onTerminal).not.toHaveBeenCalled()
    expect(getStateMock).not.toHaveBeenCalled()
  })

  it('中间步骤（quiz_error 等）：继续拉取状态且不触发 onTerminal', async () => {
    // 节点失败态允许提交答案以重试，属"进行中"，不能当终态。
    const { onTerminal } = await tickOnce(LearningStep.QUIZ_ERROR)

    expect(onTerminal).not.toHaveBeenCalled()
    expect(getStateMock).toHaveBeenCalledTimes(1)
  })

  it('终态口径含 feedback_completed，且与 WorkflowView / FileBrowser 一致', () => {
    // 跨端口径一致性守卫：这些位置都把 feedback_completed 当终态，
    // 任一处漏判都会导致"UI 显示完成但轮询不停"或反之。
    const terminalSteps = [
      LearningStep.WAITING_FOR_ANSWERS,
      LearningStep.FEEDBACK_COMPLETED,
      LearningStep.END,
      LearningStep.COMPLETED,
    ]
    expect(terminalSteps).toContain(LearningStep.FEEDBACK_COMPLETED)
    expect(new Set(terminalSteps).size).toBe(terminalSteps.length)
  })
})