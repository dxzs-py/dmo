import { ref } from 'vue'
import { readSSEStream } from '@/utils/sse'
import { extractSSEError } from '@/utils/apiErrorHandler'

/**
 * SSE 流式消费共享骨架 composable
 *
 * 抽取四处 SSE 消费（DeepResearchView / RagView / useWorkflowExecution /
 * useResearchApprovalListener）逐段同构的骨架：
 *   AbortController 生命周期 + 活跃标志位 + !response.ok 的 extractSSEError
 *   错误提取 + readSSEStream 读流循环（含活跃守卫）+ finally 清理。
 *
 * 场景差异策略不强行统一，留在调用方：
 *   - 重试/回退（轮询回退、指数退避重连）属于各场景策略，start 以返回值
 *     暴露终止原因（status / active / error），由调用方自行决策；
 *   - requestFn 按 start 调用注入（同一 composable 实例可复用于多个请求，
 *     如工作流的启动/继续/监听三种 requestFn），由调用方决定是否把 signal
 *     传给 fetch（workflow raw API 因 Vite proxy 缓冲问题不传 signal）。
 *
 * 实例安全：controller / isActive 为调用闭包内私有，每次 start 创建全新
 * AbortController；旧一轮 start 收尾时仅在自身仍是当前连接时才复位标志，
 * 避免「abort 旧流 → 同步 start 新流」竞态下旧流清理覆盖新流状态。
 *
 * @param {object} options
 * @param {(event: object) => void} options.onEvent - SSE 事件回调（事件已由
 *   readSSEStream / parseProtocolEvent 统一完成 camelCase 转换 + type 还原），
 *   仅在连接活跃期间派发（abort 后到达的同批事件被守卫丢弃）
 * @param {boolean} [options.resolveOnBadResponse=false] - true 时 !response.ok
 *   不经 extractSSEError 抛错，而是按自然结束（completed）返回。用于审批监听
 *   场景：连接非 2xx 时静默退出，不触发调用方重连
 * @returns {{
 *   start: (requestFn: (signal: AbortSignal) => Promise<Response>) =>
 *     Promise<{status: 'completed'|'aborted'|'error', error: Error|null, active: boolean}>,
 *   abort: () => void,
 *   isActive: import('vue').Ref<boolean>,
 * }}
 */
export function useSSEStream({ onEvent, resolveOnBadResponse = false } = {}) {
  /** 活跃标志：连接建立至收尾之间为 true，兼作事件派发守卫 */
  const isActive = ref(false)

  /** @type {AbortController | null} 当前连接的控制器 */
  let currentController = null

  /**
   * 建立连接并消费 SSE 流直至终止
   *
   * @param {(signal: AbortSignal) => Promise<Response>} requestFn 发起 SSE 请求的
   *   函数；signal 由本 composable 创建并注入，是否透传给 fetch 由调用方决定
   * @returns {Promise<{status: 'completed'|'aborted'|'error', error: Error|null, active: boolean}>}
   *   - completed：读流循环自然结束（含 resolveOnBadResponse 的非 2xx 情形），
   *     active 指示结束时连接是否仍活跃（false = 流期间被 abort 主动关闭）
   *   - aborted：以 AbortError 终止（主动 abort 或请求被中断）
   *   - error：其他异常（网络错误 / extractSSEError 提取的服务端错误）
   */
  const start = async (requestFn) => {
    const controller = new AbortController()
    currentController = controller
    isActive.value = true

    try {
      const response = await requestFn(controller.signal)

      if (!response.ok) {
        if (resolveOnBadResponse) {
          return { status: 'completed', error: null, active: true }
        }
        // rd-06：错误响应解析统一到 apiErrorHandler.extractSSEError
        throw await extractSSEError(response)
      }

      await readSSEStream(response, (event) => {
        if (!isActive.value) return
        onEvent(event)
      }, controller.signal)

      // active 在 finally 复位前读取：自然结束且未被主动关闭时为 true
      return { status: 'completed', error: null, active: isActive.value }
    } catch (error) {
      if (error.name === 'AbortError') {
        return { status: 'aborted', error, active: false }
      }
      return { status: 'error', error, active: false }
    } finally {
      // 仅当本次连接仍是当前连接时才复位：abort 旧流后同步 start 新流的
      // 竞态下，旧流收尾不得关闭新流的活跃标志
      if (currentController === controller) {
        isActive.value = false
        currentController = null
      }
    }
  }

  /**
   * 主动断开当前连接（等价原各调用方 closeSSE/stopStreaming 的骨架部分）：
   * 复位活跃标志（立即生效于事件守卫）并中断读流循环
   */
  const abort = () => {
    isActive.value = false
    if (currentController) {
      currentController.abort()
      currentController = null
    }
  }

  return { start, abort, isActive }
}
