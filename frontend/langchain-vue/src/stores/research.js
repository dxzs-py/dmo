import { defineStore } from 'pinia'
import { ref, markRaw, triggerRef } from 'vue'
import { getApprovalHistory } from '@/api/approval'
import { logger } from '@/utils/logger'
import { toCamelCase } from '@/utils/sessionTransformers'
import { approvalStateToToolStatus } from '@/utils/toolCallStateMachine'
import { ResearchTaskStatus } from '@/types'
import {
  addOrUpdateToolCallInMap,
  updateOrAddToolResultInMap,
  setApprovalToToolCallInMap,
  findToolCallInMap,
  findToolCallById,
  updateApprovalStateInMap,
  updateToolCallStatusInMap,
  flushPendingApprovalsInMap,
  isTerminalStatus,
  _mergeToolCalls,
  sortToolCallsForDisplay,
} from '@/utils/messageOperations'

/**
 * 将 Approval 记录转换为 toolCall 对象
 *
 * 工具调用数据的唯一持久化来源是 Approval 模型（原 ResearchTask.tool_calls 字段已在
 * migration 0009 中移除）。本函数将后端 ApprovalReadSerializer 返回的审批记录
 * 转换为前端 toolCall 结构，供 loadHistory 合并使用。
 *
 * 字段映射：
 * - extra.toolCallId / interrupt_id → toolCall.id / toolCallId
 * - tool_name → toolCall.name
 * - parameters → toolCall.parameters
 * - state → toolCall.status（统一走 approvalStateToToolStatus 近似映射，见 toolCallStateMachine.js；
 *   非终态审批 pending/waiting 映射为 pending/waiting，绝不映射为 running ——
 *   Task 7：修复"待审批/等待同批工具快照恢复后显示执行中"（P3-19 同类根因））
 * - 完整审批记录 → toolCall.approval（含 interrupt_id / state / operation 等）
 *
 * 注意：toolCall.result（工具执行输出）不在 Approval 中持久化，仅通过 SSE 流实时推送。
 * 刷新页面后 result 不可恢复，loadHistory 不填充此字段。
 *
 * @param {Object} approval - ApprovalReadSerializer 返回的审批记录
 * @returns {Object} toolCall 对象
 */
function _approvalToToolCall(approval) {
  if (!approval) return null
  const extra = (approval.extra && typeof approval.extra === 'object') ? approval.extra : {}
  const toolCallId = extra.toolCallId || approval.interruptId
  return {
    id: toolCallId,
    toolCallId: toolCallId,
    name: approval.toolName,
    toolName: approval.toolName,
    parameters: approval.parameters || {},
    status: approvalStateToToolStatus(approval.state),
    approval: {
      interruptId: approval.interruptId,
      source: approval.source,
      sourceId: approval.sourceId,
      state: approval.state,
      toolName: approval.toolName,
      title: approval.title,
      description: approval.description,
      action: approval.action,
      operation: approval.operation,
      parameters: approval.parameters,
      userInput: approval.userInput,
      createdAt: approval.createdAt,
      resolvedAt: approval.resolvedAt,
      ...extra,
    },
  }
}

/**
 * 任务状态终态集合（不可被非终态覆盖）。
 * 与 task.status 字段配合，防止后端滞后快照（running/pending）覆盖本地已完成状态。
 */
const TASK_TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled'])

/**
 * 深度研究统一状态 Pinia Store
 *
 * 作为深度研究模块的统一状态管理中心，承担两类数据：
 * 1. 工具调用状态（tasks Map）—— 替代组件级 useResearchToolCalls composable
 * 2. 任务状态（taskInfo Map）—— 替代 DeepResearchView 组件本地 `task` ref，
 *    使任务状态参与统一实时同步，所有模块（聊天模块深度研究模式 / 深度研究模块 /
 *    学习工作流）共享同一底层。
 *
 * 数据结构：
 *   tasks: Map<taskId, {
 *     toolCalls: ref([]),        // 工具调用数组（响应式）
 *     toolCallMap: ref(Map),     // 按 tool_call_id 索引的 Map
 *     pendingApprovals: ref(Map) // 待绑定审批队列
 *   }>
 *   taskInfo: Map<taskId, ref(Object)>  // 任务状态（响应式）
 *
 * 方法签名与 sessionStore 对齐，便于后续统一接入审批流。
 */
export const useResearchStore = defineStore('research', () => {
  /** 所有研究任务的工具调用状态（按 taskId 索引） */
  const tasks = ref(new Map())

  /**
   * 任务状态 Map（按 taskId 索引）
   *
   * 替代 DeepResearchView 组件本地 `task` ref，作为深度研究模块的统一任务状态源。
   * 由 setTaskStatus / updateTaskFromEvent 写入，由 DeepResearchView 通过
   * getTaskStatus（建议在 computed 中调用）读取，实现跨浏览器实时同步。
   *
   * 数据结构：Map<taskId, ref({taskId, status, finalReport, ...})>
   */
  const taskInfo = ref(new Map())

  // ==================== 内部辅助 ====================

  /**
   * 获取或创建指定 task 的数据结构
   * 使用 markRaw 包装 task 对象，避免 reactive 自动解包内部 ref，
   * 保留与原 composable 一致的 .value 访问方式
   * @param {string} taskId - 研究任务 ID
   * @returns {Object} task 数据对象 { toolCalls, toolCallMap, pendingApprovals }
   */
  const _ensureTask = (taskId) => {
    if (!tasks.value.has(taskId)) {
      tasks.value.set(taskId, markRaw({
        toolCalls: ref([]),
        toolCallMap: ref(new Map()),
        pendingApprovals: ref(new Map()),
      }))
    }
    return tasks.value.get(taskId)
  }

  /**
   * 同步 toolCallMap 到 toolCalls 数组（触发响应式更新）
   * 修改 toolCallMap 内对象属性后必须调用，确保 UI 刷新
   * @param {Object} task - task 数据对象
   */
  const _syncToolCalls = (task) => {
    // 跨浏览器统一排序：seq（(module, module_id) 内跨 LLM 轮次全局递增序号，
    // 事件透传）优先、_index（LLM 单轮序号）兜底。与 sessionStore 共用
    // sortToolCallsForDisplay（messageOperations.js 唯一权威排序实现），
    // 保证深度研究详情与聊天深度研究模式工具调用顺序跨浏览器一致
    sortToolCallsForDisplay(Array.from(task.toolCallMap.value.values()))
    task.toolCalls.value = Array.from(task.toolCallMap.value.values())
  }

  // ==================== 任务状态管理 ====================
  //
  // 替代 DeepResearchView 组件本地 `task` ref，作为深度研究模块统一任务状态源。
  // 所有模块（聊天模块深度研究模式 / 深度研究模块 / 学习工作流）共享同一底层，
  // 通过 setTaskStatus 写入，通过 getTaskStatus 读取，通过 updateTaskFromEvent
  // 接收 stream_completed 等实时事件。
  //
  // 状态优先级保护：终态（completed/failed/cancelled）不被非终态覆盖，
  // 防止后端滞后快照（running/pending）覆盖本地已完成状态。
  // 与 messageOperations.js 中 STATUS_PRIORITY 对 toolCall.status 的保护机制对齐。

  /**
   * 获取或创建指定 task 的状态 ref
   * @param {string} taskId - 研究任务 ID
   * @returns {import('vue').Ref<Object>|null} 任务状态 ref（task 不存在 taskId 时返回 null）
   */
  const _ensureTaskInfo = (taskId) => {
    if (!taskId) return null
    if (!taskInfo.value.has(taskId)) {
      taskInfo.value.set(taskId, ref(null))
    }
    return taskInfo.value.get(taskId)
  }

  /**
   * 合并式更新任务状态（替代 DeepResearchView 的 `task.value = { ...task.value, ...fresh }`）
   *
   * 状态优先级保护规则：
   * - 旧 status 是终态 + 新 status 是非终态 → 保留旧 status（防止滞后快照覆盖）
   * - 新 status 是终态 → 直接覆盖（终态是权威）
   * - 其他情况 → 用新 status 覆盖
   *
   * 其他字段（finalReport / currentStep / message 等）直接合并更新。
   *
   * @param {string} taskId - 研究任务 ID
   * @param {Object} fresh - 新的任务数据（部分字段即可，会与现有数据合并）
   * @param {Object} [options]
   * @param {boolean} [options.force=false] - 强制覆盖（跳过状态优先级保护）
   * @returns {Object|null} 更新后的任务状态（taskId 无效时返回 null）
   */
  const setTaskStatus = (taskId, fresh, options = {}) => {
    if (!taskId || !fresh || typeof fresh !== 'object') return null
    const taskRef = _ensureTaskInfo(taskId)
    if (!taskRef) return null

    const current = taskRef.value
    const force = options.force === true

    // 首次设置：直接写入
    if (!current) {
      taskRef.value = { ...fresh }
      logger.info(`[Research] setTaskStatus 首次设置: taskId=${taskId}, status=${fresh.status}`)
      return taskRef.value
    }

    // 状态优先级保护（非 force 模式）
    const newStatus = fresh.status
    const oldStatus = current.status
    if (!force && newStatus && oldStatus
        && TASK_TERMINAL_STATUSES.has(oldStatus)
        && !TASK_TERMINAL_STATUSES.has(newStatus)) {
      logger.warn(
        `[Research] setTaskStatus 状态保护: taskId=${taskId}, ` +
        `旧 status=${oldStatus}(终态) 不被新 status=${newStatus}(非终态) 覆盖`
      )
      // 保留旧 status，但其他字段可以合并
      const { status: _ignored, ...otherFields } = fresh
      taskRef.value = { ...current, ...otherFields, status: oldStatus }
      return taskRef.value
    }

    // 正常合并更新
    taskRef.value = { ...current, ...fresh }
    if (newStatus && newStatus !== oldStatus) {
      logger.info(
        `[Research] setTaskStatus 状态变更: taskId=${taskId}, ` +
        `oldStatus=${oldStatus}, newStatus=${newStatus}`
      )
    }
    return taskRef.value
  }

  /**
   * 获取指定 task 的状态（响应式，建议在 computed 中调用）
   *
   * 在 DeepResearchView 中使用示例：
   *   const currentTaskId = ref(null)
   *   const task = computed(() => researchStore.getTaskStatus(currentTaskId.value))
   *
   * @param {string} taskId - 研究任务 ID
   * @returns {Object|null} 任务状态对象（taskId 无效或未设置时返回 null）
   */
  const getTaskStatus = (taskId) => {
    if (!taskId) return null
    const taskRef = taskInfo.value.get(taskId)
    return taskRef?.value || null
  }

  /**
   * 从 stream_completed 实时事件更新任务状态
   *
   * 由 sync.js handleTaskEvent 在收到 stream_completed 事件时调用，
   * 用于跨浏览器同步任务完成/失败状态。
   *
   * 与 setTaskStatus 的区别：
   * - 此方法专用于 stream_completed 事件，使用 force=true 强制覆盖终态
   *   （stream_completed 是权威完成事件，必须更新）
   * - 自动从 payload 提取 status / finalReport / error 等字段
   *
   * @param {string} taskId - 研究任务 ID
   * @param {Object} payload - stream_completed 事件 payload
   * @param {boolean} [payload.success] - 是否成功
   * @param {string} [payload.finalReport] - 最终报告内容
   * @param {string} [payload.error] - 错误信息
   * @param {string} [payload.messageId] - 关联的 ChatMessage ID
   * @returns {Object|null} 更新后的任务状态
   */
  const updateTaskFromEvent = (taskId, payload) => {
    if (!taskId || !payload) return null
    const data = toCamelCase(payload)
    const success = data.success !== false
    const patch = {
      status: success ? ResearchTaskStatus.COMPLETED : ResearchTaskStatus.FAILED,
    }
    if (data.finalReport !== undefined) patch.finalReport = data.finalReport
    if (data.error) patch.error = data.error
    if (data.messageId) patch.messageId = data.messageId
    logger.info(
      `[Research] updateTaskFromEvent stream_completed: taskId=${taskId}, ` +
      `success=${success}, hasReport=${!!data.finalReport}, error=${data.error || ''}`
    )
    return setTaskStatus(taskId, patch, { force: true })
  }

  /**
   * 清理指定 task 的状态数据
   * @param {string} taskId - 研究任务 ID
   */
  const clearTaskInfo = (taskId) => {
    if (!taskId) return
    if (taskInfo.value.has(taskId)) {
      taskInfo.value.delete(taskId)
      logger.info(`[Research] 清理 taskInfo: taskId=${taskId}`)
    }
  }

  // ==================== 公共方法 ====================

  /**
   * 创建/更新 toolCall（对应 SSE tool 事件）
   *
   * 委托通用函数 addOrUpdateToolCallInMap 处理 toolCallMap 操作（含 isSynthetic 占位机制、
   * parameters 参数回查），保留 store 特定的 _syncToolCalls 和 flushPendingApprovals 调用。
   *
   * @param {string} taskId - 研究任务 ID
   * @param {Object} data - 工具事件数据
   */
  const addOrUpdateToolCall = (taskId, data) => {
    if (!taskId || !data) return
    const task = _ensureTask(taskId)
    const toolCallMap = task.toolCallMap.value

    const toolCallId = addOrUpdateToolCallInMap(toolCallMap, data)
    if (!toolCallId) return

    _syncToolCalls(task)
    // toolCall 创建后，检查是否有待绑定的审批（时序保护：审批事件先到达）
    flushPendingApprovals(taskId, toolCallId)
  }

  /**
   * 更新/添加工具结果（对应 SSE tool_result 事件）
   *
   * 委托通用函数 updateOrAddToolResultInMap 处理 toolCallMap 操作（含结果更新、parameters 补充），
   * 保留 store 特定的 _syncToolCalls 和 flushPendingApprovals 调用。
   *
   * @param {string} taskId - 研究任务 ID
   * @param {Object} data - 工具结果事件数据
   */
  const updateOrAddToolResult = (taskId, data) => {
    if (!taskId || !data) return
    const task = _ensureTask(taskId)
    const toolCallMap = task.toolCallMap.value

    const toolCallId = updateOrAddToolResultInMap(toolCallMap, data)
    if (!toolCallId) return

    // 仅当工具未进入终态时才刷新待绑审批（与 session.js 对齐：
    // 终态时 approval 已置 null，flush 会重设导致审批面板残留）
    const isTerminal = isTerminalStatus(data.status)
    if (!isTerminal) {
      flushPendingApprovals(taskId, toolCallId)
    }
    _syncToolCalls(task)
  }

  /**
   * 将审批数据附加到对应 toolCall 的 approval 字段
   *
   * 委托通用函数 setApprovalToToolCallInMap 处理 toolCallMap 操作（含匹配查找、isSynthetic 占位机制、
   * operation/command 参数回填），保留 store 特定的 _syncToolCalls 和 pendingApprovals 队列管理。
   *
   * @param {string} taskId - 研究任务 ID
   * @param {string} toolCallId - 工具调用 ID（interrupt_id 或 tool_call_id）
   * @param {Object} approvalData - 审批事件数据
   */
  const setApprovalToToolCall = (taskId, toolCallId, approvalData) => {
    if (!taskId || !toolCallId || !approvalData) return
    const task = _ensureTask(taskId)
    const toolCallMap = task.toolCallMap.value

    const isSynthetic = setApprovalToToolCallInMap(toolCallMap, toolCallId, approvalData)
    _syncToolCalls(task)

    // 如果创建了占位条目，同时存入 pendingApprovals 队列（兜底机制，确保后续 tool 事件能正确合并）
    if (isSynthetic) {
      const pendingIds = [toolCallId, approvalData.toolCallId].filter(Boolean)
      for (const pid of pendingIds) {
        task.pendingApprovals.value.set(pid, { approvalData, toolCallId })
      }
      logger.info(
        `[Research] 占位 toolCall 已加入 pendingApprovals 队列: taskId=${taskId}, toolCallId=${toolCallId}, pendingIds=${pendingIds}`
      )
    }
  }

  /**
   * 更新审批状态并联动 toolCall.status（审批通过/拒绝/超时时同步更新 status）
   *
   * 与 session.js 的 updateToolCallApprovalState 签名和语义完全对齐，
   * 供 approval.js 统一调用，确保代理模式和深度研究模块行为一致。
   *
   * @param {string} taskId - 研究任务 ID
   * @param {string} toolCallId - 工具调用 ID
   * @param {string} state - 新的 approval.state（如 'processing' / 'approved' / 'rejected' / 'timeout'）
   */
  const updateToolCallApprovalState = (taskId, toolCallId, state) => {
    if (!taskId || !toolCallId) return
    const task = tasks.value.get(taskId)
    if (!task) {
      logger.warn(`[Research] updateToolCallApprovalState: task 不存在, taskId=${taskId}`)
      return
    }
    const toolCallMap = task.toolCallMap.value
    // 1. 更新 approval.state（不修改 status，由 updateApprovalStateInMap 保证）
    const stateUpdated = updateApprovalStateInMap(toolCallMap, toolCallId, state)
    if (!stateUpdated) return
    triggerRef(task.toolCallMap)
    _syncToolCalls(task)
  }

  /**
   * 更新 toolCall 的 status（用于深度研究审批通过后设置 running 状态）
   *
   * 统一通过 toolCallMap 作为唯一真相源操作。Map 未命中时，先从 task.toolCalls
   * 数组填充到 Map，再通过 Map 函数更新，避免直接修改 toolCalls 数组导致的竞态问题。
   *
   * @param {string} taskId - 研究任务 ID
   * @param {string} toolCallId - 工具调用 ID
   * @param {string} status - 新的 toolCall.status
   * @returns {boolean} 是否成功更新
   */
  const updateToolCallStatus = (taskId, toolCallId, status) => {
    const task = tasks.value.get(taskId)
    if (!task) {
      logger.warn(`[Research] updateToolCallStatus: task 不存在, taskId=${taskId}`)
      return false
    }
    const toolCallMap = task.toolCallMap.value

    // 尝试从 Map 查找
    let { toolCall: target } = findToolCallInMap(toolCallMap, toolCallId)

    // Map 未命中：从 task.toolCalls 数组填充到 Map（单向数据流：数组 → Map）
    if (!target) {
      const toolCallsArr = task.toolCalls.value
      if (toolCallsArr && Array.isArray(toolCallsArr)) {
        const tc = findToolCallById(toolCallsArr, toolCallId, { skipApproved: false })
        if (tc) {
          const writeKey = tc.id || tc.toolCallId || toolCallId
          toolCallMap.set(writeKey, tc)
          triggerRef(task.toolCallMap)
          target = tc
          logger.info(
            `[Research] updateToolCallStatus: Map 未命中，从 toolCalls 数组填充到 Map: ` +
            `taskId=${taskId}, toolCallId=${toolCallId}`
          )
        }
      }
    }

    if (!target) {
      logger.warn(
        `[Research] updateToolCallStatus: 未找到 toolCall, taskId=${taskId}, ` +
        `toolCallId=${toolCallId}, status=${status}`
      )
      return false
    }

    const oldStatus = target.status
    const toolName = target.name || target.toolName

    // 统一通过 Map 函数更新 status
    updateToolCallStatusInMap(toolCallMap, toolCallId, status)

    _syncToolCalls(task)
    logger.debug(
      `[Research] updateToolCallStatus: taskId=${taskId}, toolCallId=${toolCallId}, ` +
      `oldStatus=${oldStatus}, newStatus=${status}, toolName=${toolName}`
    )
    return true
  }

  /**
   * 获取指定 task 的工具调用数组（响应式）
   *
   * 建议在 computed 或模板中使用以获得响应式更新：
   *   const toolCalls = computed(() => researchStore.getToolCalls(taskId))
   *
   * @param {string} taskId - 研究任务 ID
   * @returns {Array} 工具调用数组（task 不存在时返回空数组）
   */
  const getToolCalls = (taskId) => {
    const task = tasks.value.get(taskId)
    return task ? task.toolCalls.value : []
  }

  /**
   * 从后端拉取工具调用历史
   *
   * 工具调用数据的唯一持久化来源是 Approval 模型（source='deep_research', source_id=taskId）。
   * 调用统一审批 API getApprovalHistory 查询，通过 _approvalToToolCall 转换为 toolCall 结构，
   * 再与本地实时同步数据增量合并。
   *
   * 合并策略与 sessionStore.loadSessionDetail 一致：使用 _mergeToolCalls 增量合并，
   * 保留本地审批中间状态（pending/processing/waiting）和 tool status，避免刷新时丢失实时同步数据。
   *
   * @param {string} taskId - 研究任务 ID
   */
  const loadHistory = async (taskId) => {
    if (!taskId) {
      logger.warn('[Research] loadHistory 无 taskId')
      return
    }
    const task = _ensureTask(taskId)
    try {
      const response = await getApprovalHistory(taskId, { source: 'deep_research' })
      const approvalList = response.data?.data || []
      // Approval 记录转换为 toolCall 对象
      const backendList = approvalList
        .map(_approvalToToolCall)
        .filter(Boolean)

      // 合并策略：使用 _mergeToolCalls 增量合并（而非全量替换），保留本地审批中间状态
      // （pending/processing/waiting）和 tool status，避免刷新时丢失实时同步数据。
      // 与 sessionStore.loadSessionDetail 行为一致：所有模块刷新时统一通过合并而非替换。
      const existingList = task.toolCalls.value || []
      const mergedList = _mergeToolCalls(existingList, backendList)

      // 同步更新 toolCallMap：重建 Map 以确保一致性
      const mergedMap = new Map(mergedList.map(tc => [tc.id || tc.toolCallId, tc]))
      // 保留 Map 中已有但 mergedList 中不存在的条目（WebSocket 事件写入的高优先级数据）
      for (const [key, value] of task.toolCallMap.value.entries()) {
        if (!mergedMap.has(key)) {
          mergedMap.set(key, value)
        }
      }

      task.toolCallMap.value = mergedMap
      task.toolCalls.value = mergedList
      logger.info(`[Research] 加载工具调用历史(合并): 本地=${existingList.length}, 后端=${backendList.length}, 合并后=${mergedList.length}, taskId=${taskId}`)
    } catch (e) {
      logger.warn(`[Research] 加载工具调用历史失败: taskId=${taskId}`, e)
      // 空值兜底：失败时清空，不报错
      task.toolCalls.value = []
      task.toolCallMap.value = new Map()
    }
  }

  /**
   * 清理指定 task 的全部数据（工具调用 + 任务状态）
   * @param {string} taskId - 研究任务 ID
   */
  const clearTask = (taskId) => {
    const task = tasks.value.get(taskId)
    if (!task) return
    task.toolCalls.value = []
    task.toolCallMap.value = new Map()
    task.pendingApprovals.value = new Map()
    tasks.value.delete(taskId)
    // 同步清理任务状态（统一底层：taskInfo 与 tasks 生命周期一致）
    clearTaskInfo(taskId)
    logger.info(`[Research] 清理 task 数据: taskId=${taskId}`)
  }

  /**
   * 刷新待绑定的审批数据，将其附加到对应 toolCall
   *
   * 在 addOrUpdateToolCall / updateOrAddToolResult 创建/更新 toolCall 后调用，
   * 处理审批事件先于 tool 事件到达的时序场景：
   * 1. 从 pendingApprovals Map 中查找匹配的审批
   * 2. 从队列移除后调用 setApprovalToToolCall 绑定（此时 toolCallMap 中已有目标条目）
   *
   * @param {string} taskId - 研究任务 ID
   * @param {string} toolCallId - 工具调用 ID
   */
  const flushPendingApprovals = (taskId, toolCallId) => {
    if (!taskId || !toolCallId) return
    const task = tasks.value.get(taskId)
    if (!task) return
    const didBind = flushPendingApprovalsInMap(
      task.pendingApprovals.value,
      task.toolCallMap.value,
      toolCallId
    )
    if (didBind) {
      _syncToolCalls(task)
      logger.info(`[Research] pending 审批已绑定: taskId=${taskId}, toolCallId=${toolCallId}`)
    }
  }

  /**
   * 设置任务推理内容（来自 stream_reasoning WebSocket 事件）
   *
   * 后端 writeback.py 广播的 payload 结构（toCamelCase 后）：
   * { source, sourceId, messageId, sessionId, taskId, data: { content } }
   * content 位于 payload.data.content；推理中状态由 task.status 驱动
   * （ResearchTaskDetail :is-streaming="status === RUNNING/PENDING"），
   * 此处不写 duration=0，避免任务完成后仍显示"正在思考"。
   *
   * @param {string} taskId - 研究任务 ID
   * @param {Object} payload - stream_reasoning 事件 payload
   */
  const setTaskReasoning = (taskId, payload) => {
    if (!taskId || !payload) return
    const taskRef = _ensureTaskInfo(taskId)
    if (!taskRef) return
    const current = taskRef.value || {}
    const reasoning = {
      content: payload.data?.content || payload.content || '',
      source: payload.source || 'deep_thinking',
      ...(payload.duration !== undefined && { duration: payload.duration }),
    }
    taskRef.value = { ...current, reasoning }
  }

  return {
    tasks,
    taskInfo,
    addOrUpdateToolCall,
    updateOrAddToolResult,
    setApprovalToToolCall,
    updateToolCallApprovalState,     // 与 session.js 对齐：审批状态 + toolCall.status 联动更新
    updateToolCallStatus,
    getToolCalls,
    loadHistory,
    clearTask,
    flushPendingApprovals,
    // 任务状态管理（统一底层：替代 DeepResearchView 本地 task ref）
    setTaskStatus,
    getTaskStatus,
    updateTaskFromEvent,
    setTaskReasoning,
    clearTaskInfo,
  }
})
