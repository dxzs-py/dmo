import { defineStore } from 'pinia'
import { ref, markRaw, triggerRef } from 'vue'
import { getResearchToolCalls } from '@/api/research'
import { logger } from '@/utils/logger'
import {
  addOrUpdateToolCallInMap,
  updateOrAddToolResultInMap,
  setApprovalToToolCallInMap,
  findToolCallInMap,
  findToolCallById,
  updateApprovalStateInMap,
  updateToolCallStatusInMap,
  _mergeToolCalls,
} from '@/utils/message-operations'

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
   * 数据结构：Map<taskId, ref({task_id, status, final_report, ...})>
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
  // 与 message-operations.js 中 STATUS_PRIORITY 对 toolCall.status 的保护机制对齐。

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
   * 其他字段（final_report / current_step / message 等）直接合并更新。
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
   * - 自动从 payload 提取 status / final_report / error 等字段
   *
   * @param {string} taskId - 研究任务 ID
   * @param {Object} payload - stream_completed 事件 payload
   * @param {boolean} [payload.success] - 是否成功
   * @param {string} [payload.final_report] - 最终报告内容
   * @param {string} [payload.error] - 错误信息
   * @param {string} [payload.message_id] - 关联的 ChatMessage ID
   * @returns {Object|null} 更新后的任务状态
   */
  const updateTaskFromEvent = (taskId, payload) => {
    if (!taskId || !payload) return null
    const success = payload.success !== false
    const patch = {
      status: success ? 'completed' : 'failed',
    }
    if (payload.final_report !== undefined) patch.final_report = payload.final_report
    if (payload.error) patch.error = payload.error
    if (payload.message_id) patch.message_id = payload.message_id
    logger.info(
      `[Research] updateTaskFromEvent stream_completed: taskId=${taskId}, ` +
      `success=${success}, hasReport=${!!payload.final_report}, error=${payload.error || ''}`
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
   * 委托通用函数 addOrUpdateToolCallInMap 处理 toolCallMap 操作（含 _synthetic 占位机制、
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

    // toolCall 创建后（容错场景：tool_result 先于 tool 到达），检查是否有待绑定的审批
    // flushPendingApprovals 内部会检查 pending 队列，无匹配时直接返回，调用安全
    flushPendingApprovals(taskId, toolCallId)
    _syncToolCalls(task)
  }

  /**
   * 将审批数据附加到对应 toolCall 的 approval 字段
   *
   * 委托通用函数 setApprovalToToolCallInMap 处理 toolCallMap 操作（含匹配查找、_synthetic 占位机制、
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
      const pendingIds = [toolCallId, approvalData.tool_call_id].filter(Boolean)
      for (const pid of pendingIds) {
        task.pendingApprovals.value.set(pid, { approvalData, toolCallId })
      }
      logger.info(
        `[Research] 占位 toolCall 已加入 pendingApprovals 队列: taskId=${taskId}, toolCallId=${toolCallId}, pendingIds=${pendingIds}`
      )
    }
  }

  /**
   * 仅更新 approval.state，不改变 toolCall.status
   *
   * 统一通过 toolCallMap 作为唯一真相源操作。Map 未命中时，先从 task.toolCalls
   * 数组填充到 Map，再通过 Map 函数更新，避免直接修改 toolCalls 数组导致的竞态问题。
   *
   * 审批态与工具执行态解耦：
   * - approval.state 驱动审批面板显示/按钮禁用
   * - toolCall.status 由 tool_result 事件驱动（completed/failed）
   *
   * @param {string} taskId - 研究任务 ID
   * @param {string} toolCallId - 工具调用 ID
   * @param {string} state - 新的 approval.state（如 'processing'）
   * @param {string|null} [messageBackendId=null] - 预留参数，与 sessionStore 签名对齐（research 不按消息分组）
   * @param {Object|null} [data=null] - 审批数据，用于辅助查找 toolCall（tool_call_id）
   * @returns {boolean} 是否成功更新
   */
  const updateToolCallApprovalStateOnly = (taskId, toolCallId, state, messageBackendId = null, data = null) => {
    const task = tasks.value.get(taskId)
    if (!task) {
      logger.warn(`[Research] updateToolCallApprovalStateOnly: task 不存在, taskId=${taskId}`)
      return false
    }
    const toolCallMap = task.toolCallMap.value

    // 尝试从 Map 查找
    let { toolCall: target } = findToolCallInMap(toolCallMap, toolCallId, data)

    // Map 未命中：从 task.toolCalls 数组填充到 Map（单向数据流：数组 → Map）
    // 场景：刷新后 toolCallMap 可能未填充该 toolCall，但 toolCalls 数组中已有
    if (!target) {
      const toolCallsArr = task.toolCalls.value
      if (toolCallsArr && Array.isArray(toolCallsArr)) {
        const tc = findToolCallById(toolCallsArr, toolCallId, { skipApproved: false })
        if (tc) {
          const writeKey = tc.id || tc.tool_call_id || toolCallId
          toolCallMap.set(writeKey, tc)
          triggerRef(task.toolCallMap)
          target = tc
          logger.info(
            `[Research] updateToolCallApprovalStateOnly: Map 未命中，从 toolCalls 数组填充到 Map: ` +
            `taskId=${taskId}, toolCallId=${toolCallId}`
          )
        }
      }
    }

    if (!target) {
      logger.warn(
        `[Research] updateToolCallApprovalStateOnly: 未找到 toolCall, taskId=${taskId}, ` +
        `toolCallId=${toolCallId}, state=${state}`
      )
      return false
    }

    const oldState = target.approval?.state
    const toolName = target.name || target.tool_name

    // 统一通过 Map 函数更新 approval.state
    const updated = updateApprovalStateInMap(toolCallMap, toolCallId, state, data)
    if (!updated) return false

    _syncToolCalls(task)
    logger.debug(
      `[Research] updateToolCallApprovalStateOnly: taskId=${taskId}, toolCallId=${toolCallId}, ` +
      `oldState=${oldState}, newState=${state}, toolName=${toolName}`
    )
    return true
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
   * @param {string|null} [messageBackendId=null] - 预留参数，与 sessionStore 签名对齐
   * @param {Object|null} [data=null] - 审批数据，用于辅助查找 toolCall（tool_call_id）
   */
  const updateToolCallStatus = (taskId, toolCallId, status, messageBackendId = null, data = null) => {
    const task = tasks.value.get(taskId)
    if (!task) {
      logger.warn(`[Research] updateToolCallStatus: task 不存在, taskId=${taskId}`)
      return
    }
    const toolCallMap = task.toolCallMap.value

    // 尝试从 Map 查找
    let { toolCall: target } = findToolCallInMap(toolCallMap, toolCallId, data)

    // Map 未命中：从 task.toolCalls 数组填充到 Map（单向数据流：数组 → Map）
    if (!target) {
      const toolCallsArr = task.toolCalls.value
      if (toolCallsArr && Array.isArray(toolCallsArr)) {
        const tc = findToolCallById(toolCallsArr, toolCallId, { approvalData: data, skipApproved: false })
        if (tc) {
          const writeKey = tc.id || tc.tool_call_id || toolCallId
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
      return
    }

    const oldStatus = target.status
    const toolName = target.name || target.tool_name

    // 统一通过 Map 函数更新 status
    updateToolCallStatusInMap(toolCallMap, toolCallId, status, data)

    _syncToolCalls(task)
    logger.debug(
      `[Research] updateToolCallStatus: taskId=${taskId}, toolCallId=${toolCallId}, ` +
      `oldStatus=${oldStatus}, newStatus=${status}, toolName=${toolName}`
    )
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
   * 调用 getResearchToolCalls API，初始化 task 数据结构，
   * 将历史数据（对象结构）转换为 toolCallMap + toolCalls 数组
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
      const response = await getResearchToolCalls(taskId)
      const data = response.data?.data || response.data
      const toolCallsObj = data?.tool_calls || {}
      // 后端返回对象结构（key=tool_call_id），转换为数组
      const backendList = Object.values(toolCallsObj)

      // 合并策略：使用 _mergeToolCalls 增量合并（而非全量替换），保留本地审批中间状态
      // （pending/processing/waiting）和 tool status，避免刷新时丢失实时同步数据。
      // 与 sessionStore.loadSessionDetail 行为一致：所有模块刷新时统一通过合并而非替换。
      const existingList = task.toolCalls.value || []
      const mergedList = _mergeToolCalls(existingList, backendList)

      // 同步更新 toolCallMap：重建 Map 以确保一致性
      const mergedMap = new Map(mergedList.map(tc => [tc.id || tc.tool_call_id, tc]))
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
    const pending = task.pendingApprovals.value.get(toolCallId)
    if (!pending) return
    // 从队列移除后递归调用 setApprovalToToolCall，此时 toolCallMap 中已有目标条目
    task.pendingApprovals.value.delete(toolCallId)
    setApprovalToToolCall(taskId, toolCallId, pending.approvalData)
    logger.info(`[Research] pending 审批已绑定: taskId=${taskId}, toolCallId=${toolCallId}`)
  }

  return {
    tasks,
    taskInfo,
    addOrUpdateToolCall,
    updateOrAddToolResult,
    setApprovalToToolCall,
    updateToolCallApprovalStateOnly,
    updateToolCallStatus,
    getToolCalls,
    loadHistory,
    clearTask,
    flushPendingApprovals,
    // 任务状态管理（统一底层：替代 DeepResearchView 本地 task ref）
    setTaskStatus,
    getTaskStatus,
    updateTaskFromEvent,
    clearTaskInfo,
  }
})
