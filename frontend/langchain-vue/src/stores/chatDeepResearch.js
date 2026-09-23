import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useSessionStore } from './session'
import { logger } from '../utils/logger'

/**
 * 聊天深度研究模式桥接层（Task 9 / spec Change 5：聊天模块与深度研究模块完全解耦）
 *
 * 聊天模块与深度研究模块是完全独立的两个模块，仅「聊天深度研究模式」
 * （在聊天中切换到 deep-research 模式）这一功能场景通过本桥接层关联：
 * - chat.js 与 research.js 互不静态 import；
 * - 本模块是唯一允许同时感知两个模块的模块，持有从 research.js
 *   chatDeepResearch 子域迁移而来的桥接状态（deepResearchTask /
 *   researchTaskId / researchContextInfo），并封装审批恢复等桥接逻辑；
 * - research.js 不再承担任何聊天深度研究相关状态，独立深度研究逻辑不受影响。
 *
 * 依赖说明：
 * - useSessionStore：restoreChatResearchContext 需从会话消息历史推断 researchTaskId；
 * - research store：研究任务数据与审批路由由 research store 承担
 *   （approval.js 经惰性动态 import 访问），本桥接层当前迁移来的方法均不直接
 *   触碰 research store 的数据，故不静态引入（避免未使用的死依赖）。
 */
export const useChatDeepResearchStore = defineStore('chatDeepResearch', () => {
  /** 深度研究任务对象（deep_research 事件数据） */
  const deepResearchTask = ref(null)
  /** 深度研究任务 ID（深度研究审批路由依赖此值） */
  const researchTaskId = ref(null)
  /** 研究上下文标识 { taskId, query }，发送首条消息后清空 */
  const researchContextInfo = ref(null)

  /**
   * 设置深度研究任务（聊天流式链路 setDeepResearchTask 回调）
   * 任务创建时立即写入 researchTaskId，确保后续审批能正确路由到研究审批 API
   * @param {Object} data - 深度研究任务数据
   */
  const setChatDeepResearchTask = (data) => {
    deepResearchTask.value = data
    if (data?.taskId) {
      researchTaskId.value = data.taskId
    }
  }

  /**
   * 设置聊天深度研究任务 ID（聊天流式链路 setResearchTaskId 回调 / 审批恢复）
   * @param {string} taskId - 深度研究任务 ID
   */
  const setChatResearchTaskId = (taskId) => {
    researchTaskId.value = taskId
  }

  /** 清空深度研究任务对象（发送新消息前调用，保留 researchTaskId 供审批使用） */
  const clearChatDeepResearchTask = () => {
    deepResearchTask.value = null
  }

  /** 清空研究上下文标签（发送首条消息后 / 用户手动取消研究上下文） */
  const clearChatResearchContext = () => {
    researchContextInfo.value = null
  }

  /**
   * 重置聊天深度研究全部状态（clearAll / 登出时调用）
   */
  const resetChatDeepResearch = () => {
    deepResearchTask.value = null
    researchTaskId.value = null
    researchContextInfo.value = null
  }

  /**
   * 按指定会话消息恢复聊天深度研究上下文（页面刷新 / 会话切换 / 会话删除后调用）
   * 从消息末尾向前找最后一条带 researchTaskId 的 assistant 消息；
   * 该会话无深度研究上下文时清空桥接状态（防止旧会话残留污染新会话）。
   * @param {string} sessionId - 聊天会话 ID
   */
  const restoreChatResearchContext = (sessionId) => {
    const sessionStore = useSessionStore()
    const session = sessionStore.sessions.find(s => s.id === sessionId)
    if (!session?.messages) return
    for (let i = session.messages.length - 1; i >= 0; i--) {
      const msg = session.messages[i]
      if (msg.role === 'assistant' && msg.researchTaskId) {
        researchTaskId.value = msg.researchTaskId
        researchContextInfo.value = { taskId: msg.researchTaskId, query: '' }
        logger.log('[ChatDeepResearch] 已恢复聊天深度研究 researchTaskId:', msg.researchTaskId)
        return
      }
    }
    // 当前会话无深度研究上下文：清空桥接状态。
    // 根因修复：删除深度研究会话 / 切换到普通会话后，残留的 researchTaskId 与
    // researchContextInfo 会污染代理模式（输入框闪现"🔬 深度研究"标签、请求参数
    // 泄漏 research_task_id 导致后端错误加载旧研究上下文）。
    // 按会话重算而非"有值即跳过"，确保切换/删除会话后残留被正确清除。
    if (researchTaskId.value || researchContextInfo.value) {
      researchTaskId.value = null
      researchContextInfo.value = null
      logger.log('[ChatDeepResearch] 会话无深度研究上下文，已清空桥接状态:', sessionId)
    }
  }

  /**
   * 深度研究审批的 researchTaskId 恢复与路由取值（原 chat.js _executeApproval 审批恢复逻辑）
   *
   * 深度研究审批执行时若 researchTaskId 丢失，依次尝试恢复：
   * 1. 从 deepResearchTask.taskId 恢复
   * 2. 从当前会话消息历史恢复（restoreChatResearchContext）
   *
   * @param {Object} [approval] - 审批数据对象（含 source 字段）
   * @returns {string|null} 当前有效的深度研究任务 ID
   */
  const getResearchTaskIdForApproval = (approval) => {
    // 深度研究审批：如果 researchTaskId 丢失，尝试恢复
    if (approval?.source === 'deep_research' && !researchTaskId.value) {
      if (deepResearchTask.value?.taskId) {
        setChatResearchTaskId(deepResearchTask.value.taskId)
        logger.warn('[ChatDeepResearch] 深度研究审批时 researchTaskId 为空，从 deepResearchTask 恢复:', researchTaskId.value)
      } else {
        restoreChatResearchContext(useSessionStore().currentSessionId)
        logger.warn('[ChatDeepResearch] 深度研究审批时 researchTaskId 为空，已尝试从消息历史恢复', researchTaskId.value)
      }
    }
    return researchTaskId.value
  }

  return {
    // 状态（自 research.js chatDeepResearch 子域迁移）
    deepResearchTask,
    researchTaskId,
    researchContextInfo,
    // 方法
    setChatDeepResearchTask,
    setChatResearchTaskId,
    clearChatDeepResearchTask,
    clearChatResearchContext,
    resetChatDeepResearch,
    restoreChatResearchContext,
    getResearchTaskIdForApproval,
  }
})
