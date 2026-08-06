import { logger } from '@/utils/logger'
import { mergeMessageFromBackend } from '@/utils/messageOperations'
import { ToolCallStatus, ApprovalState } from '@/types'
import { NON_TERMINAL_STATUSES, applyToolCallState } from '@/utils/toolCallStateMachine'
import { NON_TERMINAL_APPROVAL_STATES } from './constants'
import {
  findMessageById,
  getSession,
  getCurrentVersion,
} from './helpers'

/**
 * @typedef {import('@/composables/useRealtimeSync').RealtimeEvent} RealtimeEvent
 */

/**
 * 最终化深度研究结果回写场景的非终态 toolCalls（success 感知终态）
 *
 * 收敛自 streamStateHandlers.js handleStreamCompleted 深度研究回写分支的内联实现，
 * 与原实现行为完全一致。与 finalizeToolCallsForCompletedMessage（通用流式兜底）的差异：
 * - 审批终态：研究成功 → APPROVED，失败 → REJECTED（通用兜底无条件 → TIMEOUT）
 * - 工具终态：仅处理 WAITING/RUNNING，按 result/output 与 success 区分
 *   COMPLETED/FAILED（通用兜底将所有非终态 → COMPLETED）
 * - 不同步 toolCallsMap（原内联实现无此行为）
 *
 * @param {Object} message - 目标消息对象
 * @param {boolean} success - 深度研究是否成功（对应 payload.success !== false）
 * @returns {{ pendingApprovalCount: number, runningToolCount: number }} 清理前计数（供日志使用）
 */
export const finalizeToolCallsForResearchResult = (message, success) => {
  let pendingApprovalCount = 0
  let runningToolCount = 0

  if (message?.toolCalls && Array.isArray(message.toolCalls)) {
    for (const tc of message.toolCalls) {
      if (NON_TERMINAL_APPROVAL_STATES.includes(tc.approval?.state)) pendingApprovalCount++
      if (tc.status === ToolCallStatus.WAITING || tc.status === ToolCallStatus.RUNNING) runningToolCount++
      // 对于仍在 pending/processing/waiting 状态的审批，研究完成/失败后强制清理为终态
      if (tc.approval && NON_TERMINAL_APPROVAL_STATES.includes(tc.approval.state)) {
        tc.approval.state = success ? ApprovalState.APPROVED : ApprovalState.REJECTED
      }
      // 如果工具还是 waiting/running 状态，研究都结束了，根据实际情况设置。
      // 状态推进统一走状态机（add 模式），终态不回退由 applyToolCallState 保证
      if (tc.status === ToolCallStatus.WAITING || tc.status === ToolCallStatus.RUNNING) {
        const target = (tc.result || tc.output) ? ToolCallStatus.COMPLETED : (success ? ToolCallStatus.COMPLETED : ToolCallStatus.FAILED)
        const { status: nextStatus, applied } = applyToolCallState(tc, { status: target }, { mode: 'add' })
        if (applied && nextStatus !== undefined) tc.status = nextStatus
      }
    }
  }

  // 同步清理 versions 中的审批状态
  const ver = getCurrentVersion(message)
  if (ver?.toolCalls) {
    for (const tc of ver.toolCalls) {
      if (tc.approval && NON_TERMINAL_APPROVAL_STATES.includes(tc.approval.state)) {
        tc.approval.state = success ? ApprovalState.APPROVED : ApprovalState.REJECTED
      }
      if (tc.status === ToolCallStatus.WAITING || tc.status === ToolCallStatus.RUNNING) {
        const target = (tc.result || tc.output) ? ToolCallStatus.COMPLETED : (success ? ToolCallStatus.COMPLETED : ToolCallStatus.FAILED)
        const { status: nextStatus, applied } = applyToolCallState(tc, { status: target }, { mode: 'add' })
        if (applied && nextStatus !== undefined) tc.status = nextStatus
      }
    }
  }

  return { pendingApprovalCount, runningToolCount }
}

/**
 * 创建消息完整性兜底处理器
 *
 * 提供 toolCall 最终化与全量同步后完整性校验能力，供 streamStateHandlers 调用。
 *
 * @param {Object} ctx - 依赖上下文
 * @param {Object} ctx.sessionStore - session store 实例
 * @returns {{
 *   finalizeToolCallsForCompletedMessage: (message: Object, sessionId?: string) => void,
 *   verifyMessageIntegrityAfterSync: (sessionId: string, messageId: string, backendMessages: Array) => void,
 * }}
 */
export const createMessageIntegrityHandlers = (ctx) => {
  const { sessionStore } = ctx

  /**
   * 最终化消息中所有非终态 toolCalls（流式完成后兜底）
   *
   * 场景：WebSocket tool_call_completed 事件丢失或乱序，导致 toolCall.status
   * 卡在 pending/running/waiting，UI 永久显示"执行中"。
   *
   * 处理规则：
   * - 有 result/output 的 toolCall → status = COMPLETED
   * - 无 result/output 的 toolCall → status = COMPLETED（流式已结束，工具必然已完成）
   * - approval.state 为 pending/processing/waiting → 不强制修改（审批流程独立于工具执行）
   *   但若流式已结束且审批仍在等待中，说明审批可能已超时，标记为 timeout
   * - 同步更新 versions[currentVersion].toolCalls
   * - 同步更新 toolCallsMap（单一真相源），确保 UI 读取的状态一致
   *
   * @param {Object} message - 目标消息对象
   * @param {string} [sessionId] - 会话 ID（用于同步 toolCallsMap）
   */
  const finalizeToolCallsForCompletedMessage = (message, sessionId) => {
    if (!message?.toolCalls || !Array.isArray(message.toolCalls)) return

    let finalizedCount = 0
    // 流式结束时兜底：将所有非终态工具调用修正为 COMPLETED
    // RUNNING 也需纳入：审批通过但 tool_call_completed 事件丢失时，状态会卡在 'running'，
    // 流式已结束说明 agent 处理完毕，应兜底为 COMPLETED（issue_new_c 根因）

    for (const tc of message.toolCalls) {
      if (!tc) continue
      // 审批未放行执行（state 非 approved/completed）：工具尚未被确认执行，
      // 流式结束（如审批恢复流）不代表其已完成，不得兜底为 COMPLETED（P3-34/P3-35 根因修复）。
      // 覆盖 pending/processing/waiting（审批未完成，等待用户确认/拒绝）与
      // rejected/timeout（未执行，防止误标"已完成"）；审批超时/拒绝由后端
      // tool_call_timeout / tool_call_rejected 事件驱动，前端不代为判定。
      if (tc.approval
          && tc.approval.state !== 'approved'
          && tc.approval.state !== 'completed') {
        continue
      }
      if (NON_TERMINAL_STATUSES.has(tc.status)) {
        // 有结果 → COMPLETED，无结果也 → COMPLETED（流式已结束）。
        // 状态推进统一走状态机（add 模式），终态不回退由 applyToolCallState 保证
        const { status: nextStatus, applied } =
          applyToolCallState(tc, { status: ToolCallStatus.COMPLETED }, { mode: 'add' })
        if (applied && nextStatus !== undefined) {
          tc.status = nextStatus
          if (!tc.state) tc.state = 'output-available'
          finalizedCount++
        }
      }
    }

    // 同步到 versions[currentVersion].toolCalls
    const ver = getCurrentVersion(message)
    if (ver?.toolCalls && Array.isArray(ver.toolCalls)) {
      for (const verTc of ver.toolCalls) {
        if (!verTc) continue
        // 按 id/toolCallId 匹配并同步状态
        const matched = message.toolCalls.find(tc =>
          tc && (tc.id === verTc.id || tc.toolCallId === verTc.toolCallId)
        )
        if (matched) {
          verTc.status = matched.status
          if (matched.state) verTc.state = matched.state
          if (matched.approval) verTc.approval = { ...matched.approval }
        }
      }
    }

    if (finalizedCount > 0) {
      logger.info(
        `[Sync] _finalizeToolCallsForCompletedMessage: 最终化 ${finalizedCount} 个 toolCall: ` +
        `message=${message.backendId || message.id}`
      )
      // 同步更新 toolCallsMap（单一真相源），确保 UI 读取的状态与 message.toolCalls 一致
      if (sessionId) {
        const msgBackendId = message.backendId?.toString() || message.id?.toString()
        sessionStore.finalizeToolCallsInMap(sessionId, msgBackendId)
      }
    }
  }

  /**
   * 全量同步完成后校验消息完整性（tool_calls + content）
   *
   * requestFullSync 内部已通过 mergeMessageFromBackend 做字段级合并，
   * 此函数作为安全网，确保触发浏览器的 tool_calls 数量和 content 长度
   * 不低于后端权威快照（工具卡片完整性 + AI 内容完整性）。
   *
   * 触发场景：
   * - 流式期间 message_updated 选择性跳过 SSE 字段，可能导致 tool_calls 缺失
   * - 时序竞态导致 ChatMessage.tool_calls 不完整
   * - AI 文本内容在工具调用开始时被部分覆盖（SSE 流式期间 content 被截断）
   *
   * 注意：backendMessages 是 requestFullSync 在合并前保存的后端原始快照，
   * 不受本地保护态合并逻辑影响，可作为完整性校验的权威基准。
   *
   * @param {string} sessionId
   * @param {string} messageId - 目标消息 backendId 或 id
   * @param {Array} backendMessages - requestFullSync 返回的后端消息快照（合并前）
   */
  const verifyMessageIntegrityAfterSync = (sessionId, messageId, backendMessages) => {
    if (!sessionId || !messageId || !Array.isArray(backendMessages) || backendMessages.length === 0) return

    // 在 backendMessages 快照中查找对应消息（合并前的后端原始数据）
    const backendMsg = findMessageById({ messages: backendMessages }, messageId)
    if (!backendMsg) {
      logger.debug(`[Sync] 完整性校验: 未在后端快照中找到消息: session=${sessionId}, message=${messageId}`)
      return
    }

    // 在当前 session 中重新查找本地消息（requestFullSync 可能已替换消息对象引用）
    const session = getSession(sessionStore, sessionId)
    if (!session?.messages) return
    const localMsg = findMessageById(session, messageId)
    if (!localMsg) {
      logger.warn(`[Sync] 完整性校验: 本地未找到消息: session=${sessionId}, message=${messageId}`)
      return
    }

    // === tool_calls 完整性校验 ===
    // 后端 toolCalls 数量应等于或大于本地（后端是权威源）
    // 若本地缺失，强制调用 mergeMessageFromBackend 触发 mergeToolCalls 合并
    const backendToolCalls = Array.isArray(backendMsg.toolCalls) ? backendMsg.toolCalls : []
    const localToolCalls = Array.isArray(localMsg.toolCalls) ? localMsg.toolCalls : []
    if (backendToolCalls.length > localToolCalls.length) {
      logger.warn(
        `[Sync] 完整性校验: tool_calls 缺失，强制合并: session=${sessionId}, ` +
        `message=${messageId}, local=${localToolCalls.length}, backend=${backendToolCalls.length}`
      )
      // 强制合并：mergeMessageFromBackend 内部 mergeToolCalls 会保留本地已有结果与终态
      mergeMessageFromBackend(localMsg, { toolCalls: backendToolCalls })
      // 同步到 versions[currentVersion]
      const ver = getCurrentVersion(localMsg)
      if (ver) ver.toolCalls = localMsg.toolCalls
    }

    // === content 完整性校验 ===
    // 触发浏览器 SSE 流式期间可能因工具调用开始时事件覆盖导致 content 截断
    // 若本地 content 长度 < 后端 content 长度，以后端为准直接覆盖
    const backendContent = typeof backendMsg.content === 'string' ? backendMsg.content : ''
    const localContent = typeof localMsg.content === 'string' ? localMsg.content : ''
    if (backendContent.length > localContent.length) {
      logger.warn(
        `[Sync] 完整性校验: content 截断，以后端为准: session=${sessionId}, ` +
        `message=${messageId}, local=${localContent.length}, backend=${backendContent.length}`
      )
      localMsg.content = backendContent
      // 同步到 versions[currentVersion]，避免版本切换后回退到截断内容
      const ver = getCurrentVersion(localMsg)
      if (ver) ver.content = backendContent
    }
  }

  return {
    finalizeToolCallsForCompletedMessage,
    verifyMessageIntegrityAfterSync,
  }
}
