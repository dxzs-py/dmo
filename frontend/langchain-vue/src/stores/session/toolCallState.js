/**
 * session store 切片：工具调用与审批状态域（toolCallMap 为唯一真相源）
 * （拆分自原 stores/session.js，C5/cq-04 Task 3，行为保持）
 *
 * 持有 state：toolCallsMap / pendingApprovals
 *
 * 数据结构：
 *   toolCallsMap: Map<sessionId, Map<toolCallId, toolCall>>
 *   pendingApprovals: Map<sessionId, Map<toolCallId, {approvalData, toolCallId}>>
 *
 * 设计与 researchStore 对齐：toolCallMap 作为唯一真相源，message.toolCalls
 * 数组通过 _syncMessageToolCalls 派生（单向数据流：Map → message.toolCalls）。
 *
 * 时序保护：审批事件先于 tool 事件到达时，setApprovalToToolCall 创建 isSynthetic
 * 占位条目并加入 pendingApprovals 队列；后续 tool 事件到达时通过
 * flushPendingApprovals 绑定审批数据到真实 toolCall。
 *
 * 跨切片运行时依赖（runtimeDeps，由 index.js 在全部切片创建后注册）：
 * - _debouncedToolSync（messageFlow，工具事件后 debounced 同步消息到后端）
 */
import { ref, triggerRef } from 'vue'
import { logger } from '../../utils/logger'
import {
  // Map 版本工具函数（与 researchStore 共用，toolCallMap 作为唯一真相源）
  addOrUpdateToolCallInMap,
  updateOrAddToolResultInMap,
  setApprovalToToolCallInMap,
  updateApprovalStateInMap,
  finalizeToolCallsInMap,
  findToolCallInMap,
  flushPendingApprovalsInMap,
  isTerminalStatus,
  sortToolCallsForDisplay,
} from '../../utils/messageOperations'

export const createToolCallStateSlice = (sessionList, runtimeDeps) => {
  const { sessions } = sessionList

  /** @type {import('vue').Ref<Map<string, Map<string, Object>>>} */
  const toolCallsMap = ref(new Map())
  /** @type {import('vue').Ref<Map<string, Map<string, {approvalData: Object, toolCallId: string}>>>} */
  const pendingApprovals = ref(new Map())

  /**
   * 获取或创建指定 session 的 toolCallMap
   * @param {string} sessionId - 会话 ID
   * @returns {Map<string, Object>} toolCallMap
   */
  const _ensureToolCallsMap = (sessionId) => {
    if (!sessionId) return null
    if (!toolCallsMap.value.has(sessionId)) {
      toolCallsMap.value.set(sessionId, new Map())
      triggerRef(toolCallsMap)
    }
    return toolCallsMap.value.get(sessionId)
  }

  /**
   * 获取或创建指定 session 的 pendingApprovalsMap
   * @param {string} sessionId - 会话 ID
   * @returns {Map<string, {approvalData: Object, toolCallId: string}>} pendingApprovalsMap
   */
  const _ensurePendingApprovalsMap = (sessionId) => {
    if (!sessionId) return null
    if (!pendingApprovals.value.has(sessionId)) {
      pendingApprovals.value.set(sessionId, new Map())
      triggerRef(pendingApprovals)
    }
    return pendingApprovals.value.get(sessionId)
  }

  /**
   * 清理指定会话的工具调用与待绑定审批数据（Map 为唯一真相源）。
   * deleteSession / removeSession / clearCurrentSessionMessages 共用。
   * @param {string} sessionId - 会话 ID
   */
  const _removeSessionToolCallState = (sessionId) => {
    if (toolCallsMap.value.has(sessionId)) {
      toolCallsMap.value.delete(sessionId)
      triggerRef(toolCallsMap)
    }
    if (pendingApprovals.value.has(sessionId)) {
      pendingApprovals.value.delete(sessionId)
      triggerRef(pendingApprovals)
    }
  }

  /** 清空全部工具调用与待绑定审批数据（clearAllLocalData 共用） */
  const _clearAllToolCallState = () => {
    toolCallsMap.value = new Map()
    pendingApprovals.value = new Map()
  }

  /**
   * 按被删除消息的后端 ID 清理 toolCallsMap 中的对应条目
   * （removeMessagesByIds 共用，messageBackendId 精确归属）
   * @param {string} sessionId - 会话 ID
   * @param {Array<string>} ids - 被删除消息 ID 列表（前端 id 或 backendId）
   */
  const _pruneToolCallsByRemovedMessageIds = (sessionId, ids) => {
    const idSet = new Set(ids.map(String))
    const tcMap = toolCallsMap.value.get(sessionId)
    if (tcMap) {
      for (const [tcId, tc] of tcMap) {
        if (tc.messageBackendId && idSet.has(tc.messageBackendId)) tcMap.delete(tcId)
      }
    }
  }

  /**
   * 将 API 加载的消息中的 toolCalls 初始化到 toolCallsMap
   *
   * loadSessionDetail 从后端加载完整消息后，message.toolCalls 已有完整数据，
   * 但 toolCallsMap 为空。若后续 WebSocket 事件先于全量 tool_call_* 事件
   * 触发 _syncMessageToolCalls，会把 message.toolCalls 替换为 toolCallsMap
   * 的不完整子集（P27）。
   *
   * 此函数建立双向一致性：将 message.toolCalls 回填到 toolCallsMap。
   * 仅填充尚不存在的条目，不覆盖 WebSocket 已写入的动态字段。
   *
   * @param {string} sessionId - 会话 ID
   * @param {Array} messages - 消息列表（来自 API）
   */
  const _syncToolCallsMapFromMessages = (sessionId, messages) => {
    if (!sessionId || !Array.isArray(messages)) return
    let targetMap = toolCallsMap.value.get(sessionId)
    if (!targetMap) {
      targetMap = new Map()
      toolCallsMap.value.set(sessionId, targetMap)
    }
    // 以 API 消息 toolCalls 顺序（= 后端 DB 数组顺序，时间权威）重建 Map 插入顺序。
    // 根因修复（跨浏览器工具乱序）：WebSocket 事件与 API 历史加载并发时，事件先到达
    // 会将新工具（如审批恢复后的 fs_write_file）插入 Map 开头；此前 persist 丢弃 seq
    // 导致刷新后排序依据缺失而乱序。现已由后端补齐 seq（sse_generator /
    // stream_persistence / approval_service 统一经 enrich_entry_seq 从 ToolCallContext
    // 写入），API 快照自带 seq，_syncMessageToolCalls 按 seq 稳定排序。此处仍以
    // API 数组顺序重建 Map 插入顺序作为快照基线；已存在条目保留实时动态字段
    // （status/approval），不覆盖。
    const orderedEntries = []
    const seenKeys = new Set()
    for (const msg of messages) {
      if (msg.role !== 'assistant' || !Array.isArray(msg.toolCalls)) continue
      for (const tc of msg.toolCalls) {
        const key = tc.toolCallId || tc.id
        if (!key || seenKeys.has(key)) continue
        seenKeys.add(key)
        const existing = targetMap.get(key)
        if (existing) {
          // position 是不可变字段（Agent 图层嵌套规范 D3），API 快照为权威来源。
          // 现有条目（WebSocket 事件先写入）缺失 position 时从 API 快照回填
          // （position=0 是合法值，用 typeof 判断而非真值）；其余动态字段
          // （status/approval）仍保留实时值不覆盖。
          if (typeof existing.position !== 'number' && typeof tc.position === 'number') {
            existing.position = tc.position
          }
          // 图层字段回填（spec D1）：子代理卡归集依据 subagentThreadId，事件先写
          // 入的现有条目缺失时从 API 快照回填（后端实时落库后快照为权威来源）。
          if (!existing.subagentThreadId && tc.subagentThreadId) {
            existing.subagentThreadId = tc.subagentThreadId
          }
          if (!existing.agentName && tc.agentName) {
            existing.agentName = tc.agentName
          }
          orderedEntries.push([key, existing])
        } else {
          orderedEntries.push([key, { ...tc, messageBackendId: msg.backendId?.toString() }])
        }
      }
    }
    // 保留 API 快照未覆盖的实时条目（WebSocket 刚插入的最新工具），追加末尾
    for (const [key, value] of targetMap) {
      if (!seenKeys.has(key)) orderedEntries.push([key, value])
    }
    // 重建 Map 使插入顺序与 API 权威顺序一致
    toolCallsMap.value.set(sessionId, new Map(orderedEntries))
    triggerRef(toolCallsMap)
  }

  /**
   * 从 toolCallMap 收集应同步到目标消息的 toolCall 数组（唯一归属规则，两处同步共用）
   *
   * 归属规则：仅 messageBackendId 与目标消息 backendId 精确匹配的条目。
   * 无 messageBackendId 的条目不归入任何消息（丢弃），不得兜底。
   *
   * @param {Map} toolCallMap - 该 session 的 toolCall Map
   * @param {Object} targetMsg - 目标消息
   * @returns {Array} 归属到该消息的 toolCall 数组
   */
  const _collectToolCallsForMessage = (toolCallMap, targetMsg) => {
    const backendId = targetMsg.backendId?.toString()
    const arr = []
    for (const tc of toolCallMap.values()) {
      if (tc.messageBackendId && tc.messageBackendId === backendId) {
        arr.push(tc)
      }
    }
    return arr
  }

  /**
   * 将 toolCallMap 同步到 messages 数组（最后一条 assistant 消息的 toolCalls）
   *
   * 单向数据流：Map → message.toolCalls（派生）。
   * 修改 toolCallMap 内对象属性后必须调用，确保 UI 刷新。
   *
   * 同步范围：
   * - 最后一条 assistant 消息的 toolCalls
   * - 当前 version（versions[currentVersion]）的 toolCalls
   *
   * @param {string} sessionId - 会话 ID
   * @param {Object} [message] - 可选，指定目标消息；不传则回退到最后一条 assistant
   */
  const _syncMessageToolCalls = (sessionId, message) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session || session.messages.length === 0) return
    const targetMsg = message || session.messages[session.messages.length - 1]
    if (!targetMsg || targetMsg.role !== 'assistant') return
    const toolCallMap = toolCallsMap.value.get(sessionId)
    if (!toolCallMap) {
      // 兜底保护：toolCallMap 未初始化时，不清空已有 toolCalls 数据
      // （刷新后 API 加载的 toolCalls 已在 message.toolCalls 中，等待
      //  loadSessionDetail → _syncToolCallsMapFromMessages 回填 Map）
      return
    }
    // 归属规则统一走 _collectToolCallsForMessage
    const arr = _collectToolCallsForMessage(toolCallMap, targetMsg)
    // 跨浏览器统一排序：seq（register 分配的 (module, module_id) 内跨 LLM 轮次
    // 全局递增序号，所有链路统一透传）是唯一排序依据。sortToolCallsForDisplay
    // 是唯一权威排序实现（messageOperations.js），sessionStore 与 researchStore 共用
    sortToolCallsForDisplay(arr)
    targetMsg.toolCalls = arr
    const ver = targetMsg.versions?.[targetMsg.currentVersion]
    if (ver) ver.toolCalls = arr
    // 同步完成后清理其他 assistant 消息上残留的 toolCalls（避免旧消息保留审批 UI）。
    // 逐条过滤：仅保留 Map 中 messageBackendId 精确归属本消息且仍存在的工具调用。
    // 原实现用 some（至少一个有效则保留全部），污染消息只要有一个工具归属正确
    // 整批残留都会被保留，放大跨消息污染。改为 filter 逐条校验。
    for (const msg of session.messages) {
      if (msg !== targetMsg && msg.role === 'assistant' && msg.toolCalls?.length > 0) {
        const validToolCalls = msg.toolCalls.filter(tc => {
          const mapTc = toolCallMap.get(tc.id)
          return mapTc && mapTc.messageBackendId === msg.backendId?.toString()
        })
        if (validToolCalls.length !== msg.toolCalls.length) {
          msg.toolCalls = validToolCalls
        }
      }
    }
  }

  // ==================== 工具调用 Map 反向同步 ====================

  /**
   * 将 toolCallsMap 中的状态同步回所有消息的 toolCalls 数组
   *
   * 方向：Map → messages（反向同步）。
   * 场景：loadSessionDetail 后 API 数据已写入 Map，需将 Map 中的完整状态
   * （含 WebSocket 事件已更新的动态字段）回写到 messages 数组供 UI 渲染。
   */
  const _syncAllMessageToolCallsFromMap = (sessionId) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session) return
    const toolCallMap = toolCallsMap.value.get(sessionId)
    if (!toolCallMap || toolCallMap.size === 0) return
    for (const msg of session.messages) {
      if (msg.role !== 'assistant') continue
      // 归属规则统一走 _collectToolCallsForMessage：仅 messageBackendId 精确匹配
      const arr = _collectToolCallsForMessage(toolCallMap, msg)
      if (arr.length > 0) {
        // 跨浏览器统一排序：seq 唯一排序依据（与 _syncMessageToolCalls 同一权威实现）
        sortToolCallsForDisplay(arr)
        msg.toolCalls = arr
        const ver = msg.versions?.[msg.currentVersion]
        if (ver) ver.toolCalls = arr
      }
    }
  }

  /**
   * 版本切换后重建该 session 的 toolCallsMap（Agent 图层嵌套规范 D4/D6，task 3.5）
   *
   * toolCallsMap 是 session 级扁平索引（Map<toolCallId, toolCall>），覆盖主/子全部图层。
   * 切换版本时以目标版本快照 toolCalls 为基底重建当前消息的条目：
   * - 保留其他消息（messageBackendId 不匹配）的工具，不受版本切换影响
   * - 当前消息条目合并旧 Map 的动态字段（status/approval/result 等实时状态）
   * - 剔除不属于目标版本的旧条目（防止版本污染：v2 流式期间 Map 残留 v1 工具）
   *
   * @param {string} sessionId - 会话 ID
   * @param {Object} message - 目标消息
   * @param {Array} versionToolCalls - 目标版本快照 toolCalls（扁平全量）
   */
  const _rebuildToolCallsMapForVersion = (sessionId, message, versionToolCalls) => {
    const oldMap = toolCallsMap.value.get(sessionId)
    if (!oldMap) return
    const msgBackendId = message.backendId?.toString()
    const newMap = new Map()
    // 保留其他消息的工具（版本切换不影响）
    for (const [key, tc] of oldMap) {
      if (msgBackendId && tc.messageBackendId && tc.messageBackendId !== msgBackendId) {
        newMap.set(key, tc)
      }
    }
    // 以版本快照为基底重建当前消息的工具，合并旧 Map 实时动态字段
    for (const tc of Array.isArray(versionToolCalls) ? versionToolCalls : []) {
      const key = tc.toolCallId || tc.id
      if (!key) continue
      const existing = oldMap.get(key)
      const merged = existing
        ? { ...tc, ...existing, messageBackendId: msgBackendId }
        : { ...tc, messageBackendId: msgBackendId }
      // position 是不可变字段（Agent 图层嵌套规范 D3）：版本快照 tc 为权威基底，
      // 旧 Map 条目（existing）缺失 position 时不得覆盖快照的 number position
      // （position=0 是合法值，用 typeof 判断而非真值）。
      if (typeof merged.position !== 'number' && typeof tc.position === 'number') {
        merged.position = tc.position
      }
      // 图层字段回填（spec D1）：旧 Map 实时条目缺失 subagentThreadId/agentName 时
      // 从版本快照回填（子代理卡归集依据），避免快照有而实时条目缺时丢失。
      if (!merged.subagentThreadId && tc.subagentThreadId) {
        merged.subagentThreadId = tc.subagentThreadId
      }
      if (!merged.agentName && tc.agentName) {
        merged.agentName = tc.agentName
      }
      newMap.set(key, merged)
    }
    toolCallsMap.value.set(sessionId, newMap)
    triggerRef(toolCallsMap)
  }

  /**
   * 将审批数据设置到指定 toolCall（工具调用级审批）
   *
   * 统一通过 toolCallMap 作为唯一真相源操作（与 researchStore 对齐）。
   * isSynthetic 占位机制：审批先于真实 tool 事件到达时，创建占位条目使 ToolCallCard
   * 立即渲染审批面板，并加入 pendingApprovals 队列（兜底机制）。
   *
   * @param {string} sessionId - 会话 ID
   * @param {string} toolCallId - 工具调用 ID（interrupt_id 或 tool_call_id）
   * @param {Object} approvalData - 审批事件数据
   */
  const setApprovalToToolCall = (sessionId, toolCallId, approvalData) => {
    if (!sessionId || !toolCallId || !approvalData) return
    const toolCallMap = _ensureToolCallsMap(sessionId)
    if (!toolCallMap) return

    const isSynthetic = setApprovalToToolCallInMap(toolCallMap, toolCallId, approvalData)
    triggerRef(toolCallsMap)
    _syncMessageToolCalls(sessionId)

    // 如果创建了占位条目，同时存入 pendingApprovals 队列（兜底机制，确保后续 tool 事件能正确合并）
    if (isSynthetic) {
      const pendingMap = _ensurePendingApprovalsMap(sessionId)
      if (pendingMap) {
        const pendingIds = [toolCallId, approvalData.toolCallId].filter(Boolean)
        for (const pid of pendingIds) {
          pendingMap.set(pid, { approvalData, toolCallId })
        }
        triggerRef(pendingApprovals)
        logger.info(
          `[Session] 占位 toolCall 已加入 pendingApprovals 队列: sessionId=${sessionId}, toolCallId=${toolCallId}, pendingIds=${pendingIds}`
        )
      }
    }
  }

  /**
   * 刷新待绑定的审批数据，将其附加到对应 toolCall
   *
   * 在 addOrUpdateToolCall / updateOrAddToolResult 创建/更新 toolCall 后调用，
   * 处理审批事件先于 tool 事件到达的时序场景：
   * 1. 从 pendingApprovals Map 中查找匹配的审批
   * 2. 从队列移除后调用 setApprovalToToolCall 绑定（此时 toolCallMap 中已有目标条目）
   *
   * @param {string} sessionId - 会话 ID
   * @param {string} toolCallId - 工具调用 ID
   */
  const flushPendingApprovals = (sessionId, toolCallId) => {
    if (!sessionId || !toolCallId) return
    const pendingMap = pendingApprovals.value.get(sessionId)
    if (!pendingMap) return
    const toolCallMap = toolCallsMap.value.get(sessionId)
    if (!toolCallMap) return
    const didBind = flushPendingApprovalsInMap(pendingMap, toolCallMap, toolCallId)
    if (didBind) {
      triggerRef(pendingApprovals)
      triggerRef(toolCallsMap)
      _syncMessageToolCalls(sessionId)
      logger.info(`[Session] pending 审批已绑定: sessionId=${sessionId}, toolCallId=${toolCallId}`)
    }
  }

  /**
   * 更新 toolCall 的审批状态（approval.state）
   *
   * 用于审批成功/失败/超时后同步消息中的审批数据。
   * 仅更新 approval.state，不再同步修改 toolCall.status（审批事件不应改变工具状态）。
   *
   * @param {string} sessionId - 会话 ID
   * @param {string} toolCallId - 工具调用 ID
   * @param {string} state - 新的 approval.state（如 'processing' / 'approved' / 'rejected' / 'timeout'）
   */
  const updateToolCallApprovalState = (sessionId, toolCallId, state) => {
    if (!sessionId || !toolCallId) return
    const toolCallMap = toolCallsMap.value.get(sessionId)
    if (!toolCallMap) return
    // 1. 更新 approval.state（不修改 status，由 updateApprovalStateInMap 保证）
    const stateUpdated = updateApprovalStateInMap(toolCallMap, toolCallId, state)
    if (!stateUpdated) return
    triggerRef(toolCallsMap)
    _syncMessageToolCalls(sessionId)
  }

  /**
   * 创建/更新 toolCall（对应 SSE tool 事件）
   *
   * 委托通用函数 addOrUpdateToolCallInMap 处理 toolCallMap 操作（含 isSynthetic 占位机制、
   * parameters 参数回查），保留 store 特定的 _syncMessageToolCalls 和 flushPendingApprovals 调用。
   *
   * @param {string} sessionId - 会话 ID
   * @param {Object} data - 工具事件数据
   */
  const addOrUpdateToolCall = (sessionId, data) => {
    if (!sessionId || !data) return
    const toolCallMap = _ensureToolCallsMap(sessionId)
    if (!toolCallMap) return

    const toolCallId = addOrUpdateToolCallInMap(toolCallMap, data)
    if (!toolCallId) return

    triggerRef(toolCallsMap)
    _syncMessageToolCalls(sessionId)
    // toolCall 创建后，检查是否有待绑定的审批（时序保护：审批事件先到达）
    flushPendingApprovals(sessionId, toolCallId)
    runtimeDeps._debouncedToolSync(sessionId)
  }

  /**
   * 更新/添加工具结果（对应 SSE tool_result 事件）
   *
   * 委托通用函数 updateOrAddToolResultInMap 处理 toolCallMap 操作（含结果更新、parameters 补充），
   * 保留 store 特定的 _syncMessageToolCalls 和 flushPendingApprovals 调用。
   *
   * @param {string} sessionId - 会话 ID
   * @param {Object} data - 工具结果事件数据
   */
  const updateOrAddToolResult = (sessionId, data) => {
    if (!sessionId || !data) return
    const toolCallMap = _ensureToolCallsMap(sessionId)
    if (!toolCallMap) return

    const toolCallId = updateOrAddToolResultInMap(toolCallMap, data)
    if (!toolCallId) return

    // 仅当工具未进入终态时才刷新待绑审批。updateOrAddToolResultInMap 在终态时
    // 已将 approval 置 null，flushPendingApprovals 会找到旧批次审批并重设，
    // 导致审批面板残留（P29 次生问题：已完成工具仍显示审批确认）
    const isTerminal = isTerminalStatus(data.status)
    if (!isTerminal) {
      flushPendingApprovals(sessionId, toolCallId)
    }
    triggerRef(toolCallsMap)
    _syncMessageToolCalls(sessionId)
    runtimeDeps._debouncedToolSync(sessionId)
  }

  /**
   * 获取指定 session 的 toolCall（通过 toolCallId）
   *
   * @param {string} sessionId - 会话 ID
   * @param {string} toolCallId - 工具调用 ID
   * @returns {Object|null} toolCall 对象（不存在时返回 null）
   */
  const getToolCallById = (sessionId, toolCallId) => {
    if (!sessionId || !toolCallId) return null
    const toolCallMap = toolCallsMap.value.get(sessionId)
    if (!toolCallMap) return null
    const { toolCall } = findToolCallInMap(toolCallMap, toolCallId)
    return toolCall || null
  }

  /**
   * 最终化指定消息的所有非终态 toolCalls（流式完成后兜底）
   *
   * 被 messageIntegrity.finalizeToolCallsForCompletedMessage 调用（P3-22/P3-23 根因修复）：
   * WebSocket tool_call_completed / approval_approved 事件丢失或乱序时，
   * toolCallsMap（单一真相源）中 toolCall.status 可能卡在 pending/running/waiting，
   * approval.state 可能卡在 pending/processing/waiting，导致 UI 永久显示"执行中"
   * 和审批按钮不消失。
   *
   * 此方法在流式结束时兜底：将非终态工具状态修正为 COMPLETED，
   * 将非终态审批状态修正为 TIMEOUT，并同步到 message.toolCalls（派生数据）。
   *
   * @param {string} sessionId - 会话 ID
   * @param {string} [messageBackendId] - 可选，仅最终化属于该消息的 toolCalls；
   *   不传则最终化该 session 下所有 toolCalls
   * @returns {number} 最终化的 toolCall 数量
   */
  const finalizeToolCallsInMapForSession = (sessionId, messageBackendId) => {
    if (!sessionId) return 0
    const toolCallMap = toolCallsMap.value.get(sessionId)
    if (!toolCallMap || toolCallMap.size === 0) return 0

    const finalizedCount = finalizeToolCallsInMap(toolCallMap, messageBackendId)
    if (finalizedCount > 0) {
      triggerRef(toolCallsMap)
      _syncMessageToolCalls(sessionId)
      logger.info(
        `[Session] finalizeToolCallsInMap: 最终化 ${finalizedCount} 个 toolCall: ` +
        `session=${sessionId}, message=${messageBackendId || '(全部)'}`
      )
    }
    return finalizedCount
  }

  return {
    // state（toolCallsMap 公开为唯一真相源；pendingApprovals 不导出：
    // SubTask 8.4 收敛为 approvalStore.pendingApprovals 单一权威，本队列仅为内部中间态）
    toolCallsMap,
    // actions
    setApprovalToToolCall,
    flushPendingApprovals,
    updateToolCallApprovalState,
    addOrUpdateToolCall,
    updateOrAddToolResult,
    getToolCallById,
    syncMessageToolCalls: _syncMessageToolCalls,
    syncAllMessageToolCallsFromMap: _syncAllMessageToolCallsFromMap,
    finalizeToolCallsInMap: finalizeToolCallsInMapForSession,
    // 切片间共享（sessionList / messageFlow / versionFlow 使用）
    _syncToolCallsMapFromMessages,
    _rebuildToolCallsMapForVersion,
    _removeSessionToolCallState,
    _clearAllToolCallState,
    _pruneToolCallsByRemovedMessageIds,
  }
}
