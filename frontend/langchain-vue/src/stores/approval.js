import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useSessionStore } from './session'
import { useModelStore } from './model'
import { useSyncStore } from './sync'
import { useStreamFinalizer } from '../composables/useStreamFinalizer'
import { resumeApprovalStream } from '../api/approval'
import { approveResearchCommand, rejectResearchCommand } from '../api/research'
import { getInterruptId } from '../utils/message-operations'
import { readSSEStream } from '../utils/sse'
import { StreamState } from '../types'
import { logger } from '../utils/logger'
import { ElMessage, ElNotification } from 'element-plus'

/** 审批过期时间：30 分钟 */
const APPROVAL_EXPIRY_MS = 30 * 60 * 1000
/** 清理检查间隔：5 分钟 */
const CLEANUP_INTERVAL_MS = 5 * 60 * 1000

/** debounced 同步到后端 — 审批事件到达后立即同步，确保跨浏览器/刷新可恢复 */
const _approvalSyncTimers = {}
const _debouncedApprovalSync = (sessionStore, sessionId) => {
  if (_approvalSyncTimers[sessionId]) clearTimeout(_approvalSyncTimers[sessionId])
  _approvalSyncTimers[sessionId] = setTimeout(() => {
    sessionStore.syncLastMessageToBackend(sessionId).catch(() => {})
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
   * 聊天审批串行执行队列（按 sessionId 隔离）
   *
   * 同一会话的审批恢复流必须串行执行，避免多个 SSE 流并发写入同一会话
   * 的最后一条消息（content/toolCalls 竞态）。不同会话可并行。
   *
   * 结构：Map<sessionId, Promise>
   */
  const _chatApprovalQueues = new Map()

  /**
   * 将聊天审批执行入队（同一 session 串行）
   * @param {string} sessionId
   * @param {() => Promise} fn - 审批执行函数
   * @returns {Promise} fn 的返回值
   */
  const _enqueueChatApproval = (sessionId, fn) => {
    if (!sessionId) return fn()
    const prev = _chatApprovalQueues.get(sessionId) || Promise.resolve()
    // then(onFulfilled, onRejected)：前一个无论成功/失败都继续执行下一个
    const next = prev.then(fn, fn)
    _chatApprovalQueues.set(sessionId, next)
    next.finally(() => {
      if (_chatApprovalQueues.get(sessionId) === next) {
        _chatApprovalQueues.delete(sessionId)
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
    const eventType = data.type || data.state

    // 审批超时
    if (eventType === 'approval_timeout' || data.state === 'timeout') {
      _handleTimeout(data, source, sessionId)
      return
    }

    // 审批正在处理（另一端已点击确认/拒绝，后端处理中）
    if (eventType === 'approval_processing') {
      _handleProcessing(data, source, sessionId)
      return
    }

    // 审批已处理（另一端已操作完成：approval_processed / approval_approved / approval_rejected）
    if (eventType === 'approval_processed'
        || eventType === 'approval_approved'
        || eventType === 'approval_rejected') {
      _handleProcessed(data, source, sessionId)
      return
    }

    // 新审批请求
    _handleNewApproval(data, source, sessionId, taskId, baseApproval)
  }

  /**
   * 新审批请求
   */
  const _handleNewApproval = (data, source, sessionId, taskId, baseApproval) => {
    const sessionStore = useSessionStore()
    const toolCallId = getInterruptId(data)
    if (!toolCallId) return

    // 去重：已存在相同 interruptId 的 pending 审批，跳过（双端 SSE 可能同时推送）
    const existing = pendingApprovals.value.get(toolCallId)
    if (existing && existing.approvalData?.state === 'pending') {
      return
    }

    const isDeepResearch = data.source === 'deep_research' || source === 'deep_research'
    const approvalData = {
      ...data,
      state: 'pending',
      ...(isDeepResearch ? {} : {
        use_tools: baseApproval.use_tools ?? true,
        use_web_search: baseApproval.use_web_search ?? false,
        use_mcp: baseApproval.use_mcp ?? false,
        selected_mcp_servers: baseApproval.selected_mcp_servers ?? null,
        selected_tools: baseApproval.selected_tools ?? null,
        use_knowledge_base: baseApproval.use_knowledge_base ?? false,
        selected_knowledge_bases: baseApproval.selected_knowledge_bases ?? [],
      }),
    }

    const effectiveSource = isDeepResearch ? 'deep_research' : 'chat'
    // 优先使用事件数据中的 task_id（最准确），而非组件传入的 taskId（可能因切换任务而过时）
    const effectiveTaskId = data.task_id || taskId || null

    pendingApprovals.value.set(toolCallId, {
      source: effectiveSource,
      taskId: effectiveTaskId,
      sessionId: sessionId || sessionStore.currentSessionId,
      approvalData,
      createdAt: Date.now(),
    })

    // 同步到 session store（仅 chat 来源有 sessionId 时）
    if (sessionId) {
      sessionStore.setApprovalToToolCall(sessionId, toolCallId, approvalData)
      sessionStore.setApprovalToLastMessage(sessionId, { ...data, state: 'pending' })
      // 立即同步审批状态到后端，确保其他浏览器/刷新后可恢复
      _debouncedApprovalSync(sessionStore, sessionId)
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
    const effectiveSessionId = sessionId || existingEntry?.sessionId || sessionStore.currentSessionId

    if (toolCallId) {
      pendingApprovals.value.delete(toolCallId)
    }

    // 更新 session store 中的审批状态
    if (effectiveSessionId) {
      sessionStore.appendToLastMessage(
        effectiveSessionId,
        `\n\n> ⏰ 工具 "${data.tool_name || '未知'}" 的审批已超时，Agent 将使用其他方式继续\n`
      )
      if (toolCallId) {
        sessionStore.updateToolCallApprovalState(effectiveSessionId, toolCallId, 'timeout')
        sessionStore.setApprovalToLastMessage(effectiveSessionId, { ...data, state: 'timeout' })
      }
      // 同步审批状态到后端，防止刷新后状态丢失
      sessionStore.syncLastMessageToBackend(effectiveSessionId).catch(() => {})
    }

    // 深度研究来源额外提示
    if (source === 'deep_research' || data.source === 'deep_research') {
      ElMessage.warning(`工具 "${data.tool_name || '未知'}" 的审批已超时，Agent 将使用其他方式继续`)
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

    const effectiveSessionId = sessionId || existingEntry?.sessionId || sessionStore.currentSessionId
    if (effectiveSessionId) {
      sessionStore.updateToolCallApprovalState(effectiveSessionId, toolCallId, 'processing')
      sessionStore.setApprovalToLastMessage(effectiveSessionId, { ...data, state: 'processing' })
      sessionStore.syncLastMessageToBackend(effectiveSessionId).catch(() => {})
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
    const effectiveSessionId = sessionId || existingEntry?.sessionId || sessionStore.currentSessionId

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
      sessionStore.syncLastMessageToBackend(effectiveSessionId).catch(() => {})
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
    const sessionId = entry?.sessionId || sessionStore.currentSessionId
    const taskId = options.taskId || entry?.taskId || null

    // 防重复
    if (approvalData.state === 'processing') {
      logger.warn(`[ApprovalStore] 审批正在处理中，忽略重复操作: ${toolCallId}`)
      return
    }

    // 标记为 processing
    approvalData.state = 'processing'
    if (sessionId) {
      sessionStore.updateToolCallApprovalState(sessionId, toolCallId, 'processing')
      sessionStore.setApprovalToLastMessage(sessionId, { ...approvalData, state: 'processing' })
    }
    pendingApprovals.value.delete(toolCallId)

    const finalState = approved ? 'approved' : 'rejected'

    try {
      const interruptId = getInterruptId(approvalData)

      if (source === 'deep_research') {
        // 深度研究审批路径
        if (!taskId) {
          throw new Error('深度研究审批缺少 taskId，无法执行')
        }
        await _executeResearchApproval(taskId, interruptId, approved, userInput, approvalData)
      } else {
        // 聊天审批路径：同一 session 串行执行，避免多个 SSE 流并发写入同一消息
        await _enqueueChatApproval(sessionId, () =>
          _executeChatApproval(approvalData, approved, userInput, sessionId, toolCallId, options)
        )
      }

      // 更新最终状态
      if (sessionId) {
        sessionStore.updateToolCallApprovalState(sessionId, toolCallId, finalState)
        sessionStore.setApprovalToLastMessage(sessionId, { ...approvalData, state: finalState })
      }

      // 同步消息到后端
      if (sessionId) {
        try {
          await sessionStore.syncLastMessageToBackend(sessionId)
        } catch (syncErr) {
          logger.error('[ApprovalStore] 审批后同步消息失败:', syncErr)
        }
      }
    } catch (err) {
      // waiting_for_others / idempotent / interrupted：
      // 状态已在 _executeChatApproval 内部处理（processing / 实际状态 / timeout 等），
      // 不应回退为 pending，也不需要再次同步消息
      if (err?.__approvalWaiting || err?.__approvalIdempotent || err?.__approvalInterrupted) {
        return
      }
      logger.error(`[ApprovalStore] 审批${approved ? '确认' : '拒绝'}失败:`, err)
      // 恢复审批状态
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
        // 同步恢复后的状态到后端
        sessionStore.syncLastMessageToBackend(sessionId).catch(() => {})
      }
    }
  }

  /**
   * 深度研究审批执行
   */
  const _executeResearchApproval = async (taskId, interruptId, approved, userInput, approvalData) => {
    if (approved) {
      await approveResearchCommand(
        taskId,
        interruptId,
        approvalData.action === 'confirm_with_input' ? userInput : undefined,
      )
    } else {
      await rejectResearchCommand(taskId, interruptId)
    }
    ElMessage.success(approved ? '已确认操作' : '已拒绝操作')
  }

  /**
   * 聊天审批执行（统一调用 /approvals/{interrupt_id}/resume/ SSE 流式端点）
   *
   * 后端响应类型由 content-type 区分：
   * - `text/event-stream`：正常 SSE 流（agent 恢复执行）
   * - `application/json`：批量审批等待（waiting_for_others）/ 幂等响应（已处理审批）/ 错误响应
   *
   * 状态语义：
   * - waiting_for_others：本工具已审批，等待同批次其他工具审批完成后开始执行。
   *   UI 应保留为 `processing` 状态（不切到 approved/rejected），并通过 message 提示用户。
   *   通过抛出带 `__approvalWaiting` 标记的错误，让外层 `executeApproval` 跳过最终状态更新。
   * - idempotent：审批已被另一端处理。UI 直接更新为最终状态，外层跳过最终状态更新。
   * - interrupted：SSE 流中收到 approval_timeout / approval_processed 事件，流被中断。
   *   审批状态已在事件处理器中更新（timeout/approved/rejected），通过 `markInterrupted`
   *   保持消息为 INTERRUPTED 状态。抛出带 `__approvalInterrupted` 标记的错误，外层跳过最终状态更新。
   *
   * 流式生命周期复用 useStreamFinalizer（与 chat.js sendMessage 一致）：
   * - startStreaming → STREAMING → readSSEStream → finalizeStream (COMPLETED) / markInterrupted / ERROR
   *
   * @returns {Promise<void>}
   * @throws {Error} 审批失败时抛出；带 `__approvalWaiting` / `__approvalIdempotent` /
   *                 `__approvalInterrupted` 标记的错误表示状态已在内部处理，外层应跳过最终状态更新。
   */
  const _executeChatApproval = async (approvalData, approved, userInput, sessionId, toolCallId, options) => {
    const interruptId = getInterruptId(approvalData)
    const modelStore = useModelStore()
    const modelConfig = modelStore.getModelConfig()
    const syncStore = useSyncStore()
    const { finalizeStream, markInterrupted } = useStreamFinalizer()
    const sessionStore = useSessionStore()
    // interrupt_id 由 URL path 传递；session_id 由后端从 Approval.chat_session_id / source_id 读取
    const requestBody = {
      approved,
      provider_id: modelConfig.provider_id || null,
      model_name: modelConfig.model_name || null,
      use_deep_thinking: modelStore.thinkingEnabled,
      special_params: modelConfig.special_params ? { ...modelConfig.special_params } : null,
      temperature: modelConfig.temperature || null,
      max_tokens: modelConfig.max_tokens || null,
      use_tools: approvalData.use_tools ?? true,
      use_web_search: approvalData.use_web_search ?? false,
      use_mcp: approvalData.use_mcp ?? false,
      selected_mcp_servers: approvalData.selected_mcp_servers ?? null,
      selected_tools: approvalData.selected_tools ?? null,
      use_knowledge_base: approvalData.use_knowledge_base ?? false,
      selected_knowledge_bases: approvalData.selected_knowledge_bases ?? [],
    }
    if (approved && approvalData.action === 'confirm_with_input' && userInput !== null) {
      requestBody.user_input = userInput
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
      if (data.status === 'waiting_for_others' || data.state === 'waiting') {
        if (sessionId) {
          sessionStore.updateToolCallApprovalState(sessionId, toolCallId, 'processing')
          sessionStore.setApprovalToLastMessage(sessionId, { ...data, state: 'processing' })
          sessionStore.syncLastMessageToBackend(sessionId).catch(() => {})
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
          : data.state === 'waiting' ? 'processing'
          : 'processing'
        if (sessionId) {
          pendingApprovals.value.delete(toolCallId)
          sessionStore.updateToolCallApprovalState(sessionId, toolCallId, idempotentState)
          sessionStore.setApprovalToLastMessage(sessionId, { ...data, state: idempotentState })
          sessionStore.syncLastMessageToBackend(sessionId).catch(() => {})
        }
        logger.info(`[ApprovalStore] 审批幂等响应：${toolCallId} 状态=${idempotentState}`)
        const err = new Error('idempotent')
        err.__approvalIdempotent = true
        throw err
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
      switch (parsed.type) {
        case 'heartbeat':
          // readSSEStream 内部已通过 lastEventTime 维持心跳检测
          break
        case 'chunk':
          if (parsed.content) {
            options.onChatStreamChunk?.(parsed.content)
            sessionStore.appendToLastAssistantMessage(sessionId, parsed.content)
          }
          break
        case 'tool':
          options.onChatStreamTool?.(parsed.data)
          sessionStore.addOrUpdateToolCallToLastMessage(sessionId, parsed.data)
          break
        case 'tool_result':
          options.onChatStreamToolResult?.(parsed.data)
          sessionStore.updateOrAddToolResultToLastMessage(sessionId, parsed.data)
          break
        case 'reasoning':
          if (parsed.data?.content) {
            options.onChatStreamReasoning?.(parsed.data)
            sessionStore.setReasoningToLastMessage(sessionId, parsed.data)
          }
          break
        case 'approval':
          if (parsed.data) {
            // 审批流中又出现新审批
            handleApprovalEvent(parsed.data, {
              source: 'chat',
              sessionId,
              baseApproval: approvalData,
            })
          }
          break
        case 'error': {
          const errorMsg = parsed.message || parsed.data?.message || ''
          streamError = new Error(errorMsg || '审批处理失败')
          approvalAbortController.abort()
          break
        }
        case 'approval_timeout': {
          // 审批超时：终止当前流，标记 interrupted 由 finally 调用 markInterrupted
          interrupted = true
          const timeoutToolCallId = getInterruptId(parsed.data || parsed)
          if (timeoutToolCallId) {
            pendingApprovals.value.delete(timeoutToolCallId)
            sessionStore.updateToolCallApprovalState(sessionId, timeoutToolCallId, 'timeout')
            sessionStore.setApprovalToLastMessage(sessionId, { ...(parsed.data || parsed), state: 'timeout' })
            sessionStore.syncLastMessageToBackend(sessionId).catch(() => {})
          }
          approvalAbortController.abort()
          break
        }
        case 'approval_processed': {
          // 审批已在另一端处理：终止当前流，标记 interrupted 由 finally 调用 markInterrupted
          interrupted = true
          const processedToolCallId = getInterruptId(parsed.data || parsed)
          if (processedToolCallId) {
            pendingApprovals.value.delete(processedToolCallId)
            const processedData = parsed.data || parsed
            const finalState = processedData.approved ? 'approved' : 'rejected'
            sessionStore.updateToolCallApprovalState(sessionId, processedToolCallId, finalState)
            sessionStore.setApprovalToLastMessage(sessionId, { ...processedData, state: finalState })
            sessionStore.syncLastMessageToBackend(sessionId).catch(() => {})
          }
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
              taskId: approval.task_id || null,
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
      logger.warn('[ApprovalStore] restoreFromSSEHistory: 无法提取 interruptId', approvalData)
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

    logger.info(`[ApprovalStore] restoreFromSSEHistory: 恢复审批 ${toolCallId}, taskId=${taskId}, tool=${approvalData.tool_name}`)

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

  return {
    pendingApprovals,
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
  }
})
