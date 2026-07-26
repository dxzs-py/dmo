import { ref } from 'vue'
import { getSessionSnapshot } from '@/api/realtime'
import { useSessionStore } from '@/stores/session'
import { logger } from '@/utils/logger'
import { transformBackendMessageToFrontend } from '@/utils/session-transformers'
import { mergeMessageFromBackend } from '@/utils/message-operations'
import { ToolCallStatus } from '@/types'

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

/**
 * 工具调用终态集合（用于状态滞后判断）
 * 注：原 TERMINAL_TOOL_CALL_STATUSES / TERMINAL_APPROVAL_STATES 常量已删除（未使用，oxlint 清理）
 */

/**
 * 按 sessionId 缓存的快照校对实例
 *
 * @type {Map<string, { syncFromSnapshot: () => Promise<void>, isSyncing: import('vue').Ref<boolean> }>}
 */
const instanceCache = new Map()

/**
 * 工具调用状态优先级（数值越大优先级越高）
 * 用于判断本地状态是否滞后于快照
 *
 * @param {string} status
 * @returns {number}
 */
function _toolCallStatusPriority(status) {
  switch (status) {
    case ToolCallStatus.PENDING: return 0
    case ToolCallStatus.WAITING: return 1
    case ToolCallStatus.RUNNING: return 2
    case ToolCallStatus.COMPLETED: return 3
    case ToolCallStatus.FAILED: return 3
    case ToolCallStatus.TIMEOUT: return 3
    default: return 0
  }
}

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
 * 创建指定会话的快照校对实例
 *
 * @param {string} sessionId
 * @returns {{ syncFromSnapshot: () => Promise<void>, isSyncing: import('vue').Ref<boolean> }}
 */
function createSnapshotSyncInstance(sessionId) {
  const isSyncing = ref(false)
  /** @type {number | null} */
  let debounceTimer = null
  /** @type {Promise<void> | null} */
  let inflightPromise = null

  /**
   * 对比本地消息与快照消息，差异部分以快照为准覆盖本地
   *
   * @param {Object} sessionStore
   * @param {Array} backendMessages - 后端快照消息列表
   */
  const _reconcileMessages = (sessionStore, backendMessages) => {
    if (!Array.isArray(backendMessages) || backendMessages.length === 0) return
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session) {
      logger.warn(`[SnapshotSync] 会话不存在，跳过消息校对: session=${sessionId}`)
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
        // 本地存在：增量合并（mergeMessageFromBackend 内含流式保护逻辑）
        mergeMessageFromBackend(localMsg, transformed)
      } else {
        // 本地缺失：添加消息（addMessageToSessionById 内部已做幂等处理）
        sessionStore.addMessageToSessionById(sessionId, backendMsg)
      }
      reconciledCount++
    }
    if (reconciledCount > 0) {
      logger.info(`[SnapshotSync] 消息校对完成: session=${sessionId}, count=${reconciledCount}`)
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
      const toolCallId = backendTc.tool_call_id || backendTc.id
      if (!toolCallId) continue

      const localTc = sessionStore.getToolCallById(sessionId, toolCallId)
      if (localTc) {
        // 状态滞后判断：本地非终态、快照为终态时以快照为准
        const localPriority = _toolCallStatusPriority(localTc.status)
        const backendPriority = _toolCallStatusPriority(backendTc.status)
        if (backendPriority > localPriority) {
          // 通过 addOrUpdateToolCall 触发响应式更新（携带 messageBackendId 以定位消息）
          const updateData = {
            ...backendTc,
            messageBackendId: localTc.messageBackendId,
          }
          sessionStore.addOrUpdateToolCall(sessionId, updateData)
          reconciledCount++
        } else if (
          // approval 字段完整性检查：本地 approval 为空但快照有 approval 数据时合并
          // 解决刷新后 API 返回的 tool_calls 不含 approval 字段的问题
          (!localTc.approval || Object.keys(localTc.approval).length === 0) &&
          backendTc.approval && Object.keys(backendTc.approval).length > 0
        ) {
          sessionStore.setApprovalToToolCall(sessionId, toolCallId, backendTc.approval)
          reconciledCount++
        }
      } else {
        // 本地缺失：通过 addOrUpdateToolCall 添加
        sessionStore.addOrUpdateToolCall(sessionId, backendTc)
        reconciledCount++
      }
    }
    if (reconciledCount > 0) {
      logger.info(`[SnapshotSync] 工具调用校对完成: session=${sessionId}, count=${reconciledCount}`)
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
      const toolCallId = backendApproval.interrupt_id || backendApproval.tool_call_id
      if (!toolCallId) continue

      const localTc = sessionStore.getToolCallById(sessionId, toolCallId)
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
        sessionStore.setApprovalToToolCall(sessionId, toolCallId, backendApproval)
        reconciledCount++
      }
    }
    if (reconciledCount > 0) {
      logger.info(`[SnapshotSync] 审批校对完成: session=${sessionId}, count=${reconciledCount}`)
    }
  }

  /**
   * 执行一次快照校对（带超时保护）
   *
   * @returns {Promise<void>}
   */
  const _performSync = async () => {
    if (isSyncing.value) {
      logger.debug(`[SnapshotSync] 校对进行中，跳过本次: session=${sessionId}`)
      return
    }
    isSyncing.value = true
    try {
      const sessionStore = useSessionStore()

      // 带超时的请求，避免快照接口卡死阻塞 UI
      const fetchPromise = getSessionSnapshot(sessionId)
      const timeoutPromise = new Promise((_, reject) => {
        const timer = setTimeout(() => {
          reject(new Error(`Snapshot timeout: ${sessionId}`))
        }, SNAPSHOT_TIMEOUT_MS)
        // 清理 timer 避免内存泄漏
        fetchPromise.finally(() => clearTimeout(timer))
      })

      let resp
      try {
        resp = await Promise.race([fetchPromise, timeoutPromise])
      } catch (error) {
        logger.warn(`[SnapshotSync] 快照请求失败，保留本地状态: session=${sessionId}, error=${error.message}`)
        return
      }

      const data = resp?.data?.data
      if (!data) {
        logger.warn(`[SnapshotSync] 快照响应数据为空: session=${sessionId}`)
        return
      }

      // 校对消息（data.messages 可选）
      if (data.messages !== undefined) {
        _reconcileMessages(sessionStore, data.messages)
      }

      // 校对工具调用（data.tool_calls 可选）
      if (data.tool_calls !== undefined) {
        _reconcileToolCalls(sessionStore, data.tool_calls)
      }

      // 校对审批（data.pending_approvals 或 data.approvals 可选）
      const approvals = data.pending_approvals || data.approvals
      if (approvals !== undefined) {
        _reconcileApprovals(sessionStore, approvals)
      }

      logger.info(`[SnapshotSync] 快照校对成功: session=${sessionId}`)
    } catch (error) {
      // 任何异常都不阻塞 UI，保留本地状态
      logger.warn(`[SnapshotSync] 快照校对异常，保留本地状态: session=${sessionId}, error=${error?.message || error}`)
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
 * 快照校对组合式函数
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
    instance = createSnapshotSyncInstance(sessionId)
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

// ==================== M19-c: 深度研究任务快照校对（按 taskId） ====================

import { getResearchSnapshot } from '@/api/research'
import { useResearchStore } from '@/stores/research'
import { useApprovalStore } from '@/stores/approval'

/**
 * 按 taskId 缓存的快照校对实例
 *
 * @type {Map<string, { syncFromSnapshot: () => Promise<void>, isSyncing: import('vue').Ref<boolean> }>}
 */
const taskInstanceCache = new Map()

/**
 * 创建指定任务的快照校对实例（深度研究模块专用）
 *
 * 统一底层修复（实时同步根因）：
 * 与 createSnapshotSyncInstance 保持一致的优先级保护策略，
 * 防止快照中滞后的状态覆盖本地已通过实时事件更新的高优先级状态。
 *
 * 原实现直接调用 setApprovalToToolCall / updateOrAddToolResult 无条件覆盖，
 * 导致深度研究模块的 WAITING 状态可能被快照中的 PENDING 覆盖。
 *
 * 修复后：
 *   - tool_call：仅当快照 status 优先级 > 本地时才更新
 *   - approval：仅当快照 state 优先级 > 本地时才更新
 *   优先级函数复用模块级 _toolCallStatusPriority / _approvalStatePriority
 *
 * @param {string} taskId - 深度研究任务 ID
 * @returns {{ syncFromSnapshot: () => Promise<void>, isSyncing: import('vue').Ref<boolean> }}
 */
function createTaskSnapshotSyncInstance(taskId) {
  const isSyncing = ref(false)
  /** @type {number | null} */
  let debounceTimer = null
  /** @type {Promise<void> | null} */
  let inflightPromise = null

  const syncFromSnapshot = async () => {
    if (debounceTimer) {
      clearTimeout(debounceTimer)
    }
    return new Promise((resolve) => {
      debounceTimer = setTimeout(async () => {
        debounceTimer = null
        if (inflightPromise) {
          await inflightPromise
          resolve()
          return
        }
        isSyncing.value = true
        inflightPromise = (async () => {
          try {
            const response = await getResearchSnapshot(taskId)
            const snapshot = response?.data?.data || response?.data || {}
            const researchStore = useResearchStore()
            const approvalStore = useApprovalStore()

            // 获取本地已有工具调用，用于优先级比较
            const localToolCalls = researchStore.getToolCalls(taskId)
            const localToolCallMap = new Map(
              localToolCalls.map(tc => [
                tc.id || tc.tool_call_id,
                tc,
              ])
            )

            // 1. 更新 tool_calls（对象结构，key=tool_call_id）
            // 统一优先级保护：仅当快照 status 优先级 > 本地时才更新
            const toolCalls = snapshot.tool_calls || {}
            for (const [toolCallId, tc] of Object.entries(toolCalls)) {
              if (!toolCallId || !tc) continue
              const state = tc.state || ''
              const isResultAvailable = state in {
                'output-available': true,
                'output-error': true,
              }
              const backendStatus = tc.status || (isResultAvailable ? 'completed' : 'running')

              // 优先级比较：本地非终态、快照为更高优先级时才更新
              const localTc = localToolCallMap.get(toolCallId)
              if (localTc) {
                const localPriority = _toolCallStatusPriority(localTc.status)
                const backendPriority = _toolCallStatusPriority(backendStatus)
                if (backendPriority <= localPriority) {
                  // 快照优先级不超过本地，跳过状态更新（保护 WAITING/RUNNING 等中间态）
                  // 但仍需检查 approval 字段完整性：本地 approval 为空但快照有 approval 数据时合并
                  // 解决刷新后 API 返回的 tool_calls 不含 approval 字段的问题
                  if (
                    (!localTc.approval || Object.keys(localTc.approval).length === 0) &&
                    tc.approval && Object.keys(tc.approval).length > 0
                  ) {
                    researchStore.setApprovalToToolCall(taskId, toolCallId, tc.approval)
                  }
                  continue
                }
              }

              const data = {
                id: toolCallId,
                tool_call_id: toolCallId,
                name: tc.name,
                tool_name: tc.name,
                parameters: tc.parameters,
                args: tc.parameters,
                state: state,
                status: backendStatus,
                result: tc.result,
                error: tc.error,
                is_internal: tc.is_internal || false,
              }
              if (isResultAvailable || tc.result != null || tc.error) {
                researchStore.updateOrAddToolResult(taskId, data)
              } else {
                researchStore.addOrUpdateToolCall(taskId, data)
              }
            }

            // 2. 更新 approvals（对象结构，key=interrupt_id）
            // 统一优先级保护：仅当快照 state 优先级 > 本地时才更新
            const approvals = snapshot.approvals || {}
            for (const [interruptId, approval] of Object.entries(approvals)) {
              if (!interruptId || !approval) continue
              const toolCallId = approval.tool_call_id || interruptId

              // 优先级比较：本地非终态、快照为更高优先级时才更新
              // 注意：本地 approval 为空时（_approvalStatePriority(undefined) = -1），
              // 任何快照状态都会 > -1，从而合并缺失的 approval 数据
              const localTc = localToolCallMap.get(toolCallId)
              const localApprovalState = localTc?.approval?.state
              const localPriority = _approvalStatePriority(localApprovalState)
              const backendPriority = _approvalStatePriority(approval.state)
              if (backendPriority <= localPriority) {
                // 快照优先级不超过本地，跳过（保护 waiting/processing 等中间态）
                continue
              }

              researchStore.setApprovalToToolCall(taskId, toolCallId, approval)
              // 同步到 approvalStore（跨模块统一审批状态）
              approvalStore.updateApprovalState(interruptId, approval.state, {
                sessionId: snapshot.chat_session_id,
                taskId,
              })
            }

            // 3. 更新任务状态（current_step / final_report 由轮询/SSE 处理，此处不覆盖）
            logger.info(
              `[SnapshotSync] task=${taskId} 快照校对完成: ` +
              `${Object.keys(toolCalls).length} 个工具, ${Object.keys(approvals).length} 个审批`
            )
          } catch (err) {
            logger.warn(
              `[SnapshotSync] task=${taskId} 快照校对失败（非致命）: ${err?.message || err}`
            )
          } finally {
            isSyncing.value = false
            inflightPromise = null
          }
        })()
        await inflightPromise
        resolve()
      }, SNAPSHOT_DEBOUNCE_MS)
    })
  }

  return { syncFromSnapshot, isSyncing }
}

/**
 * 深度研究任务快照校对 composable（v5 M19-c 新增）
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
    instance = createTaskSnapshotSyncInstance(taskId)
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
