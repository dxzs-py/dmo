/**
 * useRealtimeSync 模块级常量
 *
 * 从原单文件 useRealtimeSync.js 拆出，仅承载常量，无任何依赖。
 */

/** WebSocket 路径（与 API 同源，通过 vite 代理 /ws 转发） */
export const WS_PATH = '/ws/realtime/'

/** 心跳间隔（ms） */
export const PING_INTERVAL_MS = 15000

// 指数退避重连参数：1s → 2s → 4s → 8s → 10s（max 5 次）
export const RECONNECT_BASE_MS = 1000
export const RECONNECT_MAX_MS = 10000
export const RECONNECT_MAX_ATTEMPTS = 5

/** lastSeq localStorage key 前缀（按用户隔离：`${LAST_SEQ_KEY_PREFIX}:${userId}`） */
export const LAST_SEQ_KEY_PREFIX = 'realtime:last_seq'

// disconnected 状态显示防抖（ms）：网络抖动时延迟显示，避免频繁闪烁
export const DISCONNECT_DEBOUNCE_MS = 500

// 订阅上限：防止重连时 N 个已订阅 session 触发 N 次 subscribe + replay 请求风暴
// 超过上限时按 LRU 淘汰最旧的订阅（用户极少同时关注超过 10 个会话）
export const MAX_SUBSCRIBED_SESSIONS = 10
export const MAX_SUBSCRIBED_TASKS = 10

// Replay barrier 安全超时：10s 内 replay 未到达则强制 flush
export const REPLAY_BARRIER_TIMEOUT_MS = 10000
