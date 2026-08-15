/**
 * 子代理列表组合式函数（spec D10 前端数据源）
 *
 * 职责：
 * - 通过 GET /ai-engine/subagents/?parent_thread_id=xxx 拉取子代理元数据
 *   （threadId/agentName/status/resultPreview/pendingInterruptInfo/assistantMessageId）。
 * - 维护进程内元数据缓存（threadId → 元数据），供 ChatMessage / 深度研究模块复用。
 * - 子代理工具调用 / 正文 / 审批由事件驱动增量更新（见 handleSessionEvent /
 *   handleTaskEvent / toolCallHandler），本模块只负责元数据拉取与关联。
 */
import { ref } from 'vue'
import { getSubagents } from '@/api/subagent'
import { logger } from '@/utils/logger'

// 模块级缓存：跨组件实例共享（同一父线程只拉取一次，事件增量更新元数据）
const _metaMap = ref({})

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
      const res = await getSubagents(parentThreadId)
      const data = res?.data?.data || res?.data || {}
      const list = Array.isArray(data.subagents) ? data.subagents : []
      const map = { ..._metaMap.value }
      for (const sa of list) {
        if (sa && sa.threadId) {
          map[sa.threadId] = sa
        }
      }
      _metaMap.value = map
      return list
    } catch (err) {
      logger.warn(`[Subagents] 拉取子代理列表失败: parent=${parentThreadId}, err=${err?.message || err}`)
      return []
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
    return _metaMap.value[threadId] || null
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
    return Object.values(_metaMap.value).filter(
      sa => sa && sa.assistantMessageId && String(sa.assistantMessageId) === idStr
    )
  }

  return {
    loading,
    fetchSubagents,
    getSubagentMeta,
    getSubagentsByMessage,
  }
}
