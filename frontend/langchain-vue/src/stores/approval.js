import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useSessionStore } from './session'
import { useModelStore } from './model'
import { useStreamFinalizer } from '../composables/useStreamFinalizer'
import { resumeApprovalStream } from '../api/approval'
import { getInterruptId } from '../utils/messageOperations'
import { toCamelCase } from '../utils/sessionTransformers'
import { readSSEStream } from '../utils/sse'
import { StreamState } from '../types'
import { logger } from '../utils/logger'
import { ElMessage } from 'element-plus'

/** 审批过期时间：30 分钟 */
const APPROVAL_EXPIRY_MS = 30 * 60 * 1000
/** 清理检查间隔：5 分钟 */
const CLEANUP_INTERVAL_MS = 5 * 60 * 1000

/**
 * 惰性加载 research store（统一审批中心对 research 模块的依赖解耦）
 *
 * Task 9 / spec Change 5：approval.js 不静态 import research，避免与业务模块
 * 形成模块加载期依赖；仅在审批执行（async 上下文）实际需要更新研究任务
 * 工具审批状态时动态 import 并缓存模块 Promise，实例按当前 pinia 解析。
 */
let _researchStoreModulePromise = null
const _getResearchStore = async () => {
  if (!_researchStoreModulePromise) {
    _researchStoreModulePromise = import('./research')
  }
  const mod = await _researchStoreModulePromise
  return mod.useResearchStore()
}

/** debounced 同步到后端 — 审批事件到达后立即同步，确保跨浏览器/刷新可恢复 */
const _approvalSyncTimers = {}
const _debouncedApprovalSync = (sessionStore, sessionId, messageBackendId) => {
  if (_approvalSyncTimers[sessionId]) clearTimeout(_approvalSyncTimers[sessionId])
  _approvalSyncTimers[sessionId] = setTimeout(() => {
    if (messageBackendId) {
      sessionStore.syncMessageByBackendIdToBackend(sessionId, messageBackendId, { allowCreate: false }).catch(() => {})
    } else {
      sessionStore.syncLastMessageToBackend(sessionId, { allowCreate: false }).catch(() => {})
    }
    delete _approvalSyncTimers[sessionId]
  }, 200)
}

export const useApprovalStore = defineStore('approval', () => {
  /**
   * 统一待审批 Map
   * key: interruptId
   * value: { source: 'chat'|'deep_research', taskId?, sessionId?, approvalData, createdAt }
   */
  const pendingApprovals = ref(new Map())

  /** 是否有待处理审批 */
  const hasPending = computed(() => pendingApprovals.value.size > 0)

  let cleanupTimer = null

  /**
   * 审批串行执行队列（按 sessionId 隔离）
   *
   * 同一会话的审批恢复流必须串行执行，避免多个 SSE 流并发写入同一会话
   * 的最后一条消息（content/toolCalls 竞态）。不同会话可并行。
   * deep_research 独立模式（无 sessionId）直接执行不入队。
   *
   * 结构：Map<sessionId, Promise>
   */
  const _approvalQueues = new Map()

  /**
   * 将审批执行入队（同一 session 串行）
   * @param {string} sessionId
   * @param {() => Promise} fn - 审批执行函数
   * @returns {Promise} fn 的返回值
   */
  const _enqueueApprovalExecution = (sessionId, fn) => {
    if (!sessionId) return fn()
    const prev = _approvalQueues.get(sessionId) || Promise.resolve()
    // then(onFulfilled, onRejected)：前一个无论成功/失败都继续执行下一个
    const next = prev.then(fn, fn)
    _approvalQueues.set(sessionId, next)
    next.finally(() => {
      if (_approvalQueues.get(sessionId) === next) {
        _approvalQueues.delete(sessionId)
      }
    })
    return next
  }

  // ==================== 事件处理 ====================

  /**
   * 统一处理审批相关 SSE 事件（approval / approval_timeout / approval_processed）
   * @param {Object} data - SSE 事件数据
   * @param {Object} options - 上下文信息
   * @param {string} options.source - 事件来源 'chat' | 'deep_research'
   * @param {string} [options.sessionId] - 聊天会话 ID（chat 来源必传）
   * @param {string} [options.taskId] - 深度研究任务 ID（deep_research 来源必传）
   * @param {Object} [options.baseApproval] - 基础审批数据（用于继承工具配置，仅 chat 来源）
   */
  const handleApprovalEvent = (data, options = {}) => {
    const { source = 'chat', sessionId, taskId, baseApproval = {} } = options
    const converted = toCamelCase(data)
    const eventType = converted.type || converted.state

    // 审批超时
    if (eventType === 'approval_timeout' || converted.state === 'timeout') {
      _handleTimeout(converted, source, sessionId)
      return
    }

    // 审批正在处理（另一端已点击确认/拒绝，后端处理中）
    if (eventType === 'approval_processing') {
      _handleProcessing(converted, source, sessionId)
      return
    }

    // 批量审批等待（同批次部分已审批，其他工具仍 pending）
    if (eventType === 'approval_waiting') {
      _handleWaiting(converted, source, sessionId)
      return
    }

    // 审批已处理（另一端已操作完成：approval_processed / approval_approved / approval_rejected）
    if (eventType === 'approval_processed'
        || eventType === 'approval_approved'
        || eventType === 'approval_rejected') {
      _handleProcessed(converted, source, sessionId)
      return
    }

    // 新审批请求
    _handleNewApproval(converted, source, sessionId, taskId, baseApproval)
  }

  /**
   * 新审批请求
   */
  const _handleNewApproval = (data, source, sessionId, taskId, baseApproval) => {
    const sessionStore = useSessionStore()
    const toolCallId = getInterruptId(data)
    if (!toolCallId) return

    // 去重：已存在相同 toolCallId 的 pending 审批，跳过（双端 SSE 可能同时推送）
    const existing = pendingApprovals.value.get(toolCallId)
    if (existing && existing.approvalData?.state === 'pending') {
      return
    }

    const isDeepResearch = data.source === 'deep_research' || source === 'deep_research'
    const approvalData = {
      ...data,
      state: 'pending',
      ...(isDeepResearch ? {} : {
        useTools: baseApproval.useTools ?? true,
        useWebSearch: baseApproval.useWebSearch ?? false,
        useMcp: baseApproval.useMcp ?? false,
        selectedMcpServers: baseApproval.selectedMcpServers ?? null,
        selectedTools: baseApproval.selectedTools ?? null,
        useKnowledgeBase: baseApproval.useKnowledgeBase ?? false,
        selectedKnowledgeBases: baseApproval.selectedKnowledgeBases ?? [],
      }),
    }

    const effectiveSource = isDeepResearch ? 'deep_research' : 'chat'
    // 独立 deep_research 无会话上下文，sessionId 必须保持 null：
    // 不得回退到 sessionStore.currentSessionId（聊天残留值），
    // 否则后续 approval_waiting/processing 等事件会误走 sessionStore 分支，
    // researchStore 的 toolCall.approval 状态不更新，跨浏览器按钮状态不同步。
    const entrySessionId = sessionId || (isDeepResearch ? null : sessionStore.currentSessionId)
    // taskId 路由（独立深度研究根因修复）：
    // - 优先事件 payload 内的 taskId（chat 关联场景可能携带）
    // - 回退函数参数 taskId（task 通道事件将 task_id 注入事件顶层，经
    //   handleApprovalEvent 的 options.taskId 传入，payload 内部不含 taskId）
    // 原实现仅取 data.taskId 导致独立 deep_research 的 taskId 恒为 null，
    // DeepResearchView.taskPendingApprovals 过滤 entry.taskId === tid 永不命中，
    // 审批面板（含确认/拒绝按钮）不渲染、任务卡在"等待审批"。
    const effectiveTaskId = data.taskId || taskId || null

    pendingApprovals.value.set(toolCallId, {
      source: effectiveSource,
      taskId: effectiveTaskId,
      sessionId: entrySessionId,
      approvalData,
      createdAt: Date.now(),
    })

    // 同步到 session store（仅 chat 来源有 sessionId 时）
    if (sessionId) {
      sessionStore.setApprovalToToolCall(sessionId, toolCallId, approvalData)
      sessionStore.setApprovalToLastMessage(sessionId, { ...data, state: 'pending' })
      // 立即同步审批状态到后端，确保其他浏览器/刷新后可恢复
      _debouncedApprovalSync(sessionStore, sessionId)
    } else if (effectiveTaskId) {
      // 独立 deep_research（无 sessionId）：将审批数据写入 researchStore 的 toolCall。
      // 这是 approval 数据进入工具卡片（toolCall.approval）的唯一路径：
      // toolCallHandler.js 处理 tool_call_waiting 事件构造的 toolData 不含 approval 字段，
      // ToolCallCard.isWaiting 依赖 toolCall.approval 渲染"确认执行/拒绝"按钮。
      // 时序保护：审批事件可能先于 tool_call_waiting 到达，setApprovalToToolCall 内部
      // 会创建 isSynthetic 占位条目，后续工具事件合并绑定。
      _getResearchStore()
        .then((rs) => rs.setApprovalToToolCall(effectiveTaskId, toolCallId, approvalData))
        .catch((e) => logger.warn('[ApprovalStore] 同步 researchStore 审批数据失败:', e))
    }
  }

  /**
   * 审批超时处理
   */
  const _handleTimeout = (data, source, sessionId) => {
    const sessionStore = useSessionStore()
    const toolCallId = getInterruptId(data)

    // 从 pendingApprovals Map 中获取已有条目的 sessionId
    const existingEntry = toolCallId ? pendingApprovals.value.get(toolCallId) : null

    // 幂等保护（P-FE-7）：pendingApprovals 中已无该审批条目时直接返回。
    // 同一审批超时事件可能经 WebSocket / SSE 主聊天流 / 审批恢复流多条路径重复到达，
    // 首次处理已删除条目，后续重复到达不应再重复写 store / 重复同步后端。
    if (toolCallId && !existingEntry) return

    // 独立 deep_research 无会话上下文：不使用 currentSessionId 兜底，
    // 否则会误走 sessionStore 分支（researchStore 状态不更新，跨浏览器不同步）。
    const effectiveSessionId = sessionId
      || existingEntry?.sessionId
      || (source === 'deep_research' || existingEntry?.source === 'deep_research'
        ? null
        : sessionStore.currentSessionId)

    if (toolCallId) {
      pendingApprovals.value.delete(toolCallId)
    }

    // 更新 session store 中的审批状态
    if (effectiveSessionId) {
      sessionStore.appendToLastMessage(
        effectiveSessionId,
        `\n\n> ⏰ 工具 "${data.toolName || '未知'}" 的审批已超时，Agent 将使用其他方式继续\n`
      )
      if (toolCallId) {
        sessionStore.updateToolCallApprovalState(effectiveSessionId, toolCallId, 'timeout')
        sessionStore.setApprovalToLastMessage(effectiveSessionId, { ...data, state: 'timeout' })
      }
      // 同步审批状态到后端，防止刷新后状态丢失
      sessionStore.syncLastMessageToBackend(effectiveSessionId, { allowCreate: false }).catch(() => {})
    } else if (existingEntry?.taskId) {
      // 独立 deep_research：同步超时状态到 researchStore，
      // 否则 toolCall.approval.state 残留 pending 导致 isWaiting 仍为 true，审批面板不消失
      _getResearchStore()
        .then((rs) => rs.updateToolCallApprovalState(existingEntry.taskId, toolCallId, 'timeout'))
        .catch((e) => logger.warn('[ApprovalStore] 同步 researchStore timeout 失败:', e))
    }

    // 深度研究来源额外提示
    if (source === 'deep_research' || data.source === 'deep_research') {
      ElMessage.warning(`工具 "${data.toolName || '未知'}" 的审批已超时，Agent 将使用其他方式继续`)
    }
  }

  /**
   * 审批正在处理（另一端已点击确认/拒绝，后端处理中）
   *
   * 收到 approval_processing 事件时，更新本地审批状态为 processing，
   * 但保留在 pendingApprovals 中（审批面板保持显示，按钮禁用）。
   * 工具结果到达后（approval_processed / tool_result），审批条目才会被移除。
   *
   * @param {Object} data - SSE 事件数据
   * @param {string} source - 事件来源
   * @param {string} [sessionId] - 聊天会话 ID
   */
  const _handleProcessing = (data, source, sessionId) => {
    const sessionStore = useSessionStore()
    const toolCallId = getInterruptId(data)
    if (!toolCallId) return

    const existingEntry = pendingApprovals.value.get(toolCallId)
    if (!existingEntry) return

    // 仅更新 state，保留在 pendingApprovals（审批面板保持显示，按钮禁用）
    existingEntry.approvalData = { ...existingEntry.approvalData, ...data, state: 'processing' }

    // 独立 deep_research 无会话上下文：不使用 currentSessionId 兜底
    const effectiveSessionId = sessionId
      || existingEntry?.sessionId
      || (source === 'deep_research' || existingEntry?.source === 'deep_research'
        ? null
        : sessionStore.currentSessionId)
    if (effectiveSessionId) {
      sessionStore.updateToolCallApprovalState(effectiveSessionId, toolCallId, 'processing')
      sessionStore.setApprovalToLastMessage(effectiveSessionId, { ...data, state: 'processing' })
      sessionStore.syncLastMessageToBackend(effectiveSessionId, { allowCreate: false }).catch(() => {})
    } else if (existingEntry?.taskId) {
      // 独立 deep_research：同步审批状态到 researchStore（跨浏览器按钮禁用一致性）
      _getResearchStore()
        .then((rs) => rs.updateToolCallApprovalState(existingEntry.taskId, toolCallId, 'processing'))
        .catch((e) => logger.warn('[ApprovalStore] 同步 researchStore processing 失败:', e))
    }
  }

  /**
   * 批量审批等待：同批次本工具已审批，等待其他工具。
   */
  const _handleWaiting = (data, source, sessionId) => {
    const sessionStore = useSessionStore()
    const toolCallId = getInterruptId(data)
    if (!toolCallId) return

    const existingEntry = pendingApprovals.value.get(toolCallId)

    // P19 修复：waiting 状态应保持 'waiting' 而非覆盖为 'processing'
    // 这样 ToolCallCard 的 isWaitingForSiblings 才能检测到并显示提示文字
    if (existingEntry) {
      existingEntry.approvalData = { ...existingEntry.approvalData, ...data, state: 'waiting' }
    }
    // 独立 deep_research 无会话上下文：不使用 currentSessionId 兜底
    const effectiveSessionId = sessionId
      || existingEntry?.sessionId
      || (source === 'deep_research' || existingEntry?.source === 'deep_research'
        ? null
        : sessionStore.currentSessionId)
    if (effectiveSessionId) {
      sessionStore.updateToolCallApprovalState(effectiveSessionId, toolCallId, 'waiting')
      sessionStore.setApprovalToLastMessage(effectiveSessionId, { ...data, state: 'waiting' })
      sessionStore.syncLastMessageToBackend(effectiveSessionId, { allowCreate: false }).catch(() => {})
    } else if (existingEntry?.taskId) {
      // 独立 deep_research：同步"等待同批"状态到 researchStore
      _getResearchStore()
        .then((rs) => rs.updateToolCallApprovalState(existingEntry.taskId, toolCallId, 'waiting'))
        .catch((e) => logger.warn('[ApprovalStore] 同步 researchStore waiting 失败:', e))
    }
  }

  /**
   * 审批已处理通知（另一端已操作完成）
   *
   * 处理三种事件类型：
   * - approval_processed：通用处理完成事件，通过 data.approved 判断最终状态
   * - approval_approved：明确批准事件，finalState='approved'
   * - approval_rejected：明确拒绝事件，finalState='rejected'
   *
   * 从 pendingApprovals 移除条目，更新 session store 中的审批状态。
   */
  const _handleProcessed = (data, source, sessionId) => {
    const sessionStore = useSessionStore()
    const toolCallId = getInterruptId(data)
    if (!toolCallId) return

    // 从 pendingApprovals Map 中获取已有条目的 sessionId（可能比参数中的更准确）
    const existingEntry = pendingApprovals.value.get(toolCallId)

    // 幂等保护（P-FE-7）：pendingApprovals 中已无该审批条目时直接返回。
    // 同一审批终态事件（approval_processed / approval_approved / approval_rejected）
    // 可能经 WebSocket / SSE 主聊天流 / 审批恢复流多条路径重复到达，
    // 首次处理已删除条目，后续重复到达不应再重复写 store / 重复同步后端。
    if (!existingEntry) return

    // 独立 deep_research 无会话上下文：不使用 currentSessionId 兜底
    const effectiveSessionId = sessionId
      || existingEntry?.sessionId
      || (source === 'deep_research' || existingEntry?.source === 'deep_research'
        ? null
        : sessionStore.currentSessionId)

    pendingApprovals.value.delete(toolCallId)

    // 确定最终状态：优先使用 data.state，其次 data.approved，默认 rejected
    const finalState = data.state === 'approved' ? 'approved'
      : data.state === 'rejected' ? 'rejected'
      : data.approved ? 'approved' : 'rejected'

    // 更新 session store 中的审批状态
    if (effectiveSessionId) {
      sessionStore.updateToolCallApprovalState(effectiveSessionId, toolCallId, finalState)
      sessionStore.setApprovalToLastMessage(effectiveSessionId, { ...data, state: finalState })
      // 同步审批状态到后端，防止刷新后状态丢失
      sessionStore.syncLastMessageToBackend(effectiveSessionId, { allowCreate: false }).catch(() => {})
    } else if (existingEntry?.taskId) {
      // 独立 deep_research：同步终态审批状态到 researchStore（跨浏览器一致性）
      _getResearchStore()
        .then((rs) => rs.updateToolCallApprovalState(existingEntry.taskId, toolCallId, finalState))
        .catch((e) => logger.warn('[ApprovalStore] 同步 researchStore 终态失败:', e))
    }
  }

  // ==================== 审批执行 ====================

  /**
   * 统一审批执行
   * @param {Object} approval - 审批数据对象
   * @param {boolean} approved - true=确认, false=拒绝
   * @param {string|null} userInput - 用户输入（confirm_with_input 模式）
   * @param {Object} options - 额外参数
   * @param {string} [options.taskId] - 深度研究任务 ID（覆盖 store 中的值）
   * @param {Function} [options.onChatStreamChunk] - 聊天审批 SSE 流的 chunk 回调
   * @param {Function} [options.onChatStreamTool] - 聊天审批 SSE 流的 tool 回调
   * @param {Function} [options.onChatStreamToolResult] - 聊天审批 SSE 流的 tool_result 回调
   * @param {Function} [options.onChatStreamReasoning] - 聊天审批 SSE 流的 reasoning 回调
   * @param {Function} [options.onChatStreamApproval] - 聊天审批 SSE 流的 approval 回调
   * @param {Function} [options.onChatStreamError] - 聊天审批 SSE 流的 error 回调
   */
  const executeApproval = async (approval, approved, userInput = null, options = {}) => {
    if (!approval) return

    const toolCallId = getInterruptId(approval)
    const entry = pendingApprovals.value.get(toolCallId)
    const approvalData = entry?.approvalData || approval
    if (!approvalData) return

    const sessionStore = useSessionStore()
    const source = entry?.source || (approvalData.source === 'deep_research' ? 'deep_research' : 'chat')
    // sessionId 推导优先级：
    // 1. options.sessionId（调用方显式指定，如 DeepResearchView 详情页）
    // 2. entry.sessionId（WebSocket 审批事件创建 entry 时携带）
    // 3. approvalData.crossModuleId / chatSessionId（后端透传的关联会话 ID，
    //    聊天触发的深度研究 = chat_session_id，避免详情页审批时
    //    因 currentSessionId 为空/错误导致消息审批状态不更新）
    // 4. sessionStore.currentSessionId（仅 chat/learning 来源回退；deep_research
    //    独立模式不回退，避免误用聊天残留会话导致独立任务审批被错误路由到 sessionStore）
    const sessionId = options.sessionId
      || entry?.sessionId
      || approvalData.crossModuleId
      || approvalData.chatSessionId
      || (source !== 'deep_research' ? sessionStore.currentSessionId : null)
    const taskId = options.taskId || entry?.taskId || null

    // 防重复
    if (approvalData.state === 'processing') {
      logger.warn(`[ApprovalStore] 审批正在处理中，忽略重复操作: ${toolCallId}`)
      return
    }

    // 标记为 processing
    // 数据源归属互斥路由（与 _handleTimeout/_handleProcessing/_handleWaiting/_handleProcessed 对齐）：
    // - sessionId 存在（chat / learning / 聊天触发的深度研究）→ 数据源是 sessionStore 消息 toolCalls，
    //   只更新 sessionStore，不调用 researchStore（该任务不存在于 researchStore.tasks，
    //   详情页 taskToolCalls 同样从 sessionStore 消息读取）
    // - 仅 taskId（独立深度研究）→ 数据源是 researchStore.tasks，只更新 researchStore
    approvalData.state = 'processing'
    if (sessionId) {
      sessionStore.updateToolCallApprovalState(sessionId, toolCallId, 'processing')
      sessionStore.setApprovalToLastMessage(sessionId, { ...approvalData, state: 'processing' })
    } else if (taskId) {
      try {
        const researchStore = await _getResearchStore()
        researchStore.updateToolCallApprovalState(taskId, toolCallId, 'processing')
      } catch (e) { logger.warn('[ApprovalStore] 更新 researchStore processing 失败:', e) }
    }
    pendingApprovals.value.delete(toolCallId)

    try {
      // 统一路径：所有审批（chat / deep_research）走 /approvals/{interrupt_id}/resume/
      // 后端 ApprovalGateway 根据 Approval.source 路由：
      // - chat → SSE 流式恢复（_stream_chat_resume_generator）
      // - deep_research → Celery 任务恢复（research_resume_task），返回 JSON
      // 同一 session 串行执行，避免多个 SSE 流并发写入同一消息
      await _enqueueApprovalExecution(sessionId, () =>
        _executeApprovalStream(approvalData, approved, userInput, sessionId, toolCallId, options)
      )

      // 仅在拒绝时更新状态为 rejected
      // 通过时不设 approved：SSE 流中 tool_result 事件已自行更新状态，
      // 设为 approved 会覆盖"处理中"状态，导致 P1（触发浏览器显示"已确认"而非"处理中"）
      // 数据源归属互斥路由（与 processing 标记一致）：sessionId 优先，独立深度研究走 taskId
      if (!approved && sessionId) {
        sessionStore.updateToolCallApprovalState(sessionId, toolCallId, 'rejected')
        sessionStore.setApprovalToLastMessage(sessionId, { ...approvalData, state: 'rejected' })
      } else if (!approved && taskId) {
        try {
          const researchStore = await _getResearchStore()
          researchStore.updateToolCallApprovalState(taskId, toolCallId, 'rejected')
        } catch (e) { logger.warn('[ApprovalStore] 更新 researchStore rejected 失败:', e) }
      }

      // 同步消息到后端
      if (sessionId) {
        try {
          await sessionStore.syncLastMessageToBackend(sessionId, { allowCreate: false })
        } catch (syncErr) {
          logger.error('[ApprovalStore] 审批后同步消息失败:', syncErr)
        }
      }
    } catch (err) {
      // waiting_for_others / idempotent / interrupted：
      // 状态已在 _executeApprovalStream 内部处理（processing / 实际状态 / timeout 等），
      // 不应回退为 pending，也不需要再次同步消息
      if (err?.__approvalWaiting || err?.__approvalIdempotent || err?.__approvalInterrupted) {
        return
      }
      logger.error(`[ApprovalStore] 审批${approved ? '确认' : '拒绝'}失败:`, err)
      // 恢复审批状态（数据源归属互斥路由，与 processing/rejected 一致）
      pendingApprovals.value.set(toolCallId, {
        source,
        taskId,
        sessionId,
        approvalData: { ...approvalData, state: 'pending' },
        createdAt: Date.now(),
      })
      if (sessionId) {
        sessionStore.updateToolCallApprovalState(sessionId, toolCallId, 'pending')
        sessionStore.setApprovalToLastMessage(sessionId, { ...approvalData, state: 'pending' })
      } else if (taskId) {
        try {
          const researchStore = await _getResearchStore()
          researchStore.updateToolCallApprovalState(taskId, toolCallId, 'pending')
        } catch (e) { logger.warn('[ApprovalStore] 更新 researchStore pending 失败:', e) }
      }
      // 同步恢复后的状态到后端
      sessionStore.syncLastMessageToBackend(sessionId, { allowCreate: false }).catch(() => {})
    }
  }

  /**
   * 统一审批执行（调用 /approvals/{interrupt_id}/resume/ 端点）
   *
   * Path D 统一架构：chat 与 deep_research 审批均走此路径，后端 ApprovalGateway
   * 根据 Approval.source 路由：
   * - chat → SSE 流式恢复（_stream_chat_resume_generator，HTTP 请求内执行）
   * - deep_research → Celery 任务恢复（research_resume_task，worker 内执行）
   *
   * 后端响应类型由 content-type 区分：
   * - `text/event-stream`：chat 模块的 SSE 流（agent 恢复执行）
   * - `application/json`：
   *   - waiting_for_others：批量审批等待（同批次其他工具待审批）
   *   - idempotent：审批已被另一端处理（幂等响应）
   *   - resumed：deep_research Celery 任务已派发（无 SSE 流，实际输出通过 WebSocket 推送）
   *   - 错误响应
   *
   * 状态语义：
   * - waiting_for_others：本工具已审批，等待同批次其他工具审批完成后开始执行。
   *   UI 应保留为 `processing` 状态（不切到 approved/rejected），并通过 message 提示用户。
   *   通过抛出带 `__approvalWaiting` 标记的错误，让外层 `executeApproval` 跳过最终状态更新。
   * - idempotent：审批已被另一端处理。UI 直接更新为最终状态，外层跳过最终状态更新。
   * - resumed：deep_research 恢复任务已启动。正常返回，外层 executeApproval 更新最终状态。
   *   实际 agent 输出（工具调用/结果/推理）通过 WebSocket 推送，由 deep research 页面消费。
   * - interrupted：SSE 流中收到 approval_timeout / approval_processed 事件，流被中断。
   *   审批状态已在事件处理器中更新（timeout/approved/rejected），通过 `markInterrupted`
   *   保持消息为 INTERRUPTED 状态。抛出带 `__approvalInterrupted` 标记的错误，外层跳过最终状态更新。
   *
   * 流式生命周期复用 useStreamFinalizer（与 chat.js sendMessage 一致）：
   * - startStreaming → STREAMING → readSSEStream → finalizeStream (COMPLETED) / markInterrupted / ERROR
   *   （仅 chat SSE 流路径触发，deep_research JSON 路径不进入流式生命周期）
   *
   * @returns {Promise<void>}
   * @throws {Error} 审批失败时抛出；带 `__approvalWaiting` / `__approvalIdempotent` /
   *                 `__approvalInterrupted` 标记的错误表示状态已在内部处理，外层应跳过最终状态更新。
   */
  const _executeApprovalStream = async (approvalData, approved, userInput, sessionId, toolCallId, options) => {
    const interruptId = getInterruptId(approvalData)
    const modelStore = useModelStore()
    const modelConfig = modelStore.getModelConfig()
    // 动态 import 破环：sync.js 装配了 approval 事件处理器，approval store 不应静态依赖它
    const { useSyncStore } = await import('./sync')
    const syncStore = useSyncStore()
    const { finalizeStream, markInterrupted } = useStreamFinalizer()
    const sessionStore = useSessionStore()
    // interrupt_id 由 URL path 传递；session_id 由后端从 Approval.chat_session_id / source_id 读取
    const requestBody = {
      approved,
      providerId: modelConfig.providerId || null,
      modelName: modelConfig.modelName || null,
      useDeepThinking: modelStore.thinkingEnabled,
      specialParams: modelConfig.specialParams ? { ...modelConfig.specialParams } : null,
      temperature: modelConfig.temperature || null,
      maxTokens: modelConfig.maxTokens || null,
      useTools: approvalData.useTools ?? true,
      useWebSearch: approvalData.useWebSearch ?? false,
      useMcp: approvalData.useMcp ?? false,
      selectedMcpServers: approvalData.selectedMcpServers ?? null,
      selectedTools: approvalData.selectedTools ?? null,
      useKnowledgeBase: approvalData.useKnowledgeBase ?? false,
      selectedKnowledgeBases: approvalData.selectedKnowledgeBases ?? [],
    }
    if (approved && approvalData.action === 'confirm_with_input' && userInput !== null) {
      requestBody.userInput = userInput
    }

    const approvalAbortController = new AbortController()
    const response = await resumeApprovalStream(interruptId, requestBody, {
      signal: approvalAbortController.signal,
    })

    // 1. 非 2xx 错误：解析 JSON 错误信息后抛出
    if (response.ok === false) {
      let errorMsg = `审批请求失败: ${response.status}`
      try {
        const errorData = await response.json()
        errorMsg = errorData.message || errorData.error || errorData.detail || errorMsg
      } catch { /* 忽略解析错误 */ }
      throw new Error(errorMsg)
    }

    // 2. 区分 SSE 流 / JSON 响应（waiting / idempotent / 其他 JSON 场景）
    const contentType = response.headers.get('content-type') || ''
    if (contentType.includes('application/json')) {
      const jsonData = await response.json()
      const data = jsonData.data || {}

      // 批量审批等待：本工具已审批，等待同批次其他工具
      // P19 修复：waiting 状态应保持 'waiting' 而非覆盖为 'processing'
      // 这样 ToolCallCard 的 isWaitingForSiblings 才能检测到并显示提示文字
      if (data.status === 'waiting_for_others' || data.state === 'waiting') {
        if (sessionId) {
          sessionStore.updateToolCallApprovalState(sessionId, toolCallId, 'waiting')
          sessionStore.setApprovalToLastMessage(sessionId, { ...data, state: 'waiting' })
          sessionStore.syncLastMessageToBackend(sessionId, { allowCreate: false }).catch(() => {})
        }
        ElMessage.info(data.message || '本工具已审批，等待同批次其他工具审批完成后开始执行...')
        const err = new Error('waiting_for_others')
        err.__approvalWaiting = true
        throw err
      }

      // 幂等响应：审批已被另一端处理，按返回的实际状态更新 UI
      if (data.idempotent) {
        const idempotentState = data.state === 'approved' ? 'approved'
          : data.state === 'rejected' ? 'rejected'
          : data.state === 'waiting' ? 'waiting'
          : 'processing'
        if (sessionId) {
          pendingApprovals.value.delete(toolCallId)
          sessionStore.updateToolCallApprovalState(sessionId, toolCallId, idempotentState)
          sessionStore.setApprovalToLastMessage(sessionId, { ...data, state: idempotentState })
          sessionStore.syncLastMessageToBackend(sessionId, { allowCreate: false }).catch(() => {})
        }
        logger.info(`[ApprovalStore] 审批幂等响应：${toolCallId} 状态=${idempotentState}`)
        const err = new Error('idempotent')
        err.__approvalIdempotent = true
        throw err
      }

      // deep_research 恢复：Celery 任务已派发，返回 JSON（status: 'resumed'）
      // 后端 ApprovalGateway._resume_deep_research → research_resume_task.delay()
      // 前端无需消费 SSE 流，实际 agent 输出通过 WebSocket 推送到 deep research 页面
      // 正常返回，外层 executeApproval 更新最终状态为 approved/rejected
      if (data.status === 'resumed') {
        ElMessage.success(jsonData.message || '研究恢复任务已启动')
        logger.info(`[ApprovalStore] deep_research 恢复任务已启动: ${toolCallId}`)
        return
      }

      // 其他未知 JSON 响应：作为错误抛出
      throw new Error(jsonData.message || '审批处理失败')
    }

    // 3. 处理 SSE 流式响应（统一委托 readSSEStream 消费）
    //
    // 通过 onEvent 回调处理各事件类型：
    // - heartbeat：保持连接（readSSEStream 内部已更新 lastEventTime）
    // - chunk：流式追加消息内容
    // - tool / tool_result：工具调用与结果
    // - reasoning：推理过程
    // - approval：流中出现新审批
    // - error：流错误（设置 streamError 变量 + abort，外层抛出）
    // - approval_timeout / approval_processed：终止流（abort，标记 interrupted）
    //
    // 流式生命周期（与 chat.js sendMessage 一致，复用 useStreamFinalizer）：
    // - 进入前：startStreaming + INTERRUPTED → STREAMING（恢复中断的流）
    // - 正常结束：finalizeStream（FINALIZING → SYNCING → COMPLETED + stopStreaming）
    // - 审批中断：markInterrupted（INTERRUPTED + clearTimers + stopStreaming）
    // - 错误：ERROR + stopStreaming
    syncStore.startStreaming(sessionId)
    // 恢复中断的流：INTERRUPTED → STREAMING，让 finalizeStream 状态守卫通过
    sessionStore.setStreamStateToLastMessage(sessionId, StreamState.STREAMING)

    let streamError = null
    /** 审批事件中断流（approval_timeout / approval_processed）标记 */
    let interrupted = false
    const onEvent = (parsed) => {
      if (!parsed || !parsed.type) return
      const originalType = parsed.type
      const convertedParsed = toCamelCase(parsed)
      convertedParsed.type = originalType
      switch (convertedParsed.type) {
        case 'heartbeat':
          // readSSEStream 内部已通过 lastEventTime 维持心跳检测
          break
        case 'chunk':
          if (convertedParsed.content) {
            options.onChatStreamChunk?.(convertedParsed.content)
            sessionStore.appendToLastAssistantMessage(sessionId, convertedParsed.content)
          }
          break
        case 'tool':
          options.onChatStreamTool?.(convertedParsed.data)
          // Task 5.1：恢复流 SSE tool 事件与 WebSocket tool_call_pending/waiting/running
          // 共用同一幂等写入入口（addOrUpdateToolCall → addOrUpdateToolCallInMap，
          // id/toolCallId 精确匹配），双通道重复推送同一工具时幂等合并。
          sessionStore.addOrUpdateToolCall(sessionId, convertedParsed.data)
          break
        case 'tool_result':
          options.onChatStreamToolResult?.(convertedParsed.data)
          // Task 5.1：同上，tool_result 与 WS tool_call_completed/failed/timeout
          // 共用 updateOrAddToolResultInMap 幂等入口。
          sessionStore.updateOrAddToolResult(sessionId, convertedParsed.data)
          break
        case 'reasoning':
          if (convertedParsed.data?.content) {
            options.onChatStreamReasoning?.(convertedParsed.data)
            sessionStore.setReasoningToLastMessage(sessionId, convertedParsed.data)
          }
          break
        case 'approval':
          if (convertedParsed.data) {
            // 审批流中又出现新审批
            handleApprovalEvent(convertedParsed.data, {
              source: 'chat',
              sessionId,
              baseApproval: approvalData,
            })
          }
          break
        case 'error': {
          const errorMsg = convertedParsed.message || convertedParsed.data?.message || ''
          streamError = new Error(errorMsg || '审批处理失败')
          approvalAbortController.abort()
          break
        }
        case 'approval_timeout': {
          // 审批超时：终止当前流，标记 interrupted 由 finally 调用 markInterrupted。
          // 状态更新统一委托 _handleTimeout（与 WebSocket 路径 handleApprovalEvent 一致，
          // 幂等保护：pendingApprovals 中已无该条目时跳过，避免多路径重复写 store / 重复同步）
          interrupted = true
          _handleTimeout(convertedParsed.data || convertedParsed, 'chat', sessionId)
          approvalAbortController.abort()
          break
        }
        case 'approval_processed': {
          // 审批已在另一端处理：终止当前流，标记 interrupted 由 finally 调用 markInterrupted。
          // 状态更新统一委托 _handleProcessed（与 WebSocket 路径 handleApprovalEvent 一致，
          // 幂等保护：pendingApprovals 中已无该条目时跳过，避免多路径重复写 store / 重复同步）
          interrupted = true
          _handleProcessed(convertedParsed.data || convertedParsed, 'chat', sessionId)
          approvalAbortController.abort()
          break
        }
        default:
          // 未知事件类型：忽略（保持向前兼容）
          break
      }
    }

    try {
      await readSSEStream(response, onEvent, approvalAbortController.signal)
      if (streamError) throw streamError
    } catch (err) {
      // approval_timeout / approval_processed 中断流产生的 AbortError：预期行为，
      // 转换为 __approvalInterrupted 标记错误，让 executeApproval 跳过最终状态更新
      // （审批状态已在事件处理器中更新为 timeout/approved/rejected）
      if (interrupted && err?.name === 'AbortError') {
        const interruptErr = new Error('approval_interrupted')
        interruptErr.__approvalInterrupted = true
        throw interruptErr
      }
      throw err
    } finally {
      if (interrupted) {
        // 审批事件中断流：保持 INTERRUPTED 状态，等待后续审批恢复或用户操作
        markInterrupted(sessionId)
      } else if (streamError) {
        // 流错误：标记 ERROR 并停止流式（审批状态由 executeApproval catch 恢复为 pending）
        sessionStore.setStreamStateToLastMessage(sessionId, StreamState.ERROR)
        syncStore.stopStreaming(sessionId)
      } else {
        // 正常完成：最终化流（FINALIZING → SYNCING → COMPLETED + stopStreaming）
        const session = sessionStore.sessions.find(s => s.id === sessionId)
        const lastMsg = session?.messages?.[session.messages.length - 1]
        if (lastMsg) {
          await finalizeStream(sessionId, lastMsg)
        } else {
          syncStore.stopStreaming(sessionId)
        }
      }
    }
  }

  // ==================== 恢复 ====================

  /**
   * 从聊天会话消息中恢复 pendingApprovals
   * 页面刷新后 toolCalls 已从后端恢复，但 pendingApprovals 是运行时 Map，需要重建
   * @param {string} sessionId
   */
  const restoreFromSession = (sessionId) => {
    const sessionStore = useSessionStore()

    // 清理不属于当前会话的旧条目
    for (const [key, entry] of pendingApprovals.value.entries()) {
      if (entry.sessionId && entry.sessionId !== sessionId) {
        pendingApprovals.value.delete(key)
      }
    }

    // 从当前会话消息恢复
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session?.messages) return
    for (const msg of session.messages) {
      if (!msg.toolCalls || !Array.isArray(msg.toolCalls)) continue
      for (const tc of msg.toolCalls) {
        const approval = tc.approval
        if (approval && approval.state === 'pending') {
          const toolCallId = getInterruptId(approval) || tc.id || ''
          if (toolCallId && !pendingApprovals.value.has(toolCallId)) {
            const isDeepResearch = approval.source === 'deep_research'
            pendingApprovals.value.set(toolCallId, {
              source: isDeepResearch ? 'deep_research' : 'chat',
              taskId: approval.taskId || null,
              sessionId,
              approvalData: approval,
              createdAt: Date.now(),
            })
          }
        }
      }
    }
  }

  /**
   * 更新审批状态（触发 Vue 响应式更新）
   * @param {string} interruptId
   * @param {string} state - 'processing' | 'pending' | 'approved' | 'rejected' | 'timeout'
   */
  const updateApprovalState = (interruptId, state) => {
    const entry = pendingApprovals.value.get(interruptId)
    if (!entry) return
    // 重新 set 整个 entry 触发 Vue 响应式
    pendingApprovals.value.set(interruptId, {
      ...entry,
      approvalData: { ...entry.approvalData, state },
    })
  }

  /**
   * 从 SSE 历史审批数据恢复（深度研究模块刷新/重连场景）
   * 后端 views_stream.py 在 SSE 连接建立时推送 Redis List 中的历史审批
   * @param {Object} approvalData - 历史审批数据
   * @param {string} taskId - 深度研究任务 ID
   * @param {string} [sessionId] - 聊天会话 ID（聊天模块恢复时传入，用于同步到 sessionStore）
   */
  const restoreFromSSEHistory = (approvalData, taskId, sessionId = null) => {
    const toolCallId = getInterruptId(approvalData)
    if (!toolCallId) {
      logger.warn('[ApprovalStore] restoreFromSSEHistory: 无法提取 toolCallId', approvalData)
      return
    }
    // 已存在则跳过（可能已通过实时推送收到）
    if (pendingApprovals.value.has(toolCallId)) {
      logger.debug(`[ApprovalStore] restoreFromSSEHistory: 审批已存在，跳过 ${toolCallId}`)
      return
    }

    const state = approvalData.state || 'pending'
    const effectiveSessionId = sessionId || null

    // 已处理/超时的审批：不加入 pendingApprovals（不需要用户操作），但更新 session store 的 UI 状态
    if (state === 'timeout' || state === 'approved' || state === 'rejected') {
      logger.info(`[ApprovalStore] restoreFromSSEHistory: 审批已终态(${state}), 更新 UI: ${toolCallId}`)
      if (effectiveSessionId) {
        const sessionStore = useSessionStore()
        sessionStore.updateToolCallApprovalState(effectiveSessionId, toolCallId, state)
        sessionStore.setApprovalToLastMessage(effectiveSessionId, { ...approvalData, state })
      }
      return
    }

    logger.info(`[ApprovalStore] restoreFromSSEHistory: 恢复审批 ${toolCallId}, taskId=${taskId}, tool=${approvalData.toolName}`)

    pendingApprovals.value.set(toolCallId, {
      source: 'deep_research',
      taskId,
      sessionId: effectiveSessionId,
      approvalData: { ...approvalData, state: 'pending' },
      createdAt: Date.now(),
    })

    // 同步到 session store（聊天模块恢复时需要，确保消息中的审批 UI 可见）
    if (effectiveSessionId) {
      const sessionStore = useSessionStore()
      sessionStore.setApprovalToToolCall(effectiveSessionId, toolCallId, { ...approvalData, state: 'pending' })
      sessionStore.setApprovalToLastMessage(effectiveSessionId, { ...approvalData, state: 'pending' })
    }
  }

  // ==================== 清理 ====================

  /**
   * 清理过期审批（超过 30 分钟）
   */
  const cleanupExpired = () => {
    const now = Date.now()
    const sessionStore = useSessionStore()
    for (const [key, entry] of pendingApprovals.value.entries()) {
      if (entry.createdAt && now - entry.createdAt > APPROVAL_EXPIRY_MS) {
        logger.warn(`[ApprovalStore] 审批已过期，自动清理: ${key}`)
        pendingApprovals.value.delete(key)
        // 仅当 sessionId 明确有值时才更新 session store
        if (entry.sessionId) {
          sessionStore.updateToolCallApprovalState(entry.sessionId, key, 'timeout')
          sessionStore.setApprovalToLastMessage(entry.sessionId, { ...entry.approvalData, state: 'timeout' })
        }
      }
    }
  }

  /**
   * 按来源清理审批
   * @param {string} [source] - 不传则清理全部
   */
  const clearBySource = (source) => {
    if (!source) {
      pendingApprovals.value.clear()
      return
    }
    for (const [key, entry] of pendingApprovals.value.entries()) {
      if (entry.source === source) {
        pendingApprovals.value.delete(key)
      }
    }
  }

  /**
   * 清理指定任务 ID 的审批
   * @param {string} taskId
   */
  const clearByTaskId = (taskId) => {
    if (!taskId) return
    for (const [key, entry] of pendingApprovals.value.entries()) {
      if (entry.taskId === taskId) {
        pendingApprovals.value.delete(key)
      }
    }
  }

  /**
   * 清理全部（登出等场景）
   */
  const clearAll = () => {
    pendingApprovals.value.clear()
    if (cleanupTimer) {
      clearInterval(cleanupTimer)
      cleanupTimer = null
    }
  }

  // 启动自动清理定时器
  const startCleanup = () => {
    if (cleanupTimer) return
    cleanupTimer = setInterval(cleanupExpired, CLEANUP_INTERVAL_MS)
  }
  startCleanup()

  /**
   * 待绑定审批队列：审批事件到达时 toolCall 可能尚未创建（LLM 流式输出渐进），
   * 暂存到队列，由 tool_call_* 系列事件触发重绑定
   * @type {import('vue').Ref<Array<{sessionId: string, toolCallId: string, approvalData: Object, createdAt: number}>>}
   */
  const pendingBindQueue = ref([])

  /**
   * 刷新待绑定队列：尝试将队列中的审批绑定到已创建的 toolCall
   * @param {string} [sessionId] - 仅刷新指定 session 的待绑定审批
   */
  const flushPendingBindQueue = (sessionId) => {
    if (pendingBindQueue.value.length === 0) return

    const sessionStore = useSessionStore()
    const remaining = []

    for (const item of pendingBindQueue.value) {
      if (sessionId && item.sessionId !== sessionId) {
        remaining.push(item)
        continue
      }
      const result = sessionStore.setApprovalToToolCall(item.sessionId, item.toolCallId, item.approvalData)
      if (result) {
        logger.info(`[ApprovalStore] 队列重绑定成功: toolCallId=${item.toolCallId}, sessionId=${item.sessionId}`)
      } else {
        remaining.push(item)
      }
    }

    pendingBindQueue.value = remaining

    // 清理超时的队列项（30 秒）
    const now = Date.now()
    pendingBindQueue.value = pendingBindQueue.value.filter(q => now - q.createdAt < 30000)
  }

  return {
    pendingApprovals,
    pendingBindQueue,
    hasPending,
    handleApprovalEvent,
    executeApproval,
    updateApprovalState,
    restoreFromSession,
    restoreFromSSEHistory,
    cleanupExpired,
    clearBySource,
    clearByTaskId,
    clearAll,
    flushPendingBindQueue,
  }
})
