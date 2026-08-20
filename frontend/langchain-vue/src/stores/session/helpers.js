/**
 * session store 切片共享常量与纯工具函数（拆分自原 stores/session.js，C5/cq-04 Task 3）
 *
 * 仅存放被多个切片共用的 localStorage key 常量、安全读写函数与 key 构造函数，
 * 不持有任何响应式状态。
 */

/** localStorage key：持久化 currentSessionId，防止刷新后丢失（Task 15 P0 修复） */
export const CURRENT_SESSION_ID_KEY = 'lc_current_session_id'

/** localStorage key 前缀：持久化未固化选中版本索引（刷新/关闭兜底，Task 6.6） */
export const VERSION_SELECTION_KEY_PREFIX = 'lc_msg_version_'

/** 版本固化超时：5 分钟无有效操作（切换版本/点击重新生成）触发固化 */
export const FINALIZE_TIMEOUT_MS = 5 * 60 * 1000

/** 安全读取 localStorage（Node 测试环境可能不存在） */
export const _safeGetLocalStorage = (key) => {
  try { return typeof localStorage !== 'undefined' ? localStorage.getItem(key) : null }
  catch { return null }
}
/** 安全写入 localStorage */
export const _safeSetLocalStorage = (key, value) => {
  try { if (typeof localStorage !== 'undefined') localStorage.setItem(key, value) }
  catch { /* 静默忽略 */ }
}
/** 安全移除 localStorage */
export const _safeRemoveLocalStorage = (key) => {
  try { if (typeof localStorage !== 'undefined') localStorage.removeItem(key) }
  catch { /* 静默忽略 */ }
}

/** 未固化版本选择持久化 key 构造（versionFlow 持久化/恢复 + messageFlow 链式删除清理共用） */
export const _versionSelectionKey = (sessionId, messageId) =>
  `${VERSION_SELECTION_KEY_PREFIX}${sessionId}_${messageId}`
