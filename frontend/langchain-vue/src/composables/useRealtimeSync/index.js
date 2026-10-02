import { watch } from 'vue'
import { useUserStore } from '@/stores/user'
import { logger } from '@/utils/logger'
import { RealtimeConnectionStatus } from '@/types/realtimeEvents'
import { createSeqDedupe } from './dedupe.js'
import { createReplay } from './replay.js'
import { createDispatcher } from './dispatcher.js'
import { createSubscribers } from './subscribers.js'
import { createConnection } from './connection.js'
import { createReconnect } from './reconnect.js'

/**
 * @typedef {import('@/types/realtimeEvents').RealtimeEvent} RealtimeEvent
 */

/**
 * @typedef {import('@/types/realtimeEvents').RealtimeEventCallback} RealtimeEventCallback
 */

/**
 * 实时同步管理器（模块级单例）
 *
 * 原单文件 useRealtimeSync.js（1200 行单例闭包耦合六子系统）按子系统边界拆分：
 *   - index.js（本文件）：编排器——组装上下文、注册 watch / visibilitychange、维持单例与对外 API
 *   - constants.js：模块级常量
 *   - dedupe.js：seq 去重（lastSeq 单一权威：推进 / 持久化 / 恢复 / 清理）
 *   - replay.js：快照回放 + Replay Barrier（缓冲实时事件，防 lastSeq 污染）
 *   - dispatcher.js：事件路由分发（onMessage 入口 + 双阶段 dispatchEvent + 快照校对触发）
 *   - subscribers.js：消费方订阅注册（user/session/task 三通道 + LRU 淘汰）
 *   - connection.js：WS 建连 / 鉴权 / 生命周期 / 原始发送 / 连接状态
 *   - reconnect.js：重连退避 + 心跳
 *
 * 单例语义保持：createRealtimeSync 仅在首次 useRealtimeSync() 调用时执行一次；
 * visibilitychange 监听永不移除为已知规范缺口（单例常驻语义），保持现状。
 *
 * 依赖约定：
 *   - 各子系统工厂接收共享上下文 ctx，跨模块依赖一律 `ctx.<member>` 调用时访问
 *     （禁止工厂创建期解构，避免创建顺序耦合）
 *   - 工厂之间零静态 import，依赖图为 index.js → 各工厂的单向星型结构，无循环 import
 */

/** @type {ReturnType<typeof createRealtimeSync> | null} */
let instance = null

/**
 * 组装实时同步管理器（仅执行一次，见模块级单例 instance）
 * @returns {ReturnType<typeof createRealtimeSync>}
 */
function createRealtimeSync() {
  const userStore = useUserStore()

  /**
   * 跨子系统共享上下文（原单例闭包局部变量的显式化）
   *
   * - 共享可变原始值（ws / intentionalClose 等）以 ctx 对象属性承载，
   *   各子系统通过 ctx 读写，等价于原闭包对局部变量的共享
   * - 各子系统私有状态（定时器、回调注册表、lastSeq Map 等）仍归属各自工厂
   * @type {any}
   */
  const ctx = {
    userStore,
    /** @type {WebSocket | null} 当前 WebSocket 连接句柄（connection.js 读写，订阅/重连路径只读） */
    ws: null,
    /** 是否为主动断开（disconnect 置 true / connect 置 false；scheduleReconnect 读取） */
    intentionalClose: false,
    /** 当前重连尝试次数（reconnect.js 递增；onConnectionOpen / disconnect / 页面恢复时归零） */
    reconnectAttempt: 0,
    /** 是否已达到最大重连次数（用于 UI 显示"连接断开"提示） */
    exhaustedReconnect: false,
  }

  // 装配六子系统（工厂仅定义闭包不执行；watch 注册前 ctx 成员全部就位）
  Object.assign(ctx, createSeqDedupe({ userStore }))
  Object.assign(ctx, createReplay(ctx))
  Object.assign(ctx, createDispatcher(ctx))
  Object.assign(ctx, createSubscribers(ctx))
  Object.assign(ctx, createConnection(ctx))
  Object.assign(ctx, createReconnect(ctx))

  // 初始化时立即恢复 lastSeq（原时序：早于 watch 注册）
  ctx.loadLastSeq()

  // 监听登录状态，自动连接/断开
  watch(
    () => userStore.isLoggedIn,
    (loggedIn) => {
      if (loggedIn) {
        ctx.connect()
      } else {
        ctx.clearLastSeq()
        ctx.disconnect()
      }
    },
    { immediate: true }
  )

  // token 变更时（如刷新）主动重连以使用新 token
  // 注意：登录时 isLoggedIn 和 token 同时变化，isLoggedIn watch 已触发 connect，
  // 此处仅在已有连接时才 disconnect+reconnect，避免重复连接
  watch(
    () => userStore.token,
    (newToken, oldToken) => {
      if (newToken && newToken !== oldToken && userStore.isLoggedIn) {
        if (ctx.ws) {
          ctx.disconnect()
        }
        ctx.connect()
      }
    }
  )

  // 用户切换时清理/恢复 lastSeq
  watch(
    () => userStore.userInfo?.id,
    (newUserId, oldUserId) => {
      if (newUserId && oldUserId && newUserId !== oldUserId) {
        logger.info(`[Realtime] 检测到用户切换: ${oldUserId} -> ${newUserId}，重置 lastSeq`)
        ctx.clearLastSeq()
        ctx.connect()
      } else if (newUserId && !oldUserId) {
        ctx.loadLastSeq()
      }
    }
  )

  // 页面从隐藏切回可见时，若已断线则尝试重连
  // 重置 exhaustedReconnect 标志，给用户手动恢复的机会
  const handleVisibilityChange = () => {
    if (document.visibilityState === 'visible' &&
      ctx.connectionStatus.value === RealtimeConnectionStatus.DISCONNECTED &&
      userStore.isLoggedIn) {
      logger.info('[Realtime] 页面重新可见，重置重连计数并尝试恢复 WebSocket 连接')
      ctx.exhaustedReconnect = false
      ctx.reconnectAttempt = 0
      ctx.connect()
    }
  }
  document.addEventListener('visibilitychange', handleVisibilityChange)

  return {
    connectionStatus: ctx.connectionStatus,
    streamingActiveCount: ctx.streamingActiveCount,
    lastSeq: ctx.lastSeq,
    advanceLastSeq: ctx.advanceLastSeq,
    subscribeUserEvents: ctx.subscribeUserEvents,
    subscribeSession: ctx.subscribeSession,
    unsubscribeSession: ctx.unsubscribeSession,
    subscribeTask: ctx.subscribeTask,
    unsubscribeTask: ctx.unsubscribeTask,
    incrementStreaming: ctx.incrementStreaming,
    decrementStreaming: ctx.decrementStreaming,
    connect: ctx.connect,
    disconnect: ctx.disconnect,
  }
}

/**
 * 获取全局唯一的实时同步管理器
 * @returns {ReturnType<typeof createRealtimeSync>}
 */
export function useRealtimeSync() {
  if (!instance) {
    instance = createRealtimeSync()
  }
  return instance
}
