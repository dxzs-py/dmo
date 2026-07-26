import { ref, onScopeDispose, getCurrentScope } from 'vue'
import { fetchSSE, readSSEStream } from '@/utils/sse'
import { logger } from '@/utils/logger'

/**
 * SSE 连接状态枚举
 * @readonly
 * @enum {string}
 */
const ConnectionStatus = {
  IDLE: 'idle',
  CONNECTING: 'connecting',
  CONNECTED: 'connected',
  DISCONNECTED: 'disconnected',
  ERROR: 'error',
}

/**
 * 全局 SSE 连接注册表（模块级单例）
 * @type {Map<string, { abortController: AbortController, status: import('vue').Ref<string>, url: string, createdAt: number, ownerId: string|null }>}
 */
const connectionRegistry = new Map()

/** 全局活跃连接计数 */
const activeConnectionCount = ref(0)

let connectionIdCounter = 0

/**
 * SSE 连接生命周期管理 composable
 * @param {string} [ownerId] - 连接所有者标识（如组件名或会话ID），用于批量清理
 */
export function useSSEConnection(ownerId = null) {
  const connections = ref(new Set()) // 当前 composable 持有的连接 ID 集合

  /**
   * 创建并注册一个 SSE 连接
   * @param {string} url - SSE 端点 URL
   * @param {Object} [options]
   * @param {string} [options.connectionId] - 自定义连接 ID（不提供则自动生成）
   * @param {Function} [options.onEvent] - SSE 事件回调
   * @param {Function} [options.onStatusChange] - 连接状态变更回调
   * @param {Function} [options.onIdleTimeout] - 空闲超时回调
   * @param {number} [options.idleTimeout] - 空闲超时时间(ms)
   * @param {Object} [options.reconnectPolicy] - 重连策略
   * @param {Object} [options.fetchOptions] - 传递给 fetchSSE 的额外选项
   * @returns {Promise<{ connectionId: string, status: import('vue').Ref<string> }>}
   */
  const connect = async (url, options = {}) => {
    const id = options.connectionId || `sse-${++connectionIdCounter}`

    // 如果已有同名连接，先断开
    if (connectionRegistry.has(id)) {
      logger.info(`[SSEConnection] 连接 ${id} 已存在，先断开旧连接`)
      disconnect(id)
    }

    const status = ref(ConnectionStatus.IDLE)
    const abortController = new AbortController()

    // 注册连接
    connectionRegistry.set(id, {
      abortController,
      status,
      url,
      createdAt: Date.now(),
      ownerId,
    })
    connections.value.add(id)
    activeConnectionCount.value = connectionRegistry.size

    try {
      status.value = ConnectionStatus.CONNECTING
      options.onStatusChange?.(ConnectionStatus.CONNECTING)
      logger.info(`[SSEConnection] 开始连接: id=${id}, url=${url}`)

      const fetchOpts = {
        ...options.fetchOptions,
        signal: abortController.signal,
        onStatusChange: (state, ...args) => {
          if (state === 'connected') {
            status.value = ConnectionStatus.CONNECTED
          } else if (state === 'error') {
            status.value = ConnectionStatus.ERROR
          }
          options.onStatusChange?.(state, ...args)
        },
      }

      const response = await fetchSSE(url, fetchOpts)

      // 注意：connected 状态已由 fetchSSE 的 onStatusChange 回调设置，此处不再重复

      // 读取 SSE 流
      await readSSEStream(
        response,
        (parsed) => {
          if (abortController.signal.aborted) return
          return options.onEvent?.(parsed)
        },
        abortController.signal,
        {
          idleTimeout: options.idleTimeout,
          onIdleTimeout: () => {
            options.onIdleTimeout?.()
          },
        }
      )

      // 流正常结束
      status.value = ConnectionStatus.DISCONNECTED
      options.onStatusChange?.(ConnectionStatus.DISCONNECTED)
      logger.info(`[SSEConnection] 流正常结束: id=${id}`)
    } catch (e) {
      if (e.name === 'AbortError') {
        status.value = ConnectionStatus.DISCONNECTED
        options.onStatusChange?.(ConnectionStatus.DISCONNECTED)
        logger.info(`[SSEConnection] 连接被主动中断(AbortError): id=${id}`)
      } else {
        status.value = ConnectionStatus.ERROR
        options.onStatusChange?.(ConnectionStatus.ERROR, e)
        logger.error(`[SSEConnection] 连接 ${id} 错误:`, e)
      }
    } finally {
      // 从注册表中移除
      connectionRegistry.delete(id)
      connections.value.delete(id)
      activeConnectionCount.value = connectionRegistry.size
    }

    return { connectionId: id, status }
  }

  /**
   * 断开指定连接
   * @param {string} id - 连接 ID
   */
  const disconnect = (id) => {
    const conn = connectionRegistry.get(id)
    if (conn) {
      conn.abortController.abort()
      conn.status.value = ConnectionStatus.DISCONNECTED
      connectionRegistry.delete(id)
      connections.value.delete(id)
      activeConnectionCount.value = connectionRegistry.size
    }
  }

  /**
   * 断开当前 composable 持有的所有连接
   */
  const disconnectAll = () => {
    for (const id of connections.value) {
      disconnect(id)
    }
  }

  /**
   * 断开指定所有者的所有连接
   * @param {string} owner - 所有者标识
   */
  const disconnectByOwner = (owner) => {
    for (const [id, conn] of connectionRegistry.entries()) {
      if (conn.ownerId === owner) {
        disconnect(id)
      }
    }
  }

  /**
   * 获取指定连接的状态
   * @param {string} id - 连接 ID
   * @returns {string|null}
   */
  const getStatus = (id) => {
    return connectionRegistry.get(id)?.status.value ?? null
  }

  /**
   * 获取所有活跃连接信息
   * @returns {Array<{id: string, url: string, status: string, createdAt: number, ownerId: string|null}>}
   */
  const getAllConnections = () => {
    return Array.from(connectionRegistry.entries()).map(([id, conn]) => ({
      id,
      url: conn.url,
      status: conn.status.value,
      createdAt: conn.createdAt,
      ownerId: conn.ownerId,
    }))
  }

  // 组件卸载时自动断开关联连接
  if (getCurrentScope()) {
    onScopeDispose(() => {
      disconnectAll()
    })
  }

  return {
    connect,
    disconnect,
    disconnectAll,
    disconnectByOwner,
    getStatus,
    getAllConnections,
    activeConnectionCount,
    ConnectionStatus,
  }
}
