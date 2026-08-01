import { describe, it, expect } from 'vitest'
import { ref, nextTick } from 'vue'
import { useWorkflowSteps } from '../useWorkflowSteps'

describe('useWorkflowSteps', () => {
  function createSteps(currentStep = '') {
    const execution = ref(currentStep ? { current_step: currentStep } : null)
    return { execution, ...useWorkflowSteps({ execution }) }
  }

  it('workflowSteps 包含 8 个固定步骤', () => {
    const { workflowSteps } = createSteps()
    expect(workflowSteps).toHaveLength(8)
    expect(workflowSteps.map(s => s.key)).toEqual([
      'start', 'planner', 'retrieval', 'quiz_generator',
      'waiting_for_answers', 'grading', 'feedback', 'end',
    ])
  })

  it('stepOrder 与 workflowSteps 顺序一致', () => {
    const { workflowSteps, stepOrder } = createSteps()
    expect(stepOrder).toEqual(workflowSteps.map(s => s.key))
  })

  it('completedSteps：execution 为 null 时返回空数组', () => {
    const { completedSteps } = createSteps()
    expect(completedSteps.value).toEqual([])
  })

  it('completedSteps：current_step 不在 stepOrder 中时返回空数组', () => {
    const { completedSteps } = createSteps('unknown_step')
    expect(completedSteps.value).toEqual([])
  })

  it('completedSteps：返回 current_step 之前的所有步骤（不含当前步骤）', () => {
    const { completedSteps } = createSteps('grading')
    expect(completedSteps.value).toEqual([
      'start', 'planner', 'retrieval', 'quiz_generator', 'waiting_for_answers',
    ])
  })

  it('completedSteps：current_step 为首个步骤时返回空数组', () => {
    const { completedSteps } = createSteps('start')
    expect(completedSteps.value).toEqual([])
  })

  it('completedSteps 响应式：current_step 变化时自动更新', async () => {
    const { execution, completedSteps } = createSteps('planner')
    expect(completedSteps.value).toEqual(['start'])
    execution.value.current_step = 'feedback'
    await nextTick()
    expect(completedSteps.value).toEqual([
      'start', 'planner', 'retrieval', 'quiz_generator', 'waiting_for_answers', 'grading',
    ])
  })

  it('getStepType 返回步骤对应的 el-tag type', () => {
    const { getStepType } = createSteps()
    expect(getStepType('start')).toBe('info')
    expect(getStepType('planner')).toBe('primary')
    expect(getStepType('quiz_generator')).toBe('warning')
    expect(getStepType('waiting_for_answers')).toBe('warning')
    expect(getStepType('feedback')).toBe('success')
    expect(getStepType('end')).toBe('success')
    expect(getStepType('completed')).toBe('success')
  })

  it('getStepType 未知步骤返回 info', () => {
    const { getStepType } = createSteps()
    expect(getStepType('unknown')).toBe('info')
  })

  it('getStepText 返回步骤中文文案', () => {
    const { getStepText } = createSteps()
    expect(getStepText('planner')).toBe('生成学习计划')
    expect(getStepText('retrieval')).toBe('检索资料')
    expect(getStepText('grading')).toBe('评分中')
    expect(getStepText('feedback_completed')).toBe('工作流已完成')
  })

  it('getStepText 未知步骤返回原值', () => {
    const { getStepText } = createSteps()
    expect(getStepText('custom_step')).toBe('custom_step')
  })

  it('getQuestionTypeText 返回题目类型中文文案', () => {
    const { getQuestionTypeText } = createSteps()
    expect(getQuestionTypeText('multiple_choice')).toBe('选择题')
    expect(getQuestionTypeText('fill_blank')).toBe('填空题')
    expect(getQuestionTypeText('short_answer')).toBe('简答题')
  })

  it('getQuestionTypeText 未知类型返回原值', () => {
    const { getQuestionTypeText } = createSteps()
    expect(getQuestionTypeText('essay')).toBe('essay')
  })
})
