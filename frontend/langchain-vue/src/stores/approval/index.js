import { defineStore } from 'pinia'
import { createApprovalState, getResearchStore } from './state'
import { createRestActions } from './restActions'
import { createEventHandlers } from './eventHandlers'
import { createRestoreActions } from './restore'
import { createCleanupActions } from './cleanup'

/**
 * approval store（审批唯一状态权威，ARCH-13 拆分自原 stores/approval.js 单文件）
 *
 * 架构说明：原单文件六职责合一，现按职责切片拆分到 ./approval/ 子目录，
 * 本文件仅负责状态初始化、装配各切片与对外导出（store id 'approval' 与
 * 全部导出符号保持不变，消费方 import 路径 '@/stores/approval' 自动解析本目录）。
 *
 * 切片清单：
 * - state.js          : 状态权威（pendingApprovals Map / pendingBindQueue / 内部非响应式状态）
 *                       + getResearchStore 惰性解析（动态 import + Promise 缓存破环）
 * - constants.js      : 常量单源（APPROVAL_EXPIRY_MS / APPROVAL_TIMEOUT_HINT 文案等）
 * - sessionRouting.js : 双域路由样板单一实现（resolveEffectiveSessionId，F⑤ 收敛）
 * - eventHandlers.js  : WS/SSE 审批事件消费入口（handleApprovalEvent 五分支分发；
 *                       入站数据已由事件入口统一 toCamelCase，此处不再二次转换——F⑥）
 * - restActions.js    : REST 封装（executeApproval + /approvals/{id}/resume/ + 串行队列）
 * - restore.js        : 快照/历史恢复（restoreFromSession / restoreFromSSEHistory）
 * - cleanup.js        : 超时清理与待绑定队列（cleanupExpired / clearByTaskId / clearAll /
 *                       flushPendingBindQueue / startCleanup）
 *
 * 依赖方向（无模块加载期环）：
 * - index → 各切片（静态，单向）；
 * - restActions ← eventHandlers 的依赖（_triggerTimeoutResume 复用 executeApproval）
 *   经装配顺序 + ctx 注入（先创建 restActions 再创建 eventHandlers）解决；
 * - research store 经 state.js 的 getResearchStore 动态 import + Promise 缓存惰性解析；
 * - sync store 经 restActions.js 的动态 import('../sync') 惰性解析
 *   （sync.js 装配了 approval 事件处理器，approval 不静态依赖 sync）。
 * - session store 为单向静态依赖（session 不反向依赖 approval）。
 */
export const useApprovalStore = defineStore('approval', () => {
  // === 状态初始化（状态权威） ===
  const state = createApprovalState()

  // === 装配切片（依赖经 ctx 注入，模块间无静态耦合） ===
  // 先创建 restActions（executeApproval 供 eventHandlers 的超时恢复复用）
  const restActions = createRestActions({
    pendingApprovals: state.pendingApprovals,
    _approvalQueues: state._approvalQueues,
  })
  const eventHandlers = createEventHandlers({
    pendingApprovals: state.pendingApprovals,
    _timeoutResumeKeys: state._timeoutResumeKeys,
    _approvalSyncTimers: state._approvalSyncTimers,
    getResearchStore,
    executeApproval: restActions.executeApproval,
  })
  const restore = createRestoreActions({
    pendingApprovals: state.pendingApprovals,
  })
  const cleanup = createCleanupActions({
    pendingApprovals: state.pendingApprovals,
    pendingBindQueue: state.pendingBindQueue,
    cleanupTimerBox: state.cleanupTimerBox,
  })

  // 启动自动清理定时器（与原实现一致：setup 内即启动）
  cleanup.startCleanup()

  return {
    pendingApprovals: state.pendingApprovals,
    pendingBindQueue: state.pendingBindQueue,
    handleApprovalEvent: eventHandlers.handleApprovalEvent,
    executeApproval: restActions.executeApproval,
    restoreFromSession: restore.restoreFromSession,
    restoreFromSSEHistory: restore.restoreFromSSEHistory,
    cleanupExpired: cleanup.cleanupExpired,
    clearByTaskId: cleanup.clearByTaskId,
    clearAll: cleanup.clearAll,
    flushPendingBindQueue: cleanup.flushPendingBindQueue,
  }
})
