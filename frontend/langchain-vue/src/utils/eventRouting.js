/**
 * 事件路由字段统一解析（P3-R1 Task 1）
 *
 * 背景：后端将 session_id / task_id 注入事件**顶层**（与 payload 平级，
 * 见 realtime_events.py _publish_to_session_async / _publish_to_task_async），
 * payload 内部通常不含这两个路由字段。历史实现只读 event.payload 导致
 * 路由字段解析为 undefined，所有 WebSocket 事件被静默丢弃（黑盒测试证实）。
 *
 * 约定：
 * - sessionId / taskId 为网络传输层路由标识符（后端注入事件顶层），
 *   解析优先级：payload 优先（兼容业务内嵌字段），event 顶层兜底（后端注入），null 最后。
 * - 仅收敛"路由字段"（sessionId / taskId）的解析；业务数据字段
 *   （payload 内的 toolCallId / messageId / sourceId 等）不在本模块职责范围。
 *
 * @typedef {import('@/composables/useRealtimeSync').RealtimeEvent} RealtimeEvent
 */

/**
 * 从事件中解析 sessionId（统一路由字段解析入口）
 *
 * 后端将 session_id 注入事件顶层（event.sessionId，与 payload 平级），
 * payload 内部通常不含 session_id；payload.sessionId 为业务内嵌字段兜底
 * （message_added / approval_* 等事件 schema 显式携带）。
 *
 * @param {RealtimeEvent} event - 实时事件对象（含顶层路由字段 + payload）
 * @returns {string | null} 会话 ID；payload 与事件顶层均缺失时返回 null
 */
export const getEventSessionId = (event) => {
  return event?.payload?.sessionId || event?.sessionId || null
}

/**
 * 从事件中解析 taskId（统一路由字段解析入口）
 *
 * 后端将 task_id 注入事件顶层（event.taskId，与 payload 平级），
 * payload 内部通常不含 task_id；payload.taskId 为业务内嵌字段兜底。
 *
 * @param {RealtimeEvent} event - 实时事件对象（含顶层路由字段 + payload）
 * @returns {string | null} 任务 ID；payload 与事件顶层均缺失时返回 null
 */
export const getEventTaskId = (event) => {
  return event?.payload?.taskId || event?.taskId || null
}
