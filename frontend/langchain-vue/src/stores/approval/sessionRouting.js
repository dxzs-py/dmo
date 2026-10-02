/**
 * 审批双域路由（sessionStore / researchStore 数据源归属推导，ARCH-13 F⑤ 单一实现）
 *
 * 原实现中 _handleTimeout / _handleProcessing / _handleWaiting / _handleProcessed
 * 四处各有一段近似重复的 effectiveSessionId 推导，现收敛为本函数。
 *
 * 推导优先级：
 * 1. 事件参数 sessionId（调用方显式传入）
 * 2. pendingApprovals 既有条目的 sessionId（创建条目时携带，可能比参数更准确）
 * 3. 回退 currentSessionId —— 仅 chat 来源；独立 deep_research 无会话上下文，
 *    不使用 currentSessionId 兜底（聊天残留值），否则会误走 sessionStore 分支
 *    （researchStore 状态不更新，跨浏览器按钮状态不同步）。
 *
 * @param {string|null} sessionId - 事件参数中的聊天会话 ID
 * @param {string} source - 事件来源 'chat' | 'deep_research'
 * @param {Object|null} existingEntry - pendingApprovals 既有条目（可空）
 * @param {Object} sessionStore - session store 实例
 * @returns {string|null} 生效的 sessionId（null 表示走 researchStore 双域路由分支）
 */
export const resolveEffectiveSessionId = (sessionId, source, existingEntry, sessionStore) => (
  sessionId
    || existingEntry?.sessionId
    || (source === 'deep_research' || existingEntry?.source === 'deep_research'
      ? null
      : sessionStore.currentSessionId)
)
