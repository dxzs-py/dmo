/**
 * session store（领域切片组合架构，拆分自原 stores/session.js，C5/cq-04 Task 3）
 *
 * 组装四个切片：
 * - sessionList：会话列表加载 / 初始化 / 知识库选择（sessions 等共享 ref 持有者）
 * - sessionCrud：会话生命周期 CRUD（创建/切换/删除/更新/实时同步 upsert）
 * - messageFlow：消息域（消息增删、后端同步、流式状态、字段写入）
 * - versionFlow：消息版本域（版本切换 / 版本固化 / 未固化选择持久化）
 * - toolCallState：工具调用与审批状态域（toolCallsMap 为唯一真相源）
 *
 * 跨切片循环依赖（如 sessionList.loadSessionDetail 调 toolCallState 的 Map 回填、
 * toolCallState.addOrUpdateToolCall 调 messageFlow 的 debounced 同步）通过
 * runtimeDeps 注册表解决：所有跨切片调用均发生在 action 运行时而非切片创建期，
 * 故先依次创建切片、再统一注册依赖。
 */
import { defineStore } from 'pinia'
import { createSessionListSlice } from './sessionList'
import { createSessionCrudSlice } from './sessionCrud'
import { createMessageFlowSlice } from './messageFlow'
import { createVersionFlowSlice } from './versionFlow'
import { createToolCallStateSlice } from './toolCallState'

export const useSessionStore = defineStore('session', () => {
  /** 跨切片运行时依赖注册表（全部切片创建完成后填充） */
  const runtimeDeps = {}

  const sessionList = createSessionListSlice(runtimeDeps)
  const sessionCrud = createSessionCrudSlice(sessionList, runtimeDeps)
  const messageFlow = createMessageFlowSlice(sessionList, runtimeDeps)
  const versionFlow = createVersionFlowSlice(sessionList, runtimeDeps)
  const toolCallState = createToolCallStateSlice(sessionList, runtimeDeps)

  Object.assign(runtimeDeps, {
    // sessionList → toolCallState / versionFlow
    _syncToolCallsMapFromMessages: toolCallState._syncToolCallsMapFromMessages,
    syncAllMessageToolCallsFromMap: toolCallState.syncAllMessageToolCallsFromMap,
    restoreSelectedVersions: versionFlow.restoreSelectedVersions,
    _clearAllToolCallState: toolCallState._clearAllToolCallState,
    // sessionCrud / messageFlow → toolCallState
    _removeSessionToolCallState: toolCallState._removeSessionToolCallState,
    _pruneToolCallsByRemovedMessageIds: toolCallState._pruneToolCallsByRemovedMessageIds,
    // messageFlow → versionFlow
    clearFinalizeTimer: versionFlow.clearFinalizeTimer,
    // versionFlow → toolCallState
    _rebuildToolCallsMapForVersion: toolCallState._rebuildToolCallsMapForVersion,
    syncMessageToolCalls: toolCallState.syncMessageToolCalls,
    // toolCallState → messageFlow
    _debouncedToolSync: messageFlow._debouncedToolSync,
  })

  return {
    // ===== state =====
    sessions: sessionList.sessions,
    currentSessionId: sessionList.currentSessionId,
    selectedKnowledgeBase: sessionList.selectedKnowledgeBase,
    selectedKnowledgeBases: sessionList.selectedKnowledgeBases,
    knowledgeBases: sessionList.knowledgeBases,
    // 工具调用状态（Map 为唯一真相源，按 sessionId 索引）
    toolCallsMap: toolCallState.toolCallsMap,

    // ===== 会话列表 / CRUD =====
    loadSessionsFromBackend: sessionList.loadSessionsFromBackend,
    loadMoreSessions: sessionList.loadMoreSessions,
    loadSessionDetail: sessionList.loadSessionDetail,
    createNewSession: sessionCrud.createNewSession,
    switchSession: sessionCrud.switchSession,
    deleteSession: sessionCrud.deleteSession,
    removeSession: sessionCrud.removeSession,
    updateSessionTitle: sessionCrud.updateSessionTitle,
    upsertSession: sessionCrud.upsertSession,
    updateSessionFields: sessionCrud.updateSessionFields,
    consumeOptimisticSession: sessionCrud.consumeOptimisticSession,
    setSelectedKnowledgeBase: sessionList.setSelectedKnowledgeBase,
    setSelectedKnowledgeBases: sessionList.setSelectedKnowledgeBases,
    loadKnowledgeBases: sessionList.loadKnowledgeBases,
    initialize: sessionList.initialize,
    clearAllLocalData: sessionList.clearAllLocalData,
    touchSessionUpdatedAt: sessionList.touchSessionUpdatedAt,
    getSessionMessages: sessionList.getSessionMessages,

    // ===== 消息域 =====
    addMessageToSession: messageFlow.addMessageToSession,
    syncLastMessageToBackend: messageFlow.syncLastMessageToBackend,
    syncMessageByBackendIdToBackend: messageFlow.syncMessageByBackendIdToBackend,
    syncMessageToBackend: messageFlow.syncMessageToBackend,
    waitForSyncLock: messageFlow.waitForSyncLock,
    flushPendingSync: messageFlow.flushPendingSync,
    updateLastMessage: messageFlow.updateLastMessage,
    appendToLastMessage: messageFlow.appendToLastMessage,
    setApprovalToLastMessage: messageFlow.setApprovalToLastMessage,
    setStreamStateToLastMessage: messageFlow.setStreamStateToLastMessage,
    setStreamStateToMessageByIdx: messageFlow.setStreamStateToMessageByIdx,
    clearCurrentSessionMessages: messageFlow.clearCurrentSessionMessages,
    removeMessageFromSession: messageFlow.removeMessageFromSession,
    removeMessagePairFromSession: messageFlow.removeMessagePairFromSession,
    updateMessageFieldByBackendId: messageFlow.updateMessageFieldByBackendId,
    removeMessagesByIds: messageFlow.removeMessagesByIds,
    clearToolSyncTimer: messageFlow.clearToolSyncTimer,
    clearSyncSignature: messageFlow.clearSyncSignature,

    // ===== 版本域 =====
    switchMessageVersion: versionFlow.switchMessageVersion,
    finalizeLatestMessage: versionFlow.finalizeLatestMessage,
    resetFinalizeTimer: versionFlow.resetFinalizeTimer,
    clearFinalizeTimer: versionFlow.clearFinalizeTimer,

    // ===== 工具调用域 =====
    setApprovalToToolCall: toolCallState.setApprovalToToolCall,
    updateToolCallApprovalState: toolCallState.updateToolCallApprovalState,
    addOrUpdateToolCall: toolCallState.addOrUpdateToolCall,
    syncAllMessageToolCallsFromMap: toolCallState.syncAllMessageToolCallsFromMap,
    updateOrAddToolResult: toolCallState.updateOrAddToolResult,
    getToolCallById: toolCallState.getToolCallById,
    finalizeToolCallsInMap: toolCallState.finalizeToolCallsInMap,
    flushPendingApprovals: toolCallState.flushPendingApprovals,
    // 同步工具调用到消息（handleSessionEvent 中 message_added 后调用，解决审批组件错位问题）
    syncMessageToolCalls: toolCallState.syncMessageToolCalls,
  }
})
