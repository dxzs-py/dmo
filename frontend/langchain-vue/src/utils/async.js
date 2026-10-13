/**
 * 异步调度工具
 */

/**
 * requestAnimationFrame 节流：窗口内高频调用合并为每帧最多一次，
 * 且始终以最后一次调用的参数执行（尾帧语义，天然保证终态正确）。
 *
 * 典型场景：流式 Markdown 渲染——token 高频追加时避免每次变更都全量
 * marked + DOMPurify 重建 HTML（O(n²)），降为每帧一次（O(n × 帧率)）。
 *
 * @param {Function} fn - 需要节流的函数（参数透传最后一次调用的参数）
 * @returns {((...args: *) => void) & { cancel: () => void }} 节流后的函数，
 *   附带 cancel() 取消未执行的尾帧调用（组件卸载时防泄漏）
 */
export function rafThrottle(fn) {
  let rafId = null
  let pendingArgs = null

  const invoke = () => {
    rafId = null
    if (pendingArgs === null) return
    const args = pendingArgs
    pendingArgs = null
    fn(...args)
  }

  const throttled = (...args) => {
    pendingArgs = args
    if (rafId === null) {
      rafId = requestAnimationFrame(invoke)
    }
  }

  throttled.cancel = () => {
    if (rafId !== null) {
      cancelAnimationFrame(rafId)
      rafId = null
    }
    pendingArgs = null
  }

  return throttled
}
