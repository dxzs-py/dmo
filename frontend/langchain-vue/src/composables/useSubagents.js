/**
 * 子代理列表组合式函数（spec D10 前端数据源）
 *
 * 职责：
 * - 通过 GET /ai-engine/subagents/?parent_thread_id=xxx 拉取子代理元数据
 *   （threadId/agentName/status/resultPreview/pendingInterruptInfo/assistantMessageId）。
 * - 维护进程内元数据缓存（threadId → 元数据），供 ChatMessage / 深度研究模块复用。
 * - 子代理工具调用 / 正文 / 审批由事件驱动增量更新（见 handleSessionEvent /
 *   handleTaskEvent / toolCallHandler），本模块只负责元数据拉取与关联。
 * - 事件驱动刷新（scheduleSubagentsRefresh）：子代理工具事件（携带
 *   subagentThreadId）到达时由 toolCallHandler 调度，去抖合并后重新拉取元数据，
 *   解决非触发浏览器停留旧快照（双浏览器不同步）。
 */
import { ref } from 'vue'
import { getSubagents } from '@/api/subagent'
import { logger } from '@/utils/logger'

// 模块级缓存：跨组件实例共享（同一父线程只拉取一次，事件增量更新元数据）。
// 导出 ref 供组件按 parentThreadId 响应式过滤（事件驱动刷新后自动更新）。
export const subagentsMetaMap = ref({})

// 事件驱动刷新队列：去抖合并同一批次内的多次刷新（如批量工具事件）
const _refreshQueue = new Set()
let _refreshTimer = null
const _REFRESH_DEBOUNCE_MS = 800

async function _fetchSubagents(parentThreadId) {
  if (!parentThreadId) return []
  try {
    const res = await getSubagents(parentThreadId)
    const data = res?.data?.data || res?.data || {}
    const list = Array.isArray(data.subagents) ? data.subagents : []
    const map = { ...subagentsMetaMap.value }
    for (const sa of list) {
      if (sa && sa.threadId) {
        map[sa.threadId] = sa
      }
    }
    subagentsMetaMap.value = map
    return list
  } catch (err) {
    logger.warn(`[Subagents] 拉取子代理列表失败: parent=${parentThreadId}, err=${err?.message || err}`)
    return []
  }
}

/**
 * 事件驱动刷新（模块级导出，Store/事件处理器直接复用）：
 * 子代理工具/审批事件到达时调用，去抖合并后重新拉取父线程下子代理元数据。
 *
 * @param {string} parentThreadId - 父线程 thread_id（session_id 或 task_id）
 */
export function scheduleSubagentsRefresh(parentThreadId) {
  if (!parentThreadId) return
  _refreshQueue.add(parentThreadId)
  if (_refreshTimer) return
  _refreshTimer = setTimeout(async () => {
    _refreshTimer = null
    const parents = [..._refreshQueue]
    _refreshQueue.clear()
    await Promise.all(parents.map((p) => _fetchSubagents(p)))
  }, _REFRESH_DEBOUNCE_MS)
}

export function useSubagents() {
  const loading = ref(false)

  /**
   * 拉取指定父线程下的子代理元数据并合并进缓存。
   *
   * @param {string} parentThreadId - 父线程 thread_id（session_id 或 task_id）
   * @returns {Promise<Array>} 本次拉取到的子代理元数据列表
   */
  async function fetchSubagents(parentThreadId) {
    if (!parentThreadId) return []
    loading.value = true
    try {
      return await _fetchSubagents(parentThreadId)
    } finally {
      loading.value = false
    }
  }

  /**
   * 按 threadId 获取子代理元数据（缓存未命中返回 null）。
   * @param {string} threadId
   * @returns {Object|null}
   */
  function getSubagentMeta(threadId) {
    return subagentsMetaMap.value[threadId] || null
  }

  /**
   * 按消息 ID 获取关联的子代理元数据列表（assistantMessageId 匹配）。
   *
   * 子代理在 spawn 时 metadata 记录 assistant_message_id（后端 GET 接口返回），
   * 据此将子代理卡片挂到对应 AI 消息下方。
   *
   * @param {string} messageId - 后端消息 ID（message.backendId 或 message.id）
   * @returns {Array}
   */
  function getSubagentsByMessage(messageId) {
    if (!messageId) return []
    const idStr = String(messageId)
    return Object.values(subagentsMetaMap.value).filter(
      sa => sa && sa.assistantMessageId && String(sa.assistantMessageId) === idStr
    )
  }

  return {
    loading,
    fetchSubagents,
    getSubagentMeta,
    getSubagentsByMessage,
    scheduleRefresh: scheduleSubagentsRefresh,
  }
}
