/**
 * 任务状态 → 文案 / 标签色映射（单一权威）
 *
 * 收敛 TaskList.vue / ResearchTaskDetail.vue / AiNode.vue 三处重复的
 * getStatusType / getStatusText 映射，避免文案与标签色在不同组件间漂移。
 *
 * 覆盖两类状态域：
 *   - ResearchTaskStatus：深度研究任务状态（pending/awaiting_approval/running/completed/failed）
 *   - LearningStep：学习工作流阶段（planner/retrieval/quiz_generator/...）
 *
 * 合并查找无键名冲突（如 completed/failed 在两类域中映射到同一文案/颜色），
 * 故提供 getTaskStatusText / getTaskStatusTagType 两个统一入口。
 */
import { ResearchTaskStatus, LearningStep } from '../types/index.js'

/** 深度研究任务状态 → 中文文案 */
export const RESEARCH_TASK_STATUS_TEXT = {
  [ResearchTaskStatus.PENDING]: '待执行',
  [ResearchTaskStatus.AWAITING_APPROVAL]: '等待审批',
  [ResearchTaskStatus.RUNNING]: '执行中',
  [ResearchTaskStatus.COMPLETED]: '已完成',
  [ResearchTaskStatus.FAILED]: '失败',
}

/** 深度研究任务状态 → Element Plus tag type */
export const RESEARCH_TASK_STATUS_TAG_TYPE = {
  [ResearchTaskStatus.PENDING]: 'info',
  [ResearchTaskStatus.AWAITING_APPROVAL]: 'warning',
  [ResearchTaskStatus.RUNNING]: 'warning',
  [ResearchTaskStatus.COMPLETED]: 'success',
  [ResearchTaskStatus.FAILED]: 'danger',
}

/** 学习工作流阶段 → 中文文案 */
export const LEARNING_STEP_TEXT = {
  [LearningStep.WAITING_FOR_ANSWERS]: '等待答题',
  [LearningStep.PLANNER]: '生成计划',
  [LearningStep.RETRIEVAL]: '检索资料',
  [LearningStep.QUIZ_GENERATOR]: '生成题目',
  [LearningStep.GRADING]: '评分中',
  [LearningStep.FEEDBACK]: '生成反馈',
  [LearningStep.END]: '已结束',
}

/** 学习工作流阶段 → Element Plus tag type */
export const LEARNING_STEP_TAG_TYPE = {
  [LearningStep.WAITING_FOR_ANSWERS]: 'warning',
  [LearningStep.PLANNER]: 'primary',
  [LearningStep.RETRIEVAL]: 'primary',
  [LearningStep.QUIZ_GENERATOR]: 'warning',
  [LearningStep.GRADING]: 'primary',
  [LearningStep.FEEDBACK]: 'success',
  [LearningStep.END]: 'success',
}

const TASK_STATUS_TEXT = {
  ...RESEARCH_TASK_STATUS_TEXT,
  ...LEARNING_STEP_TEXT,
}

const TASK_STATUS_TAG_TYPE = {
  ...RESEARCH_TASK_STATUS_TAG_TYPE,
  ...LEARNING_STEP_TAG_TYPE,
}

/**
 * 任务/阶段状态 → 中文文案（深度研究 + 学习工作流统一查找）
 * @param {string} status
 * @returns {string}
 */
export const getTaskStatusText = (status) => TASK_STATUS_TEXT[status] || status || ''

/**
 * 任务/阶段状态 → Element Plus tag type
 * @param {string} status
 * @returns {string}
 */
export const getTaskStatusTagType = (status) => TASK_STATUS_TAG_TYPE[status] || 'info'
