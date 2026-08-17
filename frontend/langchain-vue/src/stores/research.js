import { defineStore } from 'pinia'
import { ref, markRaw, triggerRef } from 'vue'
import { logger } from '@/utils/logger'
import { toCamelCase } from '@/utils/sessionTransformers'
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
        // 子代理图层正文/中间思考（spec MODIFIED：按 subagentThreadId 键累计）：
        // key = subagentThreadId，value = { content, reasoningContent }
        subagentContents: ref({}),
      }))
    }
    return tasks.value.get(taskId)
  }

  /**
   * 更新任务子代理正文/中间思考（spec D10）
   *
   * 由 task 频道 stream_subagent_content 事件调用（独立深度研究模式）；
   * 按 subagentThreadId 聚合（替代旧 agentPath 聚合）；content/reasoningContent 追加累计。
   *
   * @param {string} taskId - 研究任务 ID
   * @param {string} subagentThreadId - 子代理 thread_id
   * @param {string} content - 本轮子代理正文增量
   * @param {string} reasoningContent - 本轮子代理中间思考增量
   */
  const setTaskSubagentContent = (taskId, subagentThreadId, content, reasoningContent) => {
    if (!taskId || !subagentThreadId) return
    const task = _ensureTask(taskId)
    if (!content && !reasoningContent) return
    // 新对象追加（与 chat 模块 _applySubagentContent 修复模式一致，防同类共享引用隐患）：
    // 从现有 entry 构建新对象，content/reasoningContent 各追加一次后整表替换
    const entry = task.subagentContents.value[subagentThreadId] || { content: '', reasoningContent: '' }
    const nextEntry = { ...entry }
    if (content) nextEntry.content = (nextEntry.content || '') + content
    if (reasoningContent) nextEntry.reasoningContent = (nextEntry.reasoningContent || '') + reasoningContent
    task.subagentContents.value = { ...task.subagentContents.value, [subagentThreadId]: nextEntry }
  }

  /**
   * 同步 toolCallMap 到 toolCalls 数组（触发响应式更新）
   * 修改 toolCallMap 内对象属性后必须调用，确保 UI 刷新
   * @param {Object} task - task 数据对象
   */
  const _syncToolCalls = (task) => {
    // 跨浏览器统一排序：seq（register 分配的 (module, module_id) 内跨 LLM 轮次
    // 全局递增序号，所有链路统一透传，含 Approval.extra 持久化）是唯一排序依据。
    // 与 sessionStore 共用 sortToolCallsForDisplay（messageOperations.js 唯一权威
    // 排序实现），保证深度研究详情与聊天深度研究模式工具调用顺序跨浏览器一致
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
   * 获取指定 task 的子代理图层正文/思考索引（Agent 图层嵌套规范 Task 8.3）
   *
   * 独立深度研究模式子代理正文的读取入口；聊天触发场景由 session 消息
   * （message.subagentContents）优先提供，此处为回退来源。
   *
   * @param {string} taskId - 研究任务 ID
   * @returns {Object} { [pathKey]: { content, reasoningContent } }（task 不存在时返回空对象）
   */
  const getTaskSubagentContents = (taskId) => {
    const task = tasks.value.get(taskId)
    return task ? task.subagentContents.value : {}
  }

  /**
   * 从后端快照合并工具调用（含 result，刷新还原权威来源）
   *
   * 数据源为 ResearchTask.tool_calls（后端 status 接口返回，含 result），
   * 替代原 Approval 历史重建（Approval 不持久化 result，刷新后丢失）。
   * 合并策略与 sessionStore.loadSessionDetail 一致：_mergeToolCalls 增量合并，
   * 保留本地审批中间状态与更完整的 result/status。
   *
   * @param {string} taskId - 研究任务 ID
   * @param {Array} backendToolCalls - 后端 tool_calls 数组（camelCase）
   */
  const setTaskToolCallsFromSnapshot = (taskId, backendToolCalls) => {
    if (!taskId) return
    const task = _ensureTask(taskId)
    const existingList = task.toolCalls.value || []
    const backendList = Array.isArray(backendToolCalls) ? backendToolCalls : []
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
  }

  /**
   * 从后端快照合并子代理正文（刷新还原权威来源）
   *
   * 数据源为 ResearchTask.subagent_contents（后端 status 接口返回，key=threadId）。
   * 后端为持久化权威，但本地实时累计更超前时保留本地（快照滞后保护）。
   *
   * @param {string} taskId - 研究任务 ID
   * @param {Object} contents - 后端 subagent_contents（camelCase，key=threadId）
   */
  const setTaskSubagentContentsFromSnapshot = (taskId, contents) => {
    if (!taskId || !contents || typeof contents !== 'object') return
    const task = _ensureTask(taskId)
    const merged = { ...task.subagentContents.value }
    for (const [threadId, entry] of Object.entries(contents)) {
      if (!entry || typeof entry !== 'object') continue
      const local = merged[threadId] || {}
      merged[threadId] = {
        content: (local.content?.length ?? 0) >= (entry.content?.length ?? 0)
          ? (local.content || '')
          : (entry.content || ''),
        reasoningContent: (local.reasoningContent?.length ?? 0) >= (entry.reasoningContent?.length ?? 0)
          ? (local.reasoningContent || '')
          : (entry.reasoningContent || ''),
        ...(entry.agentName ? { agentName: entry.agentName } : {}),
        ...(typeof entry.depth === 'number' ? { depth: entry.depth } : {}),
      }
    }
    task.subagentContents.value = merged
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
    getTaskSubagentContents,
    setTaskToolCallsFromSnapshot,
    setTaskSubagentContentsFromSnapshot,
    clearTask,
    flushPendingApprovals,
    // 子代理图层正文（Agent 图层嵌套规范 Task 1.5）
    setTaskSubagentContent,
    // 任务状态管理（统一底层：替代 DeepResearchView 本地 task ref）
    setTaskStatus,
    getTaskStatus,
    updateTaskFromEvent,
    setTaskReasoning,
    clearTaskInfo,
  }
})
