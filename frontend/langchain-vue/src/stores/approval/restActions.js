import { useSessionStore } from '@/stores/session'
import { resumeApprovalStream } from '@/api/approval'
import { getInterruptId } from '@/utils/messageOperations'
import { logger } from '@/utils/logger'
import { ElMessage } from 'element-plus'
import { StreamState } from '@/types'
import { getResearchStore } from './state'
import { resolveApprovalExpiryMs } from './constants'

/**
 * 审批执行（REST 封装，ARCH-13 拆分）
 *
 * 职责：executeApproval 统一入口 + /approvals/{interrupt_id}/resume/ REST 调用 +
 * 审批串行执行队列（按 sessionId 隔离）。
 *
 * @param {Object} ctx - 依赖上下文
 * @param {Object} ctx.pendingApprovals - 统一待审批 Map（ref）
 * @param {Map<string, Promise>} ctx._approvalQueues - 审批串行执行队列
 * @returns {{
 *   executeApproval: (approval: Object, approved: boolean, userInput?: string|null, options?: Object) => Promise<void>,
 * }}
 */
export const createRestActions = (ctx) => {
  const { pendingApprovals, _approvalQueues } = ctx

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
    next
      .finally(() => {
        if (_approvalQueues.get(sessionId) === next) {
          _approvalQueues.delete(sessionId)
        }
      })
      .catch(() => {
        // 队列清理 promise 不参与业务错误传播：next 的 rejection 已由调用方
        // await/catch 处理（如 waiting_for_others 带 __approvalWaiting 标记的
        // 正常等待分支）。finally 派生的 promise 必须消费 rejection，
        // 否则会变成 Unhandled Promise Rejection 污染控制台。
      })
    return next
  }

  /**
   * 统一审批执行
   * @param {Object} approval - 审批数据对象
   * @param {boolean} approved - true=确认, false=拒绝
   * @param {string|null} userInput - 用户输入（confirm_with_input 模式）
   * @param {Object} options - 额外参数
   * @param {string} [options.taskId] - 深度研究任务 ID（覆盖 store 中的值）
   * @param {string} [options.sessionId] - 会话 ID（调用方显式指定，如超时恢复/详情页）
   * @param {boolean} [options.timeoutResume] - 超时恢复标记（不重置审批状态）
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

    // 超时恢复标记（Task 5 事件驱动化）：
    // 审批已终态 TIMEOUT，前端触发恢复流驱动 agent 继续执行，
    // 但不重置审批状态为 processing、不覆盖为 rejected（保持"已超时"终态展示）。
    const timeoutResume = !!options.timeoutResume

    // 标记为 processing（timeoutResume 时跳过——审批已终态，无需本地标记处理中）
    // 数据源归属互斥路由（与 _handleTimeout/_handleProcessing/_handleWaiting/_handleProcessed 对齐）：
    // - sessionId 存在（chat / learning / 聊天触发的深度研究）→ 数据源是 sessionStore 消息 toolCalls，
    //   只更新 sessionStore，不调用 researchStore（该任务不存在于 researchStore.tasks，
    //   详情页 taskToolCalls 同样从 sessionStore 消息读取）
    // - 仅 taskId（独立深度研究）→ 数据源是 researchStore.tasks，只更新 researchStore
    if (!timeoutResume) {
      approvalData.state = 'processing'
      if (sessionId) {
        sessionStore.updateToolCallApprovalState(sessionId, toolCallId, 'processing')
        sessionStore.setApprovalToLastMessage(sessionId, { ...approvalData, state: 'processing' })
      } else if (taskId) {
        try {
          const researchStore = await getResearchStore()
          researchStore.updateToolCallApprovalState(taskId, toolCallId, 'processing')
        } catch (e) { logger.warn('[ApprovalStore] 更新 researchStore processing 失败:', e) }
      }
    }
    pendingApprovals.value.delete(toolCallId)

    try {
      // 统一路径：所有审批（chat / deep_research）走 /approvals/{interrupt_id}/resume/
      // 后端 ApprovalGateway 根据 Approval.source 路由：
      // - chat → SSE 流式恢复（_stream_chat_resume_generator）
      // - deep_research → Celery 任务恢复（research_resume_task），返回 JSON
      // 同一 session 串行执行，避免多个 SSE 流并发写入同一消息
      await _enqueueApprovalExecution(sessionId, () =>
        _executeApprovalStream(approvalData, approved, userInput, sessionId, toolCallId)
      )

      // 仅在拒绝时更新状态为 rejected
      // 通过时不设 approved：SSE 流中 tool_result 事件已自行更新状态，
      // 设为 approved 会覆盖"处理中"状态，导致 P1（触发浏览器显示"已确认"而非"处理中"）
      // 数据源归属互斥路由（与 processing 标记一致）：sessionId 优先，独立深度研究走 taskId
      // timeoutResume 时跳过：审批保持 TIMEOUT 终态（后端恢复流结束会广播终态事件）
      if (!approved && sessionId && !timeoutResume) {
        sessionStore.updateToolCallApprovalState(sessionId, toolCallId, 'rejected')
        sessionStore.setApprovalToLastMessage(sessionId, { ...approvalData, state: 'rejected' })
      } else if (!approved && taskId && !timeoutResume) {
        try {
          const researchStore = await getResearchStore()
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
      // waiting_for_others / idempotent：
      // 状态已在 _executeApprovalStream 内部处理（processing / 实际状态 / timeout 等），
      // 不应回退为 pending，也不需要再次同步消息
      if (err?.__approvalWaiting || err?.__approvalIdempotent) {
        return
      }
      logger.error(`[ApprovalStore] 审批${approved ? '确认' : '拒绝'}失败:`, err)
      // 超时恢复失败：审批已终态 TIMEOUT，不回退为 pending（无意义且会覆盖终态）
      if (timeoutResume) return
      // 恢复审批状态（数据源归属互斥路由，与 processing/rejected 一致）
      const now = Date.now()
      pendingApprovals.value.set(toolCallId, {
        source,
        taskId,
        sessionId,
        approvalData: { ...approvalData, state: 'pending' },
        createdAt: now,
        // 保留后端原超时点：审批失败回退 pending 不重置后端计时，
        // 仍以 payload expiresAt（若 approvalData 携带）为准
        expiresAt: resolveApprovalExpiryMs(approvalData.expiresAt, now),
      })
      if (sessionId) {
        sessionStore.updateToolCallApprovalState(sessionId, toolCallId, 'pending')
        sessionStore.setApprovalToLastMessage(sessionId, { ...approvalData, state: 'pending' })
      } else if (taskId) {
        try {
          const researchStore = await getResearchStore()
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
   * 执行与连接解耦：chat 与 deep_research 审批均走此路径，后端 ApprovalGateway
   * 发布 Redis 信令唤醒执行服务后统一返回 JSON（不再返回 SSE 流）：
   * - waiting_for_others：批量审批等待（同批次其他工具待审批）
   * - idempotent：审批已被另一端处理（幂等响应）
   * - resumed：恢复信令已发布（chat agent 由 FastAPI 执行服务恢复 /
   *   deep_research Celery 任务恢复），agent 输出经 WebSocket 广播同步
   * - 错误响应
   *
   * 状态语义：
   * - waiting_for_others：本工具已审批，等待同批次其他工具审批完成后开始执行。
   *   UI 应保留为 `processing` 状态（不切到 approved/rejected），并通过 message 提示用户。
   *   通过抛出带 `__approvalWaiting` 标记的错误，让外层 `executeApproval` 跳过最终状态更新。
   * - idempotent：审批已被另一端处理。UI 直接更新为最终状态，外层跳过最终状态更新。
   * - resumed（chat）：恢复信令已发布，前端将消息 INTERRUPTED → STREAMING 后返回，
   *   实际 agent 输出（content/工具/推理/审批）经 WebSocket 由 sync store 消费，
   *   消息最终化由发起 sendMessage 的主流程在收到 stream_completed 后统一完成。
   * - resumed（deep_research）：研究恢复任务已启动。正常返回，外层 executeApproval 更新最终状态，
   *   实际 agent 输出经 WebSocket 推送到 deep research 页面消费。
   *
   * @returns {Promise<void>}
   * @throws {Error} 审批失败时抛出；带 `__approvalWaiting` / `__approvalIdempotent`
   *                 标记的错误表示状态已在内部处理，外层应跳过最终状态更新。
   */
  const _executeApprovalStream = async (approvalData, approved, userInput, sessionId, toolCallId) => {
    const interruptId = getInterruptId(approvalData)
    const sessionStore = useSessionStore()
    // 审批恢复仅需决策本身：后端 ApprovalWriteSerializer 白名单只接受 approved / user_input，
    // 其余模型/工具配置由执行服务挂起的图状态持有（执行与连接解耦后前端无需重传），
    // 前端审批数据（WebSocket 通道）也不再依赖请求级工具配置字段。
    const requestBody = { approved }
    if (approved && approvalData.action === 'confirm_with_input' && userInput !== null) {
      requestBody.userInput = userInput
    }

    const approvalAbortController = new AbortController()
    const response = await resumeApprovalStream(interruptId, requestBody, {
      signal: approvalAbortController.signal,
    })

    // 执行与连接解耦后统一返回 JSON（axios 拦截器已转换 camelCase）：
    // - waiting_for_others：批量审批等待（同批次其他工具待审批）
    // - idempotent：审批已被另一端处理（幂等响应）
    // - resumed：恢复信令已发布（chat / deep_research 统一走 Redis 信令），
    //   agent 输出经 WebSocket 广播，前端无需再消费 SSE 流
    const resData = response.data?.data || {}

    // 批量审批等待：本工具已审批，等待同批次其他工具
    // P19 修复：waiting 状态应保持 'waiting' 而非覆盖为 'processing'
    // 这样 ToolCallCard 的 isWaitingForSiblings 才能检测到并显示提示文字
    if (resData.status === 'waiting_for_others' || resData.state === 'waiting') {
      if (sessionId) {
        sessionStore.updateToolCallApprovalState(sessionId, toolCallId, 'waiting')
        sessionStore.setApprovalToLastMessage(sessionId, { ...resData, state: 'waiting' })
        sessionStore.syncLastMessageToBackend(sessionId, { allowCreate: false }).catch(() => {})
      }
      ElMessage.info(resData.message || '本工具已审批，等待同批次其他工具审批完成后开始执行...')
      const err = new Error('waiting_for_others')
      err.__approvalWaiting = true
      throw err
    }

    // 幂等响应：审批已被另一端处理，按返回的实际状态更新 UI
    if (resData.idempotent) {
      const idempotentState = resData.state === 'approved' ? 'approved'
        : resData.state === 'rejected' ? 'rejected'
        : resData.state === 'waiting' ? 'waiting'
        : 'processing'
      if (sessionId) {
        pendingApprovals.value.delete(toolCallId)
        sessionStore.updateToolCallApprovalState(sessionId, toolCallId, idempotentState)
        sessionStore.setApprovalToLastMessage(sessionId, { ...resData, state: idempotentState })
        sessionStore.syncLastMessageToBackend(sessionId, { allowCreate: false }).catch(() => {})
      }
      logger.info(`[ApprovalStore] 审批幂等响应：${toolCallId} 状态=${idempotentState}`)
      const err = new Error('idempotent')
      err.__approvalIdempotent = true
      throw err
    }

    // 审批恢复已启动（status: 'resumed'，chat / deep_research 统一）
    // chat：agent 由 FastAPI 执行服务恢复，输出经 WebSocket 同步（stream_* / tool_call_* /
    //   approval_* / stream_completed），前端只需将消息 INTERRUPTED → STREAMING 等待
    //   执行服务广播 stream_completed；最终化由发起 sendMessage 的主流程统一完成。
    // deep_research：Celery 任务恢复，前端无需消费流，实际输出经 WebSocket 推送到研究页。
    if (resData.status === 'resumed') {
      if (sessionId) {
        // 动态 import 破环：sync.js 装配了 approval 事件处理器，approval store 不应静态依赖它
        const { useSyncStore } = await import('../sync')
        const syncStore = useSyncStore()
        // 标记流式恢复：阻止 WebSocket 事件在审批恢复期间被误判为乱序 / 触发快照覆盖
        syncStore.startStreaming(sessionId)
        // 恢复中断的流：INTERRUPTED → STREAMING，等待 stream_event / stream_completed 推进
        sessionStore.setStreamStateToLastMessage(sessionId, StreamState.STREAMING)
      } else {
        ElMessage.success(response.data?.message || '研究恢复任务已启动')
      }
      logger.info(`[ApprovalStore] 审批恢复任务已启动: ${toolCallId}, source=${sessionId ? 'chat' : 'deep_research'}`)
      return
    }

    // 其他未知 JSON 响应：作为错误抛出
    throw new Error(response.data?.message || resData.message || '审批处理失败')
  }

  return {
    executeApproval,
  }
}
