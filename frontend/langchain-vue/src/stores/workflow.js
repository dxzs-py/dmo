import { defineStore } from 'pinia'
import { ref } from 'vue'
import { toCamelCase } from '@/utils/sessionTransformers'
import { logger } from '@/utils/logger'

/**
 * 学习工作流状态终态集合（不可被非终态覆盖）。
 * 与 execution.status 字段配合，防止后端滞后快照（running/pending）覆盖本地已完成状态。
 */
const WORKFLOW_TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled'])

/**
 * 学习工作流统一状态 Pinia Store
 *
 * 作为学习工作流（learning 模块）的统一状态管理中心，承担跨浏览器实时同步职责：
 * - 由 sync.js 的 onWorkflowEvent 回调写入（源自 task WebSocket 频道的 4 个 workflow_* 事件）
 * - 由 WorkflowView 通过 getWorkflowState（在 computed 中调用）读取，
 *   实现 WebSocket 事件驱动的 UI 更新
 *
 * 数据结构：Map<taskId, ref(Object)>  // 按 thread_id 索引的工作流状态
 *
 * 与 research.js 的 taskInfo 设计对齐：
 * - 状态优先级保护：终态（completed/failed/cancelled）不被非终态覆盖
 * - updateWorkflowFromEvent 使用 force=true 写入终态（权威完成事件）
 *
 * 与 SSE 的关系：
 * - SSE 是请求浏览器独占的流式数据源，WorkflowView 仍直接更新 execution.value
 * - WebSocket 通过本 store 同步给所有浏览器（含请求浏览器），WorkflowView watch 本 store 后合并到 execution.value
 * - 双路径幂等：状态字段（current_step/status 等）以最新事件为准，重复更新结果一致
 */
export const useWorkflowStore = defineStore('workflow', () => {
  /** 工作流状态 Map（按 thread_id 索引） */
  const workflows = ref(new Map())

  // ==================== 内部辅助 ====================

  /**
   * 获取或创建指定工作流的状态 ref
   * @param {string} taskId - 工作流 thread_id
   * @returns {import('vue').Ref<Object>|null} 工作流状态 ref（taskId 无效时返回 null）
   */
  const _ensureWorkflow = (taskId) => {
    if (!taskId) return null
    if (!workflows.value.has(taskId)) {
      workflows.value.set(taskId, ref(null))
    }
    return workflows.value.get(taskId)
  }

  // ==================== 状态管理 ====================

  /**
   * 合并式更新工作流状态
   *
   * 状态优先级保护规则：
   * - 旧 status 是终态 + 新 status 是非终态 → 保留旧 status（防止滞后快照覆盖）
   * - 新 status 是终态 → 直接覆盖（终态是权威）
   * - 其他情况 → 用新 status 覆盖
   *
   * 其他字段（current_step / learning_plan / quiz 等）直接合并更新。
   *
   * @param {string} taskId - 工作流 thread_id
   * @param {Object} fresh - 新的工作流数据（部分字段即可，会与现有数据合并）
   * @param {Object} [options]
   * @param {boolean} [options.force=false] - 强制覆盖（跳过状态优先级保护）
   * @returns {Object|null} 更新后的工作流状态（taskId 无效时返回 null）
   */
  const setWorkflowState = (taskId, fresh, options = {}) => {
    if (!taskId || !fresh || typeof fresh !== 'object') return null
    const wfRef = _ensureWorkflow(taskId)
    if (!wfRef) return null

    const current = wfRef.value
    const force = options.force === true

    // 首次设置：直接写入
    if (!current) {
      wfRef.value = { ...fresh }
      logger.info(`[Workflow] setWorkflowState 首次设置: taskId=${taskId}, status=${fresh.status || 'unknown'}`)
      return wfRef.value
    }

    // 状态优先级保护（非 force 模式）
    const newStatus = fresh.status
    const oldStatus = current.status
    if (!force && newStatus && oldStatus
        && WORKFLOW_TERMINAL_STATUSES.has(oldStatus)
        && !WORKFLOW_TERMINAL_STATUSES.has(newStatus)) {
      logger.warn(
        `[Workflow] setWorkflowState 状态保护: taskId=${taskId}, ` +
        `旧 status=${oldStatus}(终态) 不被新 status=${newStatus}(非终态) 覆盖`
      )
      // 保留旧 status，但其他字段可以合并
      const { status: _ignored, ...otherFields } = fresh
      wfRef.value = { ...current, ...otherFields, status: oldStatus }
      return wfRef.value
    }

    // 正常合并更新
    wfRef.value = { ...current, ...fresh }
    if (newStatus && newStatus !== oldStatus) {
      logger.info(
        `[Workflow] setWorkflowState 状态变更: taskId=${taskId}, ` +
        `oldStatus=${oldStatus}, newStatus=${newStatus}`
      )
    }
    return wfRef.value
  }

  /**
   * 获取指定工作流的状态（响应式，建议在 computed 中调用）
   *
   * 在 WorkflowView 中使用示例：
   *   const workflowState = computed(() => workflowStore.getWorkflowState(execution.value?.thread_id))
   *
   * @param {string} taskId - 工作流 thread_id
   * @returns {Object|null} 工作流状态对象（taskId 无效或未设置时返回 null）
   */
  const getWorkflowState = (taskId) => {
    if (!taskId) return null
    const wfRef = workflows.value.get(taskId)
    return wfRef?.value || null
  }

  /**
   * 从 WebSocket 事件更新工作流状态
   *
   * 由 sync.js handleTaskEvent 在收到 workflow_* 事件时通过 onWorkflowEvent 回调调用，
   * 用于跨浏览器同步工作流步骤/状态/完成/失败事件。
   *
   * 事件类型处理：
   * - workflow_step：更新 current_step 和 step_message（步骤进度）
   * - workflow_state_update：更新 status / current_step / learning_plan / quiz 等字段（状态变更）
   * - workflow_completed：标记为 completed 终态（force=true，权威完成事件）
   * - workflow_failed：标记为 failed 终态（force=true，权威失败事件）
   *
   * @param {string} eventType - 事件类型（workflow_step / workflow_state_update / workflow_completed / workflow_failed）
   * @param {Object} payload - 事件 payload
   * @param {string} [payload.step] - 当前步骤（workflow_step）
   * @param {string} [payload.message] - 步骤消息（workflow_step）
   * @param {string} [payload.state] - 工作流状态（workflow_state_update）
   * @param {string} [payload.current_step] - 当前步骤（workflow_state_update）
   * @param {Object} [payload.learning_plan] - 学习计划
   * @param {Array} [payload.retrieved_docs] - 检索文档
   * @param {Object} [payload.quiz] - 练习题
   * @param {number} [payload.score] - 分数
   * @param {string} [payload.feedback] - 反馈
   * @param {boolean} [payload.should_retry] - 是否需要重试
   * @param {string} [payload.error] - 错误信息
   * @param {string} taskId - 工作流 thread_id
   * @returns {Object|null} 更新后的工作流状态
   */
  const updateWorkflowFromEvent = (eventType, payload, taskId) => {
    if (!taskId || !payload) return null
    const data = toCamelCase(payload)
    const patch = {}

    switch (eventType) {
      case 'workflow_step':
        // 学习工作流节点执行进度
        if (data.step) patch.currentStep = data.step
        if (data.message !== undefined) patch.stepMessage = data.message
        break
      case 'workflow_state_update':
        // 学习工作流状态变更（如 waiting_for_answers / completed）
        if (data.state) patch.status = data.state
        if (data.currentStep) patch.currentStep = data.currentStep
        if (data.learningPlan) patch.learningPlan = data.learningPlan
        if (data.retrievedDocs) patch.retrievedDocs = data.retrievedDocs
        if (data.quiz) patch.quiz = data.quiz
        if (data.score !== undefined) patch.score = data.score
        if (data.feedback !== undefined) patch.feedback = data.feedback
        if (data.shouldRetry !== undefined) patch.shouldRetry = data.shouldRetry
        break
      case 'workflow_completed':
        // 学习工作流完成（终态，force=true 强制覆盖）
        patch.status = 'completed'
        if (data.currentStep) patch.currentStep = data.currentStep
        if (data.learningPlan) patch.learningPlan = data.learningPlan
        if (data.quiz) patch.quiz = data.quiz
        if (data.score !== undefined) patch.score = data.score
        if (data.feedback !== undefined) patch.feedback = data.feedback
        if (data.shouldRetry !== undefined) patch.shouldRetry = data.shouldRetry
        break
      case 'workflow_failed':
        // 学习工作流失败（终态，force=true 强制覆盖）
        patch.status = 'failed'
        if (data.error) patch.error = data.error
        if (data.message) patch.errorMessage = data.message
        break
      default:
        logger.warn(`[Workflow] updateWorkflowFromEvent 未处理的事件类型: ${eventType}`)
        return null
    }

    const force = eventType === 'workflow_completed' || eventType === 'workflow_failed'
    logger.info(
      `[Workflow] updateWorkflowFromEvent: taskId=${taskId}, type=${eventType}, ` +
      `patchKeys=${Object.keys(patch).join(',')}, force=${force}`
    )
    return setWorkflowState(taskId, patch, { force })
  }

  /**
   * 清理指定工作流的状态数据
   * @param {string} taskId - 工作流 thread_id
   */
  const clearWorkflowState = (taskId) => {
    if (!taskId) return
    if (workflows.value.has(taskId)) {
      workflows.value.delete(taskId)
      logger.info(`[Workflow] 清理 workflow 状态: taskId=${taskId}`)
    }
  }

  return {
    workflows,
    setWorkflowState,
    getWorkflowState,
    updateWorkflowFromEvent,
    clearWorkflowState,
  }
})
