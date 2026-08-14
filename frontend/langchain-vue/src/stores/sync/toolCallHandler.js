import { logger } from '@/utils/logger'
import { TOOL_CALL_STATUS_MAP, TOOL_CALL_RESULT_STATUSES } from './constants'
import { getSession, ensureSessionLoaded } from './helpers'

/**
 * @typedef {import('@/composables/useRealtimeSync').RealtimeEvent} RealtimeEvent
 */

/**
 * 创建工具调用事件处理器（三模块共享：chat / deep_research / learning）
 *
 * 后端为每个 EventType 独立 ws_event_name，前端通过 event.type 直接区分
 * 11 个工具调用事件类型（含 rejected），映射到对应的 ToolCallStatus。
 * 工具调用唯一真相源为 sessionStore.toolCallsMap：WebSocket tool_call_* 事件
 * （ToolCallLifecycleService）与 SSE tool / tool_result 事件（请求浏览器恢复流、
 * 实时工具渲染）均写入同一幂等入口 addOrUpdateToolCallInMap / updateOrAddToolResultInMap，
 * 以 id/toolCallId 精确匹配幂等合并——双通道重复推送同一工具时不产生重复条目（Task 5.1）。
 *
 * 状态应用：本模块仅做"事件类型 → 目标状态"映射（TOOL_CALL_STATUS_MAP），
 * 状态推进/校验（合法转换、终态不回退）统一由唯一状态机
 * toolCallStateMachine.applyToolCallState 在写入入口内完成（核心层权威化，Task 2），
 * 本模块不自行推导/覆盖状态。
 *
 * 通过 sessionId / taskId 自动路由到 sessionStore 或 researchStore：
 * - sessionId 存在（chat / learning / 关联 deep_research）→ sessionStore
 * - 仅 taskId 存在（独立 deep_research）→ researchStore
 *
 * @param {Object} ctx - 依赖上下文
 * @param {Object} ctx.sessionStore - session store 实例
 * @param {Object} ctx.approvalStore - approval store 实例
 * @param {Object} ctx.researchStore - research store 实例
 * @returns {{
 *   handleToolCallEvent: (sessionId: string|null, taskId: string|null, payload: Object, eventType: string, source?: string) => Promise<void>,
 * }}
 */
export const createHandleToolCallEvent = (ctx) => {
  const { sessionStore, approvalStore, researchStore } = ctx

  /**
   * 处理工具调用事件
   *
   * @param {string|null} sessionId - 会话 ID（chat/learning/关联 deep_research）
   * @param {string|null} taskId - 深度研究任务 ID（独立 deep_research）
   * @param {Object} payload - 事件载荷
   * @param {string} eventType - 事件类型（tool_call_pending / tool_call_completed 等）
   * @param {string} source - 事件来源模块（'chat' / 'deep_research' / 'learning'）
   */
  const handleToolCallEvent = async (sessionId, taskId, payload, eventType, source) => {
    // toolCallId 为唯一主键（= LLM tool_call.id → toolCallId after toCamelCase）
    const toolCallId = payload.toolCallId

    if (!toolCallId) {
      logger.warn(`[Sync] handleToolCallEvent 缺少 toolCallId: ${eventType}`, payload)
      return
    }

    // 通过 event.type 映射到 ToolCallStatus
    const mappedStatus = TOOL_CALL_STATUS_MAP[eventType]
    if (!mappedStatus) {
      logger.warn(`[Sync] handleToolCallEvent 未知工具事件类型: ${eventType}`)
      return
    }

    // 参数非空判断：{} 和空数组 [] 视为空，非空 dict/array 视为有效。
    // 接受非空数组（如 write_todos 的 todos 数组），之前 !Array.isArray 排除所有数组导致数组参数丢失。
    const hasNonEmptyParams = !!payload.parameters
      && typeof payload.parameters === 'object'
      && (
        (!Array.isArray(payload.parameters) && Object.keys(payload.parameters).length > 0)
        || (Array.isArray(payload.parameters) && payload.parameters.length > 0)
      )

    /** @type {import('@/types').ToolCallData} */
    const toolData = {
      id: toolCallId,
      toolCallId: toolCallId,
      name: payload.toolName,
      toolName: payload.toolName,
      parameters: hasNonEmptyParams ? payload.parameters : undefined,
      state: payload.state,
      status: mappedStatus || payload.status,
      result: payload.result,
      error: payload.error,
      isInternal: payload.isInternal || false,
      // SAFE 级自动通过标记（Phase F1）：后端 publish_tool_call payload 携带，
      // ToolCallCard 读取 toolCall.isAutoApproved 显示"自动通过"徽章
      isAutoApproved: payload.autoApproved === true,
      // 子 agent 嵌套层级字段（Phase E3）：后端 publish_tool_call payload 携带，
      // ToolCallCard 读取 toolCall.* 展示完整调用链路（非审批路径也可见）
      parentToolCallId: payload.parentToolCallId || '',
      depth: typeof payload.depth === 'number' && payload.depth > 0 ? payload.depth : 0,
      agentName: payload.agentName || '',
      agentPath: Array.isArray(payload.agentPath) ? payload.agentPath : [],
      riskCeiling: payload.riskCeiling || '',
      // 工具调用实际风险等级（safe/controlled/high）
      // 根因修复：后端 publish_tool_call payload 携带 risk_level（toCamelCase 后为 riskLevel），
      // 让前端从工具事件直接获取风险等级，不再单一依赖 approval_pending 事件路径。
      // ToolCallCard 优先读 toolCall.riskLevel，回退 approvalData.riskLevel，
      // 避免 approval_pending 事件丢失时跨浏览器风险等级显示不一致
      riskLevel: payload.riskLevel || '',
      // 跨浏览器统一排序序号：后端 (module, module_id) 内跨 LLM 轮次全局递增序号
      // （register 分配，事件透传）。仅 number 类型注入，配合 messageOperations
      // _mergeExistingToolCall 的 seq 保护，防止无 seq 事件覆盖已分配序号
      ...(typeof payload.seq === 'number' ? { seq: payload.seq } : {}),
      // 起始/终态时间戳（Task 4.1）：后端工具事件 payload 透传 created_at / completed_at
      // （协议键，sessionTransformers toCamelCase 后为 createdAt/completedAt）。
      // 仅 PENDING 起始事件携带 createdAt；仅终态事件（COMPLETED/FAILED/TIMEOUT/REJECTED）
      // 携带 completedAt。条件展开保证未携带字段不覆盖已有值
      // （merge 语义：createdAt 只在 PENDING 写入，终态事件不覆盖起始时间）。
      ...(payload.createdAt ? { createdAt: payload.createdAt } : {}),
      ...(payload.completedAt ? { completedAt: payload.completedAt } : {}),
      // 子代理任务目标描述（Task 4.1）：仅子代理工具事件携带（如"网络搜索和信息整理专家…"），
      // 组级共享。不覆盖语义由 messageOperations 合并层保护（已有值不覆盖）。
      ...(payload.description ? { description: payload.description } : {}),
    }

    const hasMessageId = !!payload.messageId
    const isResultEvent = TOOL_CALL_RESULT_STATUSES.has(mappedStatus)
    const storeId = sessionId || taskId
    const storeName = sessionId ? 'sessionStore' : 'researchStore'

    logger.info(
      `[Sync] 收到 ${eventType} 事件: ${storeName}=${storeId}, ` +
      `tool=${payload.toolName}, toolCallId=${toolCallId}, ` +
      `mappedStatus=${mappedStatus}, isResult=${isResultEvent}, ` +
      `messageId=${payload.messageId || '(none)'}, source=${source || '(none)'}`
    )

    if (sessionId) {
      // chat / learning / 关联 deep_research：路由到 sessionStore
      // 防御性加载：WebSocket 事件可能在 session 加载之前到达
      // 会话不存在或消息为空时必须 await loadSessionDetail 完成，
      // 否则后续 addOrUpdateToolCall / updateOrAddToolResult 内部的 _getLastAssistantMessage
      // 会返回 null 导致事件被静默丢弃（非触发浏览器典型场景：会话在列表中但消息未加载）。
      // 与 handleApprovalEvent 的空消息检查保持一致。
      const existingSession = getSession(sessionStore, sessionId)
      if (!existingSession || !existingSession.messages || existingSession.messages.length === 0) {
        logger.info(`[Sync] handleToolCallEvent 会话不存在或消息为空，兜底拉取详情: ${sessionId}`)
        await ensureSessionLoaded(sessionStore, sessionId, 'handleToolCallEvent')
      }

      if (isResultEvent) {
        // 工具结果事件（completed / failed / timeout）：更新工具结果
        // updateOrAddToolResultInMap 已有参数保护：仅在非空时更新，空时保留已有参数
        if (hasMessageId) {
          sessionStore.updateOrAddToolResult(sessionId, { ...toolData, messageBackendId: payload.messageId?.toString() })
        } else {
          sessionStore.updateOrAddToolResult(sessionId, toolData)
        }
        logger.info(`[Sync] 工具调用结果: session=${sessionId}, message=${payload.messageId || '(兜底)'}, tool=${payload.toolName}, id=${toolCallId}, eventType=${eventType}`)
      } else {
        // 工具开始/运行中事件（pending / waiting / running）：新增或更新工具调用
        if (hasMessageId) {
          sessionStore.addOrUpdateToolCall(sessionId, { ...toolData, messageBackendId: payload.messageId?.toString() })
        } else {
          sessionStore.addOrUpdateToolCall(sessionId, toolData)
        }
        // 每次 toolCall 创建后，检查是否有待绑定的审批（时序保护）
        approvalStore.flushPendingBindQueue(sessionId)
        logger.info(`[Sync] 工具调用更新: session=${sessionId}, message=${payload.messageId || '(兜底)'}, tool=${payload.toolName}, id=${toolCallId}, eventType=${eventType}`)
      }
    } else if (taskId) {
      // 独立 deep_research：路由到 researchStore
      // researchStore.addOrUpdateToolCall 内部已调用 flushPendingApprovals，
      // 不需要额外调用 approvalStore.flushPendingBindQueue
      if (isResultEvent) {
        researchStore.updateOrAddToolResult(taskId, toolData)
      } else {
        researchStore.addOrUpdateToolCall(taskId, toolData)
      }
      logger.info(`[Sync] task ${eventType}: taskId=${taskId}, tool=${payload.toolName}, toolCallId=${toolCallId}, mappedStatus=${mappedStatus}`)
    }
  }

  return {
    handleToolCallEvent,
  }
}
