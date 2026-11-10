import { useSessionStore } from '@/stores/session'
import { getInterruptId } from '@/utils/messageOperations'
import { logger } from '@/utils/logger'
import { ElMessage } from 'element-plus'
import { resolveEffectiveSessionId } from './sessionRouting'
import { formatApprovalTimeoutHint, APPROVAL_SYNC_DEBOUNCE_MS, resolveApprovalExpiryMs } from './constants'

/**
 * 审批事件消费入口（ARCH-13 拆分：WS/SSE 审批事件处理的唯一权威）
 *
 * 职责：handleApprovalEvent 按 eventType 分发到 timeout/processing/waiting/
 * processed/new 五个分支，维护 pendingApprovals 状态权威并按双域路由
 * （sessionId → sessionStore，taskId → researchStore）同步 UI 状态。
 *
 * @param {Object} ctx - 依赖上下文
 * @param {Object} ctx.pendingApprovals - 统一待审批 Map（ref）
 * @param {Set<string>} ctx._timeoutResumeKeys - 已触发超时恢复的去重键集合
 * @param {Object} ctx._approvalSyncTimers - debounced 同步定时器表
 * @param {() => Promise<Object>} ctx.getResearchStore - 惰性解析 research store
 * @param {Function} ctx.executeApproval - 统一审批执行（restActions 提供，
 *   _triggerTimeoutResume 复用 REST 恢复流路径）
 * @returns {{ handleApprovalEvent: (data: Object, options?: Object) => void }}
 */
export const createEventHandlers = (ctx) => {
  const {
    pendingApprovals,
    _timeoutResumeKeys,
    _approvalSyncTimers,
    getResearchStore,
    executeApproval,
  } = ctx

  /** debounced 同步到后端 — 审批事件到达后立即同步，确保跨浏览器/刷新可恢复 */
  const _debouncedApprovalSync = (sessionStore, sessionId, messageBackendId) => {
    if (_approvalSyncTimers[sessionId]) clearTimeout(_approvalSyncTimers[sessionId])
    _approvalSyncTimers[sessionId] = setTimeout(() => {
      if (messageBackendId) {
        sessionStore.syncMessageByBackendIdToBackend(sessionId, messageBackendId).catch(() => {})
      } else {
        sessionStore.syncLastMessageToBackend(sessionId).catch(() => {})
      }
      delete _approvalSyncTimers[sessionId]
    }, APPROVAL_SYNC_DEBOUNCE_MS)
  }

  /**
   * 统一处理审批相关 SSE/WS 事件（approval / approval_timeout / approval_processed）
   *
   * F⑥（ARCH-13）：入站数据已由事件入口统一 toCamelCase 转换
   * （WebSocket 经 useRealtimeSync，SSE 经 readSSEStream 入口），此处不再二次转换。
   *
   * @param {Object} data - 审批事件数据（camelCase）
   * @param {Object} options - 上下文信息
   * @param {string} options.source - 事件来源 'chat' | 'deep_research'
   * @param {string} [options.sessionId] - 聊天会话 ID（chat 来源必传）
   * @param {string} [options.taskId] - 深度研究任务 ID（deep_research 来源必传）
   * @param {boolean} [options.isReplay] - 历史回放标记（useRealtimeSync 注入）：
   *   回放事件仅用于状态重建，不触发 ElMessage / 消息追加 / 恢复流等副作用
   */
  const handleApprovalEvent = (data, options = {}) => {
    const { source = 'chat', sessionId, taskId, isReplay = false } = options
    const eventType = data.type || data.state

    // 审批超时
    if (eventType === 'approval_timeout' || data.state === 'timeout') {
      _handleTimeout(data, source, sessionId, isReplay)
      return
    }

    // 审批正在处理（另一端已点击确认/拒绝，后端处理中）
    if (eventType === 'approval_processing') {
      _handleProcessing(data, source, sessionId)
      return
    }

    // 批量审批等待（同批次部分已审批，其他工具仍 pending）
    if (eventType === 'approval_waiting') {
      _handleWaiting(data, source, sessionId)
      return
    }

    // 审批已处理（另一端已操作完成：approval_processed / approval_approved / approval_rejected）
    if (eventType === 'approval_processed'
        || eventType === 'approval_approved'
        || eventType === 'approval_rejected') {
      _handleProcessed(data, source, sessionId, taskId)
      return
    }

    // 新审批请求
    _handleNewApproval(data, source, sessionId, taskId)
  }

  /**
   * 新审批请求
   */
  const _handleNewApproval = (data, source, sessionId, taskId) => {
    const sessionStore = useSessionStore()
    const toolCallId = getInterruptId(data)
    if (!toolCallId) return

    // 去重：已存在相同 toolCallId 的 pending 审批，跳过（双端 SSE 可能同时推送）
    const existing = pendingApprovals.value.get(toolCallId)
    if (existing && existing.approvalData?.state === 'pending') {
      return
    }

    const isDeepResearch = data.source === 'deep_research' || source === 'deep_research'
    const approvalData = { ...data, state: 'pending' }

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
    const now = Date.now()

    pendingApprovals.value.set(toolCallId, {
      source: effectiveSource,
      taskId: effectiveTaskId,
      sessionId: entrySessionId,
      approvalData,
      createdAt: now,
      // 后端 payload 的真实超时点（ISO 字符串解析），缺失回退 createdAt+常量
      expiresAt: resolveApprovalExpiryMs(data.expiresAt, now),
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
      getResearchStore()
        .then((rs) => rs.setApprovalToToolCall(effectiveTaskId, toolCallId, approvalData))
        .catch((e) => logger.warn('[ApprovalStore] 同步 researchStore 审批数据失败:', e))
    }
  }

  /**
   * 审批超时处理
   *
   * @param {Object} data - 审批事件数据
   * @param {string} source - 事件来源 'chat' | 'deep_research'
   * @param {string|null} sessionId - 聊天会话 ID
   * @param {boolean} [isReplay=false] - 历史回放标记：回放事件仅用于状态重建，
   *   跳过 ElMessage / 消息追加 / 恢复流等副作用（否则重新打开页面重复弹超时提示）。
   *   状态更新（updateToolCallApprovalState 等）仍执行，保证状态重建正确。
   */
  const _handleTimeout = (data, source, sessionId, isReplay = false) => {
    const sessionStore = useSessionStore()
    const toolCallId = getInterruptId(data)

    // 从 pendingApprovals Map 中获取已有条目的 sessionId
    const existingEntry = toolCallId ? pendingApprovals.value.get(toolCallId) : null

    // 幂等保护（P-FE-7）：pendingApprovals 中已无该审批条目时直接返回。
    // 同一审批超时事件可能经 WebSocket / SSE 主聊天流 / 审批恢复流多条路径重复到达，
    // 首次处理已删除条目，后续重复到达不应再重复写 store / 重复同步后端。
    if (toolCallId && !existingEntry) return

    // 双域路由（F⑤：effectiveSessionId 推导单一实现，见 sessionRouting.js）
    const effectiveSessionId = resolveEffectiveSessionId(sessionId, source, existingEntry, sessionStore)

    // 超时恢复需要完整 approvalData（含 graphInterruptId / action 等字段），在删除条目前捕获
    const approvalData = existingEntry?.approvalData || data

    if (toolCallId) {
      pendingApprovals.value.delete(toolCallId)
    }

    // 更新 session store 中的审批状态
    if (effectiveSessionId) {
      // 回放事件不追加超时提示到消息内容（重新打开页面消息会重复出现超时提示）
      if (!isReplay) {
        sessionStore.appendToLastMessage(
          effectiveSessionId,
          `\n\n> ⏰ ${formatApprovalTimeoutHint(data.toolName)}\n`
        )
      }
      if (toolCallId) {
        sessionStore.updateToolCallApprovalState(effectiveSessionId, toolCallId, 'timeout')
        sessionStore.setApprovalToLastMessage(effectiveSessionId, { ...data, state: 'timeout' })
      }
      // 同步审批状态到后端，防止刷新后状态丢失
      sessionStore.syncLastMessageToBackend(effectiveSessionId).catch(() => {})
    } else if (existingEntry?.taskId) {
      // 独立 deep_research：同步超时状态到 researchStore，
      // 否则 toolCall.approval.state 残留 pending 导致 isWaiting 仍为 true，审批面板不消失
      getResearchStore()
        .then((rs) => rs.updateToolCallApprovalState(existingEntry.taskId, toolCallId, 'timeout'))
        .catch((e) => logger.warn('[ApprovalStore] 同步 researchStore timeout 失败:', e))
    }

    // 深度研究来源额外提示（回放事件不弹，避免重新打开页面重复弹超时提示）
    if (!isReplay && (source === 'deep_research' || data.source === 'deep_research')) {
      ElMessage.warning(formatApprovalTimeoutHint(data.toolName))
      return
    }

    // 回放事件不触发恢复流：历史超时已完成，恢复流应在实时事件路径触发
    if (isReplay) return

    // 事件驱动化超时恢复（Task 5）：chat 来源审批超时后，由前端收到 approval_timeout
    // 事件触发 SSE resume 端点驱动恢复流（与用户手动确认/拒绝同构，不再依赖 Celery）。
    // 后端 resume 端点会判定批次完整性：批次未决断返回 waiting（等待后续超时事件），
    // 全部决断后返回 SSE 流驱动 agent 继续执行。
    if (effectiveSessionId) {
      _triggerTimeoutResume(approvalData, effectiveSessionId)
    }
  }

  /**
   * 触发 chat 审批超时恢复（Task 5 事件驱动化）
   *
   * 复用 SSE resume 端点（POST /approvals/{interrupt_id}/resume/，timeout_resume=true）：
   * - 后端已把审批终态化 TIMEOUT，resume_approval 跳过幂等直接驱动恢复流；
   * - 审批状态保持 TIMEOUT 终态展示（不在前端覆盖为 processing/rejected）；
   * - 同批次其他工具超时事件到达时重复触发，由后端批次完整性判定（waiting）过滤，
   *   实际恢复流只驱动一次（后端恢复流去重锁 + 本函数去重键）。
   *
   * @param {Object} approvalData - 审批数据（含 graphInterruptId / action 等字段）
   * @param {string} sessionId - chat 会话 ID
   */
  const _triggerTimeoutResume = async (approvalData, sessionId) => {
    const graphInterruptId = approvalData.graphInterruptId || approvalData.extra?.graphInterruptId || ''
    const interruptId = getInterruptId(approvalData)
    const dedupKey = `${sessionId}:${graphInterruptId || interruptId || ''}`
    if (_timeoutResumeKeys.has(dedupKey)) return

    try {
      await executeApproval(approvalData, false, null, { sessionId, timeoutResume: true })
      // executeApproval 正常返回：恢复信令已发布（agent 由执行服务恢复，输出经 WebSocket 同步）
      _timeoutResumeKeys.add(dedupKey)
    } catch (err) {
      // waiting：同批次仍有 pending 审批，批次未决断——不标记，后续超时事件会再次触发
      if (err?.__approvalWaiting) return
      // 幂等（已被其他浏览器/路径触发）：已实际恢复，标记防重复
      if (err?.__approvalIdempotent) {
        _timeoutResumeKeys.add(dedupKey)
        return
      }
      logger.warn(
        `[ApprovalStore] 超时恢复触发失败，允许重试: session=${sessionId}, interruptId=${interruptId}`, err
      )
      _timeoutResumeKeys.delete(dedupKey)
    }
  }

  /**
   * 审批正在处理（另一端已点击确认/拒绝，后端处理中）
   *
   * 收到 approval_processing 事件时，更新本地审批状态为 processing，
   * 但保留在 pendingApprovals 中（审批面板保持显示，按钮禁用）。
   * 工具结果到达后（approval_processed / tool_result），审批条目才会被移除。
   *
   * @param {Object} data - 审批事件数据
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

    // 双域路由（F⑤：effectiveSessionId 推导单一实现，见 sessionRouting.js）
    const effectiveSessionId = resolveEffectiveSessionId(sessionId, source, existingEntry, sessionStore)
    if (effectiveSessionId) {
      sessionStore.updateToolCallApprovalState(effectiveSessionId, toolCallId, 'processing')
      sessionStore.setApprovalToLastMessage(effectiveSessionId, { ...data, state: 'processing' })
      sessionStore.syncLastMessageToBackend(effectiveSessionId).catch(() => {})
    } else if (existingEntry?.taskId) {
      // 独立 deep_research：同步审批状态到 researchStore（跨浏览器按钮禁用一致性）
      getResearchStore()
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
    // 双域路由（F⑤：effectiveSessionId 推导单一实现，见 sessionRouting.js）
    const effectiveSessionId = resolveEffectiveSessionId(sessionId, source, existingEntry, sessionStore)
    if (effectiveSessionId) {
      sessionStore.updateToolCallApprovalState(effectiveSessionId, toolCallId, 'waiting')
      sessionStore.setApprovalToLastMessage(effectiveSessionId, { ...data, state: 'waiting' })
      sessionStore.syncLastMessageToBackend(effectiveSessionId).catch(() => {})
    } else if (existingEntry?.taskId) {
      // 独立 deep_research：同步"等待同批"状态到 researchStore
      getResearchStore()
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
  const _handleProcessed = (data, source, sessionId, taskId) => {
    const sessionStore = useSessionStore()
    const toolCallId = getInterruptId(data)
    if (!toolCallId) return

    // 从 pendingApprovals Map 中获取已有条目的 sessionId（可能比参数中的更准确）
    const existingEntry = pendingApprovals.value.get(toolCallId)

    // 确定最终状态：优先使用 data.state，其次 data.approved，默认 rejected
    const finalState = data.state === 'approved' ? 'approved'
      : data.state === 'rejected' ? 'rejected'
      : data.approved ? 'approved' : 'rejected'

    // 幂等保护（P-FE-7）：pendingApprovals 中已无该审批条目时，仅当存在可路由
    // 的任务/会话上下文才兜底同步终态；否则直接返回。
    // 例外场景：触发浏览器点击审批时 executeApproval 已提前删除条目（executeApproval 主流程），
    // 终态事件到达时无条目可查——若不兜底，researchStore 中 toolCall.approval.state
    // 停留 processing，审批面板残留 disabled 按钮，与非触发浏览器（正常更新为
    // approved、面板消失）不一致（"审批后缺少组件"跨浏览器差异根因）。
    if (!existingEntry) {
      const isDeepResearch = source === 'deep_research' || data.source === 'deep_research'
      if (isDeepResearch && taskId) {
        getResearchStore()
          .then((rs) => rs.updateToolCallApprovalState(taskId, toolCallId, finalState))
          .catch((e) => logger.warn('[ApprovalStore] 同步 researchStore 终态失败(无条目):', e))
        return
      }
      const fallbackSessionId = data.sessionId
        || data.chatSessionId
        || data.crossModuleId
        || sessionId
        || sessionStore.currentSessionId
      if (fallbackSessionId) {
        sessionStore.updateToolCallApprovalState(fallbackSessionId, toolCallId, finalState)
      }
      return
    }

    // 双域路由（F⑤：effectiveSessionId 推导单一实现，见 sessionRouting.js）
    const effectiveSessionId = resolveEffectiveSessionId(sessionId, source, existingEntry, sessionStore)

    pendingApprovals.value.delete(toolCallId)

    // 更新 session store 中的审批状态
    if (effectiveSessionId) {
      sessionStore.updateToolCallApprovalState(effectiveSessionId, toolCallId, finalState)
      sessionStore.setApprovalToLastMessage(effectiveSessionId, { ...data, state: finalState })
      // 同步审批状态到后端，防止刷新后状态丢失
      sessionStore.syncLastMessageToBackend(effectiveSessionId).catch(() => {})
    } else if (existingEntry?.taskId) {
      // 独立 deep_research：同步终态审批状态到 researchStore（跨浏览器一致性）
      getResearchStore()
        .then((rs) => rs.updateToolCallApprovalState(existingEntry.taskId, toolCallId, finalState))
        .catch((e) => logger.warn('[ApprovalStore] 同步 researchStore 终态失败:', e))
    }
  }

  return {
    handleApprovalEvent,
  }
}
