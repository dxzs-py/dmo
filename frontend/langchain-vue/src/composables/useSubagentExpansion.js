import { ref, watch } from 'vue'
import { SUBAGENT_STATUS } from '../utils/subagentStatus'
import settings from '../config/settings'

/**
 * 子代理展开状态组合式函数（spec unify-agent-research-display-architecture）
 *
 * 封装 ChatMessage / ResearchTaskDetail 逐字重复的三件套：
 * - expandedThreadIds（Set）：已展开的子代理 threadId 集合；
 * - toggleSubagent(threadId)：点击卡片展开/收起；
 * - 待审批自动展开 watch：settings.autoExpandPendingConfirm 开启时，
 *   仅「等待你的确认」（INTERRUPTED_PENDING_USER_INPUT）的卡片自动展开
 *   （嵌套内层永不自动展开）。
 *
 * 两调用方的「拉取子代理元数据 watch」因触发源与数据来源不同
 * （hasSpawnTool → message.researchTaskId/sessionId  vs  taskId），
 * 不纳入本 composable，由调用方各自保留。
 *
 * @param {import('vue').Ref<Array>} subagentsRef - 子代理聚合视图数组的 ref/computed
 * @returns {{ expandedThreadIds: import('vue').Ref<Set>, toggleSubagent: (threadId: string) => void }}
 */
export function useSubagentExpansion(subagentsRef) {
  const expandedThreadIds = ref(new Set())

  const toggleSubagent = (threadId) => {
    const next = new Set(expandedThreadIds.value)
    if (next.has(threadId)) next.delete(threadId)
    else next.add(threadId)
    expandedThreadIds.value = next
  }

  // spec D9：autoExpandPendingConfirm 开启时，仅「等待你的确认」的卡片自动展开（嵌套内层永不自动展开）
  watch(subagentsRef, (list) => {
    if (!settings.autoExpandPendingConfirm) return
    const next = new Set(expandedThreadIds.value)
    let changed = false
    for (const sa of list) {
      if (sa.status === SUBAGENT_STATUS.INTERRUPTED_PENDING_USER_INPUT && !next.has(sa.threadId)) {
        next.add(sa.threadId)
        changed = true
      }
    }
    if (changed) expandedThreadIds.value = next
  })

  return { expandedThreadIds, toggleSubagent }
}
