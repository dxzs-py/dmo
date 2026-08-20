/**
 * session store 切片：消息版本域（版本切换 / 版本固化 / 未固化选择持久化）
 * （拆分自原 stores/session.js，C5/cq-04 Task 3，行为保持）
 *
 * 持有闭包状态：_finalizeTimers（会话级固化计时器 Map）
 *
 * 跨切片运行时依赖（runtimeDeps，由 index.js 在全部切片创建后注册）：
 * - _rebuildToolCallsMapForVersion / syncMessageToolCalls（toolCallState）
 */
import { watch } from 'vue'
import { chatAPI } from '@/api/chat'
import { useUserStore } from '../user'
import { ElMessage } from 'element-plus'
import { logger } from '../../utils/logger'
import { transformFrontendMessageToBackend } from '../../utils/sessionTransformers'
import {
  CURRENT_SESSION_ID_KEY,
  FINALIZE_TIMEOUT_MS,
  _safeGetLocalStorage,
  _safeSetLocalStorage,
  _safeRemoveLocalStorage,
  _versionSelectionKey,
} from './helpers'

export const createVersionFlowSlice = (sessionList, runtimeDeps) => {
  const { sessions, currentSessionId, touchSessionUpdatedAt } = sessionList

  const userStore = useUserStore()

  // ==================== 版本固化机制（Task 6） ====================
  //
  // 触发条件：①发送新消息前（chatStore.sendMessage 调用 finalizeLatestMessage）
  //           ②当前会话 Tab 活跃，5 分钟无有效操作（切换版本/重新生成）超时
  // 有效操作：切换版本、点击重新生成（resetFinalizeTimer 重置计时器）
  // 作废场景：切换到其他会话（currentSessionId watch）、链式删除（竞态防护）、
  //           固化完成、浏览器 Tab 后台休眠（定时器回调检查 visibilityState）
  /** 会话级固化计时器：Map<sessionId, timeoutId> */
  const _finalizeTimers = {}

  // 持久化 currentSessionId（Task 15 P0 修复）：
  // 刷新后从 localStorage 恢复，避免 loadSessionsFromBackend fallback 到 sessions[0]
  // 选中错误会话。newId 为空时移除条目，防止跨用户残留。
  // 会话切换（Task 6.5）：切走时旧会话固化计时器作废；切到新会话时若存在
  // 未固化多版本则重新计时（watch 回调执行时所有 const 函数均已初始化，安全）。
  watch(currentSessionId, (newId, oldId) => {
    if (oldId && oldId !== newId) {
      clearFinalizeTimer(oldId)
    }
    if (newId) {
      _startFinalizeTimer(newId)
    }
    if (newId) {
      _safeSetLocalStorage(CURRENT_SESSION_ID_KEY, newId)
    } else {
      _safeRemoveLocalStorage(CURRENT_SESSION_ID_KEY)
    }
  })

  /**
   * 执行版本固化：将当前选中版本设为本轮主消息并持久化到后端。
   * 后端 PATCH 保存 versions/current_version/is_finalized 并广播 message_finalized，
   * 所有浏览器同步（其他 Tab 停止计时器、禁用重生成、版本切换降级只读）。
   * @param {string} sessionId
   * @param {number} messageIndex
   * @param {Object} [opts]
   * @param {boolean} [opts.viaTimeout=false] - 超时触发时展示「版本已固化」toast
   * @returns {Promise<boolean>} 是否固化成功
   */
  const finalizeMessage = async (sessionId, messageIndex, { viaTimeout = false } = {}) => {
    const session = sessions.value.find(s => s.id === sessionId)
    const message = session?.messages?.[messageIndex]
    if (!message || message.role !== 'assistant') return false
    if (message.isFinalized) return false
    // 流式进行中不固化（incomplete 版本禁止固化为主版本）
    if (message.isStreaming) return false
    // 单版本消息无需固化（主消息本就已持久化）
    if (!Array.isArray(message.versions) || message.versions.length < 2) return false
    // 重生成中断版本（Task 9）：标记 incomplete 禁止固化为主版本
    const activeVer = message.versions[message.currentVersion]
    if (activeVer?.incomplete) {
      logger.info(`[Session] 当前版本为 incomplete（重生成中断），禁止固化: session=${sessionId}, message=${message.backendId || message.id}`)
      return false
    }

    message.isFinalized = true
    let persisted = true
    if (userStore.isLoggedIn && message.backendId) {
      try {
        const backendMsg = transformFrontendMessageToBackend(message)
        await chatAPI.updateMessage(message.backendId, backendMsg)
      } catch (error) {
        logger.error(`[Session] 版本固化持久化失败: session=${sessionId}, message=${message.backendId}`, error)
        message.isFinalized = false // 持久化失败回滚标记，待下次触发重试
        persisted = false
      }
    }
    if (persisted) {
      clearFinalizeTimer(sessionId)
      _safeRemoveLocalStorage(_versionSelectionKey(sessionId, message.backendId || message.id))
      touchSessionUpdatedAt(sessionId)
      if (viaTimeout) {
        ElMessage({ type: 'success', message: '版本已固化', duration: 2500 })
      }
      logger.info(`[Session] 版本固化完成: session=${sessionId}, message=${message.backendId || message.id}, currentVersion=${message.currentVersion}`)
    }
    return persisted
  }

  /**
   * 固化当前会话末轮 assistant 消息（若有未固化多版本）。
   * 发送新消息前 / 超时触发时调用。
   * @param {string} sessionId
   * @param {Object} [opts]
   * @param {boolean} [opts.viaTimeout=false]
   * @returns {Promise<boolean>}
   */
  const finalizeLatestMessage = async (sessionId, { viaTimeout = false } = {}) => {
    if (!sessionId) return false
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session?.messages?.length) return false
    // 从末尾向前找最后一条 assistant 消息（末轮 AI 回复）
    for (let i = session.messages.length - 1; i >= 0; i--) {
      if (session.messages[i].role === 'assistant') {
        return finalizeMessage(sessionId, i, { viaTimeout })
      }
    }
    return false
  }

  /** 启动会话固化计时器（内部检查条件，未满足则不启动） */
  const _startFinalizeTimer = (sessionId) => {
    clearFinalizeTimer(sessionId)
    if (!sessionId || sessionId !== currentSessionId.value) return
    // 仅当末轮存在未固化多版本时才需要超时固化
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session?.messages?.length) return
    let needsFinalize = false
    for (let i = session.messages.length - 1; i >= 0; i--) {
      const m = session.messages[i]
      if (m.role === 'assistant') {
        needsFinalize = !m.isFinalized
          && !m.isStreaming
          && Array.isArray(m.versions) && m.versions.length >= 2
        break
      }
    }
    if (!needsFinalize) return
    _finalizeTimers[sessionId] = setTimeout(() => {
      delete _finalizeTimers[sessionId]
      // Tab 后台休眠/挂起时放弃超时固化（spec 6.1：浏览器节流，不依赖）
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
      finalizeLatestMessage(sessionId, { viaTimeout: true }).catch(() => {})
    }, FINALIZE_TIMEOUT_MS)
  }

  /** 重置会话固化计时器（有效操作：切换版本/点击重新生成时调用） */
  const resetFinalizeTimer = (sessionId) => {
    _startFinalizeTimer(sessionId)
  }

  /** 作废会话固化计时器（会话切换/链式删除/固化完成/会话删除时调用） */
  const clearFinalizeTimer = (sessionId) => {
    if (_finalizeTimers[sessionId]) {
      clearTimeout(_finalizeTimers[sessionId])
      delete _finalizeTimers[sessionId]
    }
  }

  /**
   * 持久化未固化选中版本索引到 localStorage（刷新/关闭兜底，Task 6.6）。
   * 切换版本时调用；固化后移除。
   */
  const persistSelectedVersion = (sessionId, messageId, versionIndex) => {
    if (!sessionId || !messageId) return
    _safeSetLocalStorage(_versionSelectionKey(sessionId, messageId), String(versionIndex))
  }

  /**
   * 会话详情加载后恢复未固化选中版本（刷新兜底，Task 6.6）。
   * 已固化消息以后端 current_version 为准，不覆盖。
   */
  const restoreSelectedVersions = (sessionId) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session?.messages) return
    for (const message of session.messages) {
      if (message.role !== 'assistant' || message.isFinalized) continue
      if (!Array.isArray(message.versions) || message.versions.length < 2) continue
      const key = _versionSelectionKey(sessionId, message.backendId || message.id)
      const savedIdx = _safeGetLocalStorage(key)
      if (savedIdx == null) continue
      const idx = Number(savedIdx)
      if (!Number.isInteger(idx) || idx < 0 || idx >= message.versions.length) continue
      if (idx === message.currentVersion) continue
      // 仅恢复视图选择（纯视图切换，不写后端）
      message.currentVersion = idx
      const version = message.versions[idx]
      message.content = version.content
      message.sources = version.sources
      message.plan = version.plan
      message.chainOfThought = version.chainOfThought
      message.toolCalls = version.toolCalls
      message.reasoning = version.reasoning
      message.suggestions = version.suggestions
      message.context = version.context
      message.attachmentIds = version.attachmentIds || message.attachmentIds
      message.subagentContents = version.subagentContents || {}
      runtimeDeps._rebuildToolCallsMapForVersion(sessionId, message, version.toolCalls)
    }
    // 恢复选择后重新计时（若仍有未固化多版本）
    if (currentSessionId.value === sessionId) {
      _startFinalizeTimer(sessionId)
    }
  }

  const switchMessageVersion = (sessionId, messageIndex, versionIndex) => {
    const session = sessions.value.find(s => s.id === sessionId)
    if (session && session.messages[messageIndex]) {
      const message = session.messages[messageIndex]
      if (message.versions && versionIndex >= 0 && versionIndex < message.versions.length) {
        message.currentVersion = versionIndex
        const version = message.versions[versionIndex]
        message.content = version.content
        message.sources = version.sources
        message.plan = version.plan
        message.chainOfThought = version.chainOfThought
        message.toolCalls = version.toolCalls
        message.reasoning = version.reasoning
        message.suggestions = version.suggestions
        message.context = version.context
        message.attachmentIds = version.attachmentIds || message.attachmentIds
        // 引用同步：message.subagentContents 恒等于活跃版本快照（D5），
        // 流式更新活跃版本时顶层与版本同步推进；历史版本快照保持不可变
        message.subagentContents = version.subagentContents || {}
        // 版本切换后重建 toolCallsMap 索引（剔除旧版本条目，防止污染）
        runtimeDeps._rebuildToolCallsMapForVersion(sessionId, message, version.toolCalls)
        runtimeDeps.syncMessageToolCalls(sessionId)
        // 版本切换为纯视图操作：不写后端（后端 versions 由固化时统一持久化），
        // 避免每次切换触发 PATCH 造成跨浏览器版本回跳。
        touchSessionUpdatedAt(sessionId)
        // 有效操作：重置固化计时器 + 持久化选中版本索引（刷新兜底）
        resetFinalizeTimer(sessionId)
        persistSelectedVersion(sessionId, message.backendId || message.id, versionIndex)
      }
    }
  }

  return {
    // actions
    finalizeMessage,
    finalizeLatestMessage,
    resetFinalizeTimer,
    clearFinalizeTimer,
    restoreSelectedVersions,
    switchMessageVersion,
  }
}
