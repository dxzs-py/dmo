/**
 * 复制文本到剪贴板（带回退的统一权威实现）。
 *
 * 语义：
 * 1. 安全上下文（window.isSecureContext）且 Clipboard API 可用时，优先 navigator.clipboard.writeText；
 * 2. 非安全上下文（HTTP/file）、API 缺失，或 writeText 失败（权限被拒、文档失焦等）时，
 *    回退到临时 textarea + document.execCommand('copy')（回退路径对用户同样可复制成功）。
 *
 * 边界：
 * - 返回 Promise<boolean> 表示复制是否成功；任何异常都在内部捕获并返回 false，绝不向调用方抛出。
 * - 成功/失败的用户提示（消息、图标、状态变化）属调用方 UI 语义，本函数不做任何提示，由调用方自行处理。
 *
 * @param {string} text 要复制的文本
 * @returns {Promise<boolean>} 复制是否成功
 */
export async function copyToClipboard(text) {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // Clipboard API 失败 → 尝试 execCommand 回退
    }
  }
  return copyViaExecCommand(text)
}

/**
 * execCommand 回退：写入临时 textarea 后执行 document.execCommand('copy')，
 * 是非安全上下文（HTTP/file）下唯一可用的复制路径。
 *
 * @param {string} text 要复制的文本
 * @returns {boolean} 是否成功（execCommand 返回 false 或抛异常均为失败）
 */
function copyViaExecCommand(text) {
  let textarea = null
  try {
    textarea = document.createElement('textarea')
    textarea.value = text
    // readonly 防止移动端弹出软键盘/选择器；fixed + opacity 0 避免布局闪烁
    textarea.setAttribute('readonly', '')
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    return document.execCommand('copy') === true
  } catch {
    return false
  } finally {
    if (textarea && textarea.parentNode) {
      textarea.parentNode.removeChild(textarea)
    }
  }
}
