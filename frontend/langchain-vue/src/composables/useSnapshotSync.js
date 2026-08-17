import { ref } from 'vue'
import { getSessionSnapshot } from '@/api/realtime'
import { deepResearchAPI } from '@/api/research'
import { useSessionStore } from '@/stores/session'
import { useResearchStore } from '@/stores/research'
import { logger } from '@/utils/logger'
import { transformBackendMessageToFrontend, toCamelCase } from '@/utils/sessionTransformers'
import { _mergeToolCalls } from '@/utils/messageOperations'
import { ToolCallStatus } from '@/types'
import { getToolCallStatusPriority, TERMINAL_STATUSES } from '@/utils/toolCallStateMachine'

/**
 * 快照校对 debounce 时间（ms）
 * 关键事件可能在短时间内连续到达（如 tool_call_completed 紧跟 stream_finalized），
 * 使用 debounce 避免短时间内多次请求快照接口。
 */
const SNAPSHOT_DEBOUNCE_MS = 500

/**
 * 快照校对请求超时时间（ms）
 */
const SNAPSHOT_TIMEOUT_MS = 8000

/** 429 限流冷却期：收到 429 后此期间内不再发起快照请求，避免额度耗尽窗口内空转重试 */
const SNAPSHOT_COOLDOWN_MS = 15000

/**
 * 工具调用终态集合（快照校对 result 路径路由判断用）
 *
 * 快照 tool 状态为终态（COMPLETED/FAILED/TIMEOUT）时走 result 路径
 * （sessionStore.updateOrAddToolResult：工具结果唯一写入入口，允许终态覆盖本地
 * PENDING，绕过 add 路径的 PENDING+非终态审批锁死保护），
 * 否则走 add 路径（sessionStore.addOrUpdateToolCall：工具创建/中间态入口）。
 * 与 utils/toolCallStateMachine.js 的 TERMINAL_STATUSES（终态唯一权威）的关系：
 * 本集合是它的子集，显式排除 REJECTED——快照 result 路径仅覆盖
 * COMPLETED/FAILED/TIMEOUT 三种终态，REJECTED 保持走 add 路径（与原实现行为一致）。
 * 枚举值从权威集合派生，避免重复定义魔法值。
 */
const _TERMINAL_TOOL_CALL_STATUSES = new Set(
  [...TERMINAL_STATUSES].filter(status => status !== ToolCallStatus.REJECTED)
)

/**
 * 快照校对消息状态提升时"始终以后端为准"的非内容字段（后端是元数据权威）
 * 注意：与 messageOperations.js 的 _NON_CONTENT_FIELDS 保持一致，
 * researchTaskStatus 是研究卡片"进行中/已完成"判定的权威来源（P7 根因修复），
 * 快照校对必须注入，否则实时流式期间消息 researchTaskStatus 恒为 null，
 * 卡片回退 streamState 判定导致"研究已完成"误显。
 */
const _SNAPSHOT_NON_CONTENT_FIELDS = ['tokenCount', 'responseTime', 'model', 'backendId', 'researchTaskId', 'researchTaskStatus']

/**
 * 按 sessionId 缓存的快照校对实例
 *
 * @type {Map<string, { syncFromSnapshot: () => Promise<void>, isSyncing: import('vue').Ref<boolean> }>}
 */
const instanceCache = new Map()

/**
 * 按 taskId 缓存的快照校对实例
 *
 * @type {Map<string, { syncFromSnapshot: () => Promise<void>, isSyncing: import('vue').Ref<boolean> }>}
 */
const taskInstanceCache = new Map()

/**
 * 审批状态优先级（数值越大优先级越高）
 *
 * @param {string} state
 * @returns {number}
 */
function _approvalStatePriority(state) {
  // 空 approval 优先级最低，确保本地缺失 approval 时能被快照数据合并
  if (state == null) return -1
  switch (state) {
    case 'pending': return 0
    case 'processing': return 1
    case 'waiting': return 1
    case 'approved': return 2
    case 'rejected': return 2
    case 'timeout': return 2
    case 'completed': return 2
    default: return 0
  }
}

/**
 * 消息字段状态提升合并（Task 3.3）
 *
 * 快照校对只做**状态提升**：仅当本地缺失或后端更新（优先级更高）时才以快照覆盖，
 * 不再对非保护态消息调用 mergeMessageFromBackend 做整体覆盖（防止陈旧快照
 * 覆盖本地较新的流式/审批状态）。
 *
 * 规则（与 mergeMessageFromBackend 保护态分支 L1045-1074 对齐，统一适用于所有消息）：
 * - 非内容字段（tokenCount/responseTime/model/backendId）：始终以后端为准
 * - content：本地为空或后端更长时允许覆盖，否则保留本地
 * - toolCalls：后端数量 >= 本地时用 _mergeToolCalls 合并（保留本地更完整的 result/status），
 *   后端数量 < 本地时保留本地（快照滞后，本地更完整）
 * - reasoning：后端 reasoning.content 更长时覆盖
 * - sources/suggestions/context：本地缺失时补充，否则保留本地
 *
 * @param {Object} localMsg - 本地消息（会被原地修改）
 * @param {Object} backendMsg - 后端快照消息（已通过 transformBackendMessageToFrontend 转换）
 */
function _reconcileMessageField(localMsg, backendMsg) {
  if (!localMsg || !backendMsg) return

  // 非内容字段始终以后端为准（后端是元数据权威）
  for (const field of _SNAPSHOT_NON_CONTENT_FIELDS) {
    if (backendMsg[field] !== undefined) {
      localMsg[field] = backendMsg[field]
    }
  }

  // content：本地为空或后端更长时允许覆盖（状态提升，不整体覆盖本地新内容）
  const localContent = localMsg.content || ''
  const backendContent = backendMsg.content || ''
  const localContentEmpty = localContent.length === 0
  const backendLonger = backendContent.length > localContent.length
  let contentLifted = false
  if (localContentEmpty || backendLonger) {
    if (backendMsg.content !== undefined) {
      localMsg.content = backendMsg.content
      contentLifted = true
    }
  }

  // toolCalls：后端数量 >= 本地时合并（保留本地更完整状态）；否则保留本地
  const localToolCalls = localMsg.toolCalls || []
  const backendToolCalls = Array.isArray(backendMsg.toolCalls) ? backendMsg.toolCalls : []
  if (backendToolCalls.length >= localToolCalls.length) {
    localMsg.toolCalls = _mergeToolCalls(localToolCalls, backendToolCalls)
  }

  // reasoning：后端 reasoning.content 更长时允许覆盖
  if (backendMsg.reasoning !== undefined) {
    const localReasoningContent = localMsg.reasoning?.content || ''
    const backendReasoningContent = backendMsg.reasoning?.content || ''
    if (backendReasoningContent.length > localReasoningContent.length) {
      localMsg.reasoning = backendMsg.reasoning
    }
  }

  // sources/suggestions/context：本地缺失时补充（状态提升），否则保留本地
  for (const field of ['sources', 'suggestions', 'context']) {
    const localValue = localMsg[field]
    const localEmpty = !localValue || (Array.isArray(localValue) && localValue.length === 0)
    if (localEmpty && backendMsg[field] !== undefined) {
      localMsg[field] = backendMsg[field]
    }
  }

  // 同步到当前版本快照（与 mergeMessageFromBackend 保护态分支的版本同步语义对齐）：
  // 仅同步非内容字段与已提升字段，content 仅在实际提升时同步（保护态下不覆盖版本内容）
  const versionIdx = localMsg.currentVersion
  if (localMsg.versions && versionIdx !== undefined && localMsg.versions[versionIdx]) {
    const ver = localMsg.versions[versionIdx]
    for (const field of _SNAPSHOT_NON_CONTENT_FIELDS) {
      if (localMsg[field] !== undefined) ver[field] = localMsg[field]
    }
    ver.toolCalls = localMsg.toolCalls
    ver.subagentContents = localMsg.subagentContents
    ver.sources = localMsg.sources
    ver.reasoning = localMsg.reasoning
    ver.suggestions = localMsg.suggestions
    ver.context = localMsg.context
    if (contentLifted && localMsg.content !== undefined) {
      ver.content = localMsg.content
    }
  }
}

/**
 * 创建快照校对实例（Task 9.3：session / task 双工厂参数化合并）
 *
 * 通过 { kind: 'session' | 'task', id } 区分两种校对源：
 * - kind='session'：数据源为 getSessionSnapshot（消息 / toolCalls / 审批），
 *   校对目标为 sessionStore
 * - kind='task'：数据源为 deepResearchAPI.getStatus（tool_calls 含 result +
 *   subagent_contents），校对目标为 researchStore（深度研究模块跨浏览器同步）
 *
 * 公共框架（isSyncing / debounce / inflight 并发复用）两 kind 共用，
 * 数据获取与校对逻辑在 _performSessionSync / _performTaskSync 内分支。
 *
 * @param {{ kind: 'session' | 'task', id: string }} options
 * @returns {{ syncFromSnapshot: () => Promise<void>, isSyncing: import('vue').Ref<boolean> }}
 */
function createSnapshotSyncInstance({ kind, id }) {
  const isSyncing = ref(false)
  /** @type {number | null} */
  let debounceTimer = null
  /** @type {Promise<void> | null} */
  let inflightPromise = null
  /** 429 冷却截止时间戳（Date.now()），0 = 不在冷却期 */
  let cooldownUntil = 0

  // ==================== kind='session' 校对实现 ====================

  /**
   * 对比本地消息与快照消息（Task 3.3 状态提升合并）
   *
   * @param {Object} sessionStore
   * @param {Array} backendMessages - 后端快照消息列表
   */
  const _reconcileMessages = (sessionStore, backendMessages) => {
    if (!Array.isArray(backendMessages) || backendMessages.length === 0) return
    const session = sessionStore.sessions.find(s => s.id === id)
    if (!session) {
      logger.warn(`[SnapshotSync] 会话不存在，跳过消息校对: session=${id}`)
      return
    }

    let reconciledCount = 0
    for (const backendMsg of backendMessages) {
      if (!backendMsg) continue
      const transformed = transformBackendMessageToFrontend(backendMsg)
      if (!transformed) continue

      // 按 backendId / id 查找本地消息
      const localMsg = session.messages.find(m =>
        (m.backendId && m.backendId.toString() === String(transformed.backendId)) ||
        m.id === transformed.id
      )

      if (localMsg) {
        // 本地存在：状态提升合并（仅当本地缺失或后端更新时写，不整体覆盖）
        _reconcileMessageField(localMsg, transformed)
      } else {
        // 本地缺失：状态提升——从快照补入消息。
        // saveToBackend=false：快照校对不应将本地补入的消息回写后端
        // （addMessageToSession 内部已做 versions 初始化）
        sessionStore.addMessageToSession(id, transformed, false)
      }
      reconciledCount++
    }
    if (reconciledCount > 0) {
      logger.info(`[SnapshotSync] 消息校对完成: session=${id}, count=${reconciledCount}`)
    }
  }

  /**
   * 对比本地工具调用与快照，差异部分以快照为准
   *
   * @param {Object} sessionStore
   * @param {Array} backendToolCalls - 后端快照工具调用列表
   */
  const _reconcileToolCalls = (sessionStore, backendToolCalls) => {
    if (!Array.isArray(backendToolCalls) || backendToolCalls.length === 0) return
    let reconciledCount = 0
    for (const backendTc of backendToolCalls) {
      if (!backendTc) continue
      const toolCallId = backendTc.toolCallId || backendTc.id
      if (!toolCallId) continue

      const localTc = sessionStore.getToolCallById(id, toolCallId)
      if (localTc) {
        // 状态滞后判断：本地非终态、快照为终态时以快照为准
        const localPriority = getToolCallStatusPriority(localTc.status)
        const backendPriority = getToolCallStatusPriority(backendTc.status)
        if (backendPriority > localPriority) {
          // 通过 addOrUpdateToolCall / updateOrAddToolResult 触发响应式更新（携带 messageBackendId 以定位消息）
          // 快照为终态（COMPLETED/FAILED/TIMEOUT）时走 result 路径（updateOrAddToolResult，
          // 允许终态覆盖本地 PENDING，绕过 add 路径的 PENDING+非终态审批锁死保护），
          // 否则走 add 路径（addOrUpdateToolCall）
          const updateData = {
            ...backendTc,
            messageBackendId: localTc.messageBackendId,
          }
          if (_TERMINAL_TOOL_CALL_STATUSES.has(backendTc.status)) {
            sessionStore.updateOrAddToolResult(id, updateData)
          } else {
            sessionStore.addOrUpdateToolCall(id, updateData)
          }
          reconciledCount++
        } else if (
          // approval 字段完整性检查：本地 approval 为空但快照有 approval 数据时合并
          // 解决刷新后 API 返回的 tool_calls 不含 approval 字段的问题
          (!localTc.approval || Object.keys(localTc.approval).length === 0) &&
          backendTc.approval && Object.keys(backendTc.approval).length > 0
        ) {
          sessionStore.setApprovalToToolCall(id, toolCallId, backendTc.approval)
          reconciledCount++
        }
      } else {
        // 本地缺失：快照为终态时走 result 路径（updateOrAddToolResult），否则走 add 路径（addOrUpdateToolCall）
        // Task 3.3：补传 messageBackendId（后端快照 toolCall 携带的归属消息 id），
        // 确保 toolCallsMap → message.toolCalls 的派生同步能将 toolCall 挂载到正确消息
        // （toolCallsMap 中 toolCall.messageBackendId 驱动 _syncMessageToolCalls 归属）
        const addData = {
          ...backendTc,
          messageBackendId: backendTc.messageBackendId || backendTc.messageId,
        }
        if (_TERMINAL_TOOL_CALL_STATUSES.has(backendTc.status)) {
          sessionStore.updateOrAddToolResult(id, addData)
        } else {
          sessionStore.addOrUpdateToolCall(id, addData)
        }
        reconciledCount++
      }
    }
    if (reconciledCount > 0) {
      logger.info(`[SnapshotSync] 工具调用校对完成: session=${id}, count=${reconciledCount}`)
      // 根本修复：_reconcileToolCalls 将 snapshot 的平铺 tool_calls 列表
      // 全部写入 toolCallsMap（含旧消息的工具调用），但 addOrUpdateToolCall
      // 只调用 _syncMessageToolCalls（仅同步最后一条消息）。
      // 旧消息的工具调用留在 Map 中未分发到其 message.toolCalls。
      // 此处调用 syncAllMessageToolCallsFromMap 将 Map 中所有工具调用
      // 按 messageBackendId 正确分发到各条消息，消除跨消息工具调用污染。
      sessionStore.syncAllMessageToolCallsFromMap(id)
    }
  }

  /**
   * 对比本地审批状态与快照，差异部分以快照为准
   *
   * @param {Object} sessionStore
   * @param {Array} backendApprovals - 后端快照审批列表
   */
  const _reconcileApprovals = (sessionStore, backendApprovals) => {
    if (!Array.isArray(backendApprovals) || backendApprovals.length === 0) return
    let reconciledCount = 0
    for (const backendApproval of backendApprovals) {
      if (!backendApproval) continue
      const toolCallId = backendApproval.interruptId
      if (!toolCallId) continue

      const localTc = sessionStore.getToolCallById(id, toolCallId)
      if (!localTc) continue

      const localApprovalState = localTc.approval?.state
      const backendApprovalState = backendApproval.state
      if (!backendApprovalState) continue

      // 状态滞后判断：本地非终态、快照为终态时以快照为准
      // 注意：本地 approval 为空时（_approvalStatePriority(undefined) = -1），
      // 任何快照状态都会 > -1，从而合并缺失的 approval 数据
      const localPriority = _approvalStatePriority(localApprovalState)
      const backendPriority = _approvalStatePriority(backendApprovalState)
      if (backendPriority > localPriority) {
        sessionStore.setApprovalToToolCall(id, toolCallId, backendApproval)
        reconciledCount++
      }
    }
    if (reconciledCount > 0) {
      logger.info(`[SnapshotSync] 审批校对完成: session=${id}, count=${reconciledCount}`)
    }
  }

  /**
   * 执行一次 session 快照校对（带超时保护）
   *
   * @returns {Promise<void>}
   */
  const _performSessionSync = async () => {
    try {
      const sessionStore = useSessionStore()

      // 带超时的请求，避免快照接口卡死阻塞 UI
      const fetchPromise = getSessionSnapshot(id)
      const timeoutPromise = new Promise((_, reject) => {
        const timer = setTimeout(() => {
          reject(new Error(`Snapshot timeout: ${id}`))
        }, SNAPSHOT_TIMEOUT_MS)
        // 清理 timer 避免内存泄漏
        fetchPromise.finally(() => clearTimeout(timer))
      })

      let resp
      try {
        resp = await Promise.race([fetchPromise, timeoutPromise])
      } catch (error) {
        // 429 限流：进入冷却期，暂停后续快照触发，避免额度耗尽窗口内空转
        if (error?.response?.status === 429) {
          cooldownUntil = Date.now() + SNAPSHOT_COOLDOWN_MS
          // 清理残留 debounce 定时器，避免冷却期结束后立即触发一次
          if (debounceTimer !== null) {
            clearTimeout(debounceTimer)
            debounceTimer = null
          }
          logger.warn(`[SnapshotSync] 快照请求被限流(429)，冷却 ${SNAPSHOT_COOLDOWN_MS / 1000}s: session=${id}`)
          return
        }
        logger.warn(`[SnapshotSync] 快照请求失败，保留本地状态: session=${id}, error=${error.message}`)
        return
      }

      const data = resp?.data?.data
      if (!data) {
        logger.warn(`[SnapshotSync] 快照响应数据为空: session=${id}`)
        return
      }

      const converted = toCamelCase(data)

      // 校对消息（converted.messages 可选）
      if (converted.messages !== undefined) {
        _reconcileMessages(sessionStore, converted.messages)
      }

      // 校对工具调用（converted.toolCalls 可选）
      if (converted.toolCalls !== undefined) {
        _reconcileToolCalls(sessionStore, converted.toolCalls)
      }

      // 校对审批（converted.pendingApprovals 或 converted.approvals 可选）
      const approvals = converted.pendingApprovals || converted.approvals
      if (approvals !== undefined) {
        _reconcileApprovals(sessionStore, approvals)
      }

      logger.info(`[SnapshotSync] 快照校对成功: session=${id}`)
    } catch (error) {
      // 任何异常都不阻塞 UI，保留本地状态
      logger.warn(`[SnapshotSync] 快照校对异常，保留本地状态: session=${id}, error=${error?.message || error}`)
    }
  }

  // ==================== kind='task' 校对实现 ====================

  /**
   * 执行一次 task 快照校对（深度研究模块专用）
   *
   * 数据源：ResearchTask（后端 status 接口返回 tool_calls（含 result）+
   * subagent_contents），替代原 Approval 历史重建（Approval 不持久化 result，
   * 刷新后工具结果丢失）。
   *
   * 合并策略：researchStore.setTaskToolCallsFromSnapshot /
   * setTaskSubagentContentsFromSnapshot 内部使用 _mergeToolCalls 增量合并，
   * 保留本地审批中间状态与更完整的 result/status。
   *
   * @returns {Promise<void>}
   */
  const _performTaskSync = async () => {
    try {
      const response = await deepResearchAPI.getStatus(id)
      const data = response?.data?.data || response?.data || {}
      const backendToolCalls = Array.isArray(data.toolCalls) ? data.toolCalls : []
      const subagentContents = data.subagentContents || {}

      const researchStore = useResearchStore()
      researchStore.setTaskToolCallsFromSnapshot(id, backendToolCalls)
      researchStore.setTaskSubagentContentsFromSnapshot(id, subagentContents)

      logger.info(
        `[SnapshotSync] task=${id} 快照校对完成: ${backendToolCalls.length} 个工具调用`
      )
    } catch (err) {
      if (err?.response?.status === 429) {
        cooldownUntil = Date.now() + SNAPSHOT_COOLDOWN_MS
        if (debounceTimer !== null) {
          clearTimeout(debounceTimer)
          debounceTimer = null
        }
        logger.warn(`[SnapshotSync] task=${id} 快照校对被限流(429)，冷却 ${SNAPSHOT_COOLDOWN_MS / 1000}s`)
        return
      }
      logger.warn(
        `[SnapshotSync] task=${id} 快照校对失败（非致命）: ${err?.message || err}`
      )
    }
  }

  // ==================== 公共框架 ====================

  /**
   * 检查是否处于 429 冷却期
   * @returns {boolean}
   */
  const _isInCooldown = () => cooldownUntil > 0 && Date.now() < cooldownUntil

  /**
   * 执行一次快照校对（按 kind 路由，带 isSyncing 保护）
   *
   * @returns {Promise<void>}
   */
  const _performSync = async () => {
    if (isSyncing.value) {
      logger.debug(`[SnapshotSync] 校对进行中，跳过本次: ${kind}=${id}`)
      return
    }
    isSyncing.value = true
    try {
      if (kind === 'task') {
        await _performTaskSync()
      } else {
        await _performSessionSync()
      }
    } finally {
      isSyncing.value = false
    }
  }

  /**
   * 触发快照校对（500ms debounce）
   *
   * 关键事件（stream_finalized / tool_call_completed / approval_approved）触发后调用。
   * 短时间内多次调用只执行最后一次，避免请求风暴。
   *
   * @returns {Promise<void>}
   */
  const syncFromSnapshot = () => {
    // 429 冷却期内跳过（不发请求），避免额度耗尽窗口内空转
    if (_isInCooldown()) {
      logger.debug(`[SnapshotSync] 429 冷却期内跳过校对: ${kind}=${id}`)
      return Promise.resolve()
    }
    // 清除前一个 debounce 定时器
    if (debounceTimer !== null) {
      clearTimeout(debounceTimer)
    }
    // 返回进行中的 Promise（如有），避免调用方等待 debounce 完成后才发现已在进行
    return new Promise((resolve) => {
      debounceTimer = setTimeout(() => {
        debounceTimer = null
        // 复用进行中的 Promise 避免并发请求
        if (inflightPromise) {
          inflightPromise.then(resolve).catch(() => resolve())
          return
        }
        inflightPromise = _performSync().finally(() => {
          inflightPromise = null
        })
        inflightPromise.then(resolve).catch(() => resolve())
      }, SNAPSHOT_DEBOUNCE_MS)
    })
  }

  return {
    isSyncing,
    syncFromSnapshot,
  }
}

/**
 * 快照校对组合式函数（session 通道）
 *
 * 按 sessionId 缓存实例，同一会话多次调用返回同一实例。
 * 用于在关键事件（stream_finalized / tool_call_completed / approval_approved）后，
 * 拉取后端快照与本地状态对比，修复可能丢失或乱序的中间事件。
 *
 * 失败时仅记录 warning，不阻塞 UI，保留本地状态。
 *
 * @param {string} sessionId - 会话 ID
 * @returns {{ syncFromSnapshot: () => Promise<void>, isSyncing: import('vue').Ref<boolean> }}
 */
export function useSnapshotSync(sessionId) {
  if (!sessionId) {
    throw new Error('[useSnapshotSync] sessionId is required')
  }
  let instance = instanceCache.get(sessionId)
  if (!instance) {
    instance = createSnapshotSyncInstance({ kind: 'session', id: sessionId })
    instanceCache.set(sessionId, instance)
  }
  return instance
}

/**
 * 清理指定会话的快照校对实例缓存（会话删除时调用）
 *
 * @param {string} sessionId
 */
export function clearSnapshotSyncInstance(sessionId) {
  instanceCache.delete(sessionId)
}

/**
 * 深度研究任务快照校对 composable（task 通道，v5 M19-c 新增）
 *
 * 按 taskId 缓存实例，debounce 500ms，避免短时间内多次请求快照接口。
 * 用于深度研究模块跨浏览器同步时拉取最新状态。
 *
 * @param {string} taskId - 深度研究任务 ID
 * @returns {{ syncFromSnapshot: () => Promise<void>, isSyncing: import('vue').Ref<boolean> }}
 */
export function useSnapshotSyncByTask(taskId) {
  if (!taskId) {
    throw new Error('[useSnapshotSyncByTask] taskId is required')
  }
  let instance = taskInstanceCache.get(taskId)
  if (!instance) {
    instance = createSnapshotSyncInstance({ kind: 'task', id: taskId })
    taskInstanceCache.set(taskId, instance)
  }
  return instance
}

/**
 * 清理指定任务的快照校对实例缓存（任务删除时调用）
 *
 * @param {string} taskId
 */
export function clearTaskSnapshotSyncInstance(taskId) {
  taskInstanceCache.delete(taskId)
}
