import { computed } from 'vue'

/**
 * 学习工作流步骤可视化 composable
 *
 * 封装工作流步骤定义与状态映射：
 *   - workflowSteps / stepOrder：8 个固定步骤（start → planner → retrieval → quizGenerator
 *     → waitingForAnswers → grading → feedback → end）及其顺序
 *   - completedSteps：基于 execution.current_step 计算已完成步骤（current_step 之前的所有步骤）
 *   - getStepType / getStepText：步骤 → el-tag type / 中文文案 映射
 *   - getQuestionTypeText：题目类型 → 中文文案映射
 *
 * @param {Object} deps
 * @param {import('vue').Ref<Object|null>} deps.execution - 当前工作流执行状态 ref（读取 current_step）
 * @returns {{
 *   workflowSteps: Array<{ key: string, label: string }>,
 *   stepOrder: string[],
 *   completedSteps: import('vue').ComputedRef<string[]>,
 *   getStepType: (step: string) => string,
 *   getStepText: (step: string) => string,
 *   getQuestionTypeText: (type: string) => string,
 * }}
 */
export function useWorkflowSteps({ execution }) {
  const workflowSteps = [
    { key: 'start', label: '启动' },
    { key: 'planner', label: '规划' },
    { key: 'retrieval', label: '检索' },
    { key: 'quizGenerator', label: '出题' },
    { key: 'waitingForAnswers', label: '答题' },
    { key: 'grading', label: '评分' },
    { key: 'feedback', label: '反馈' },
    { key: 'end', label: '完成' },
  ]

  const stepOrder = workflowSteps.map(s => s.key)

  /** current_step 之前的所有步骤（不含当前步骤） */
  const completedSteps = computed(() => {
    if (!execution.value) return []
    const currentIdx = stepOrder.indexOf(execution.value.currentStep)
    if (currentIdx < 0) return []
    return stepOrder.slice(0, currentIdx)
  })

  /** 步骤 → el-tag type 映射（用于步骤标签与节点着色） */
  const getStepType = (step) => {
    const typeMap = {
      start: 'info',
      planner: 'primary',
      retrieval: 'primary',
      quizGenerator: 'warning',
      waitingForAnswers: 'warning',
      grading: 'primary',
      feedback: 'success',
      feedbackCompleted: 'success',
      end: 'success',
      completed: 'success',
    }
    return typeMap[step] || 'info'
  }

  /** 步骤 → 中文文案映射 */
  const getStepText = (step) => {
    const textMap = {
      start: '准备中',
      planner: '生成学习计划',
      retrieval: '检索资料',
      quizGenerator: '生成练习题',
      waitingForAnswers: '等待答题',
      grading: '评分中',
      feedback: '生成反馈',
      feedbackCompleted: '工作流已完成',
      end: '已结束',
      completed: '已完成',
    }
    return textMap[step] || step
  }

  /** 题目类型 → 中文文案映射 */
  const getQuestionTypeText = (type) => {
    const map = {
      multiple_choice: '选择题',
      fill_blank: '填空题',
      short_answer: '简答题',
    }
    return map[type] || type
  }

  return {
    workflowSteps,
    stepOrder,
    completedSteps,
    getStepType,
    getStepText,
    getQuestionTypeText,
  }
}
