import { logger } from '@/utils/logger'
import { scheduleSubagentsRefresh } from '@/composables/useSubagents'
import {
  findMessageById,
  getSession,
  getLastAssistantMessage,
} from './helpers'

/**
 * 创建子代理事件处理器（spec D10，session 频道）
 *
 * 装配方式：由 handleSessionEvent.js 工厂内直接创建（与 toolCallHandler 一致）。
 *
 * 处理 3 个 session 频道事件（原实现位于 handleSessionEvent.js 内联，
 * C5/cq-04 Task 2 下沉，行为保持）：
 * - stream_subagent_content：子代理图层正文/中间思考流式更新，按 subagentThreadId
 *   写入 message.subagentContents[threadId]
 * - subagent_status_change：子代理终态/挂起状态变更，触发子代理元数据刷新
 * - status_change：聊天关联深度研究任务状态实时推送（session 频道冗余路径，
 *   task 频道由 handleTaskEvent 处理）
 *
 * @param {Object} ctx - 依赖上下文
 * @param {Object} ctx.sessionStore - session store 实例
 * @param {Object} ctx.researchStore - research store 实例
 * @returns {{
 *   applySubagentContent: (sessionId: string, payload: Object) => void,
 *   handleSubagentStatusChange: (sessionId: string, payload: Object) => void,
 *   handleResearchStatusChange: (sessionId: string, payload: Object) => void,
 * }}
 */
export const createSubagentHandlers = (ctx) => {
  const { sessionStore, researchStore } = ctx

  /**
   * 应用子代理图层正文/中间思考事件（stream_subagent_content，session 频道）
   *
   * spec D10：
   * - 按 subagentThreadId 写入 message.subagentContents[threadId]（content/reasoningContent 追加累计）；
   * - 未知 threadId（chunk 先于 spawn 元数据到达）：动态初始化条目（时序竞争防御）；
   * - 子代理名称/深度从 payload.data 透传（用于摘要卡片展示）。
   *
   * @param {string} sessionId
   * @param {Object} payload - toCamelCase 后：{ source, sourceId, messageId, sessionId, subagentThreadId, data: { agentName, depth, content, reasoningContent } }
   */
  const applySubagentContent = (sessionId, payload) => {
    const data = payload.data || payload
    // subagentThreadId：子代理 SSE 定向推送路由标识符（handleSessionEvent 入口
    // 从事件顶层 subagent_thread_id 注入为 camelCase）。
    const subagentThreadId = payload.subagentThreadId || data.subagentThreadId || ''
    if (!subagentThreadId) {
      logger.debug(`[Sync] stream_subagent_content 缺少 subagentThreadId，丢弃: session=${sessionId}`)
      return
    }

    const session = getSession(sessionStore, sessionId)
    if (!session?.messages) {
      logger.debug(`[Sync] stream_subagent_content 会话未加载: session=${sessionId}`)
      return
    }
    let targetMsg = null
    if (payload.messageId) {
      targetMsg = findMessageById(session, payload.messageId)
    }
    if (!targetMsg) {
      targetMsg = getLastAssistantMessage(session)
    }
    if (!targetMsg) return

    const content = data.content || ''
    const reasoningContent = data.reasoningContent || ''
    if (!content && !reasoningContent) return

    // 动态初始化条目（chunk 先于 spawn 元数据到达的时序竞争防御）
    if (!targetMsg.subagentContents || typeof targetMsg.subagentContents !== 'object') {
      targetMsg.subagentContents = {}
    }
    // 幂等追加（根因修复）：从现有 entry 构建新对象，content/reasoningContent 各追加
    // 恰好一次后整体替换。message 与活跃版本按 D5 引用同步（下方收敛），禁止对版本
    // 二次追加——别名时二者为同一对象，二次追加会把同一 content 追加两次产生逐段双写。
    const entry = targetMsg.subagentContents[subagentThreadId] || { content: '', reasoningContent: '' }
    const nextEntry = { ...entry }
    if (content) nextEntry.content = (nextEntry.content || '') + content
    if (reasoningContent) nextEntry.reasoningContent = (nextEntry.reasoningContent || '') + reasoningContent
    if (data.agentName) nextEntry.agentName = data.agentName
    if (typeof data.depth === 'number') nextEntry.depth = data.depth
    targetMsg.subagentContents[subagentThreadId] = nextEntry

    // 活跃版本收敛（D5：message.subagentContents 恒等于活跃版本快照）：
    // 别名成立时跳过（message 写入即已同步）；别名被破坏时恢复引用，而非二次追加
    const ver = targetMsg.versions?.[targetMsg.currentVersion]
    if (ver && ver.subagentContents !== targetMsg.subagentContents) {
      ver.subagentContents = targetMsg.subagentContents
    }

    logger.debug(
      `[Sync] stream_subagent_content 应用: session=${sessionId}, message=${targetMsg.backendId || targetMsg.id}, ` +
      `threadId=${subagentThreadId}, contentLen=${content.length}, reasoningLen=${reasoningContent.length}`
    )
  }

  /**
   * 处理子代理状态变更事件（subagent_status_change，session 频道）
   *
   * 后端子代理终态/挂起时发布（langgraph_adapter._publish_status_event），
   * 前端据此刷新子代理元数据，否则子代理卡实时状态停留旧值
   * （如已完成仍显示"执行中"，刷新页面才正常——事件驱动的核心补位）。
   *
   * subagentThreadId 由 handleSessionEvent 入口从事件顶层 subagent_thread_id
   * 注入 payload（协议标识符还原）；原内联实现的 `|| event.subagent_thread_id`
   * 兜底在注入之后恒不可达（入口注入保证二者同时为真，见 handleSessionEvent
   * 的还原注入逻辑），属无效分支已删除。
   *
   * @param {string} sessionId
   * @param {Object} payload - toCamelCase 后：{ source, sourceId, subagentThreadId, data: { status } }
   */
  const handleSubagentStatusChange = (sessionId, payload) => {
    if (payload.subagentThreadId) {
      logger.info(
        `[Sync] 子代理状态变更: session=${sessionId}, subagent=${payload.subagentThreadId}, ` +
        `status=${payload.data?.status || payload.status || '(unknown)'}`
      )
      // 父线程 id 取 payload.sourceId（深研=task_id，chat/learning=session_id），
      // 与 toolCallHandler 的拉取口径一致（chat 关联深研子代理挂在 research task 下）
      scheduleSubagentsRefresh(payload.sourceId || sessionId)
    }
  }

  /**
   * 处理聊天关联深度研究任务状态变更（status_change，session 频道冗余路径）
   *
   * 聊天关联深度研究任务状态实时推送（session + task 双频道，Task 4）。
   * task 频道由 handleTaskEvent 处理；此处处理 session 频道冗余路径，
   * 确保 ChatView 打开时 DeepResearchView 的 taskInfo 也实时更新。
   * payload.source_id = task_id（DEEP_RESEARCH 来源）。
   *
   * @param {string} sessionId
   * @param {Object} payload - toCamelCase 后：{ sourceId, status, ... }
   */
  const handleResearchStatusChange = (sessionId, payload) => {
    if (payload.sourceId) {
      researchStore.setTaskStatus(payload.sourceId, payload)
      logger.info(
        `[Sync] session status_change(关联研究): taskId=${payload.sourceId}, ` +
        `status=${payload.status || 'unknown'}, session=${sessionId}`
      )
    }
  }

  return {
    applySubagentContent,
    handleSubagentStatusChange,
    handleResearchStatusChange,
  }
}
