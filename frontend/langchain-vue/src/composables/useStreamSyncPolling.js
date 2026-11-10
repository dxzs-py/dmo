import { logger } from '../utils/logger'

/**
 * 流式输出期间的消息同步轮询（D-9 收敛）。
 *
 * stores/chat.js 的 sendMessage 与 regenerateMessage 此前各自维护局部
 * streamSyncTimer（5s setInterval 将末条 assistant 消息 PATCH 到后端），
 * 两处逻辑完全同构，收敛至此统一启动/清理模式：
 * 调用方在流式发起后 start、finally 中 stop，计时器不跨调用泄漏。
 *
 * 用法（Pinia store action 内亦可安全使用，本 composable 不注册生命周期钩子，
 * 清理由调用方 finally 保证）：
 *   const { startStreamSyncPolling, stopStreamSyncPolling } = useStreamSyncPolling(
 *     () => sessionStore.syncLastMessageToBackend(sessionId)
 *   )
 *   try {
 *     startStreamSyncPolling()
 *     // ... 流式执行 ...
 *   } finally {
 *     stopStreamSyncPolling()
 *   }
 *
 * @param {() => Promise<any>|any} syncTask - 周期执行的同步任务（单次失败 logger.debug 留痕，不中断轮询）
 * @param {Object} [options]
 * @param {number} [options.intervalMs=5000] - 轮询间隔（毫秒）
 * @returns {{ startStreamSyncPolling: () => void, stopStreamSyncPolling: () => void }}
 */
export function useStreamSyncPolling(syncTask, { intervalMs = 5000 } = {}) {
  /** @type {ReturnType<typeof setInterval>|null} */
  let timer = null

  const stopStreamSyncPolling = () => {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  const startStreamSyncPolling = () => {
    stopStreamSyncPolling()
    timer = setInterval(() => {
      Promise.resolve(syncTask()).catch((error) => {
        logger.debug('[StreamSyncPolling] 同步任务执行失败（保留轮询）:', error)
      })
    }, intervalMs)
  }

  return { startStreamSyncPolling, stopStreamSyncPolling }
}
