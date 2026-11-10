/**
 * 附件处理进度提示辅助函数
 *
 * 背景：附件处理进度是「瞬时、非权威、只影响 UI 文案」的本地状态
 * （chatStore.attachmentProcessing = { stage, message } | null），
 * 由 sendMessage 设置、流结束 finally 清理。真实耗时发生在 Agent
 * 首次调用 attachment_search 懒建检索索引时（后端 _ensure_index，
 * 同步阻塞），该阶段经既有 tool_call_* 事件天然到达前端，无需新增
 * 后端事件——据此从 message.toolCalls 派生「正在建立索引」文案。
 *
 * 设计约束（spec: 反伪百分比）：阶段不可量化，不提供 progress 数值，
 * 只提供固定阶段文案（参照 researchProgressNoFakePercentage.test.js）。
 */

import { isTerminalStatus } from './toolCallStateMachine'

/** 会话附件检索工具的后端注册名（attachment_rag.py AttachmentRagSearchTool.name） */
export const ATTACHMENT_SEARCH_TOOL_NAME = 'attachment_search'

/** preparing 阶段默认文案（sendMessage 设置附件进度时的初始 stage） */
export const ATTACHMENT_STAGE_PREPARING = 'preparing'

/**
 * 是否存在仍在执行中的会话附件检索工具调用
 *
 * 终态判定复用 toolCallStateMachine 的唯一权威集合
 * （completed / failed / timeout / rejected 均视为已结束）。
 *
 * @param {Array<{toolName?: string, name?: string, status?: string}>|undefined} toolCalls
 * @returns {boolean}
 */
export function hasRunningAttachmentSearch(toolCalls) {
  if (!Array.isArray(toolCalls)) return false
  return toolCalls.some(
    tc =>
      tc &&
      (tc.toolName === ATTACHMENT_SEARCH_TOOL_NAME || tc.name === ATTACHMENT_SEARCH_TOOL_NAME) &&
      !isTerminalStatus(tc.status),
  )
}

/**
 * 解析附件处理提示条文案
 *
 * 优先级：
 * 1. 存在执行中的 attachment_search → 索引建立文案（真实耗时窗口）
 * 2. 否则回退 attachmentProcessing.message（preparing 阶段）
 * 3. 无 message 时使用通用兜底文案
 *
 * @param {Array<{toolName?: string, name?: string, status?: string}>|undefined} toolCalls
 * @param {string|undefined} fallbackMessage attachmentProcessing.message
 * @returns {string}
 */
export function resolveAttachmentProcessingText(toolCalls, fallbackMessage) {
  if (hasRunningAttachmentSearch(toolCalls)) {
    return '正在建立附件检索索引...'
  }
  return fallbackMessage || '正在处理附件...'
}
