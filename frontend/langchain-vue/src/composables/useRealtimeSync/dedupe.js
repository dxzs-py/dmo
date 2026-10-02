import { useDebounceFn } from '@vueuse/core'
import { logger } from '@/utils/logger'
import { LAST_SEQ_KEY_PREFIX } from './constants.js'

/**
 * seq 去重子系统：lastSeq 单一权威（channel + seq 单调）
 *
 * 职责（原 createRealtimeSync 内联逻辑逐行迁移）：
 * - 维护每个通道最近收到的 seq（key 为 'user' / 'session_<id>' / 'task_<id>'）
 * - advanceLastSeq 是事件级去重唯一权威的唯一写入入口（取 max 防回退 + debounce 持久化）
 * - localStorage 跨标签页取 max 合并写回，防陈旧基线互踩
 *
 * 依赖约定：跨模块依赖一律通过 ctx 在调用时访问（禁止创建期解构）；
 * 本模块仅依赖注入的 userStore，不静态 import 其他子系统。
 *
 * @param {Object} deps
 * @param {Object} deps.userStore - 用户 store（解析 localStorage key 的 userId）
 * @returns {{
 *   lastSeq: Map<string, number>,
 *   getLastSeqKey: () => string | null,
 *   loadLastSeq: () => void,
 *   persistLastSeq: () => void,
 *   advanceLastSeq: (channelKey: string, seq: number) => void,
 *   clearLastSeq: () => void,
 * }}
 */
export function createSeqDedupe({ userStore }) {
  /**
   * 每个通道最近收到的 seq：key 为 'user' 或 'session_<id>' 或 'task_<id>'
   *
   * Task 4：本 Map 是**事件级去重的唯一权威**（channel + seq 单调）：
   * - subscribe/replay 请求的 last_seq 起点由本 Map 决定（已处理事件不会被重复回放）
   * - dispatchEvent 在回调全部成功后推进本 Map（取 max 防并发回退）
   * - sync.js 的 advanceBaseline（有序队列丢弃联动）也会推进本 Map，保持基线收敛
   * 其他 seq 跟踪器（seqDedup 仅做跳号检测、orderedQueue 仅排序/间隙等待）均不得
   * 独立承担"事件已见"去重职责。
   *
   * @type {Map<string, number>}
   */
  const lastSeq = new Map()

  /**
   * 获取当前用户的 lastSeq localStorage key
   * @returns {string | null}
   */
  const getLastSeqKey = () => {
    const userId = userStore.userInfo?.id
    return userId ? `${LAST_SEQ_KEY_PREFIX}:${userId}` : null
  }

  /**
   * 从 localStorage 恢复 lastSeq
   */
  const loadLastSeq = () => {
    const key = getLastSeqKey()
    if (!key) return
    try {
      const raw = localStorage.getItem(key)
      if (!raw) return
      const entries = JSON.parse(raw)
      if (Array.isArray(entries)) {
        entries.forEach(([k, v]) => {
          if (typeof v === 'number') {
            lastSeq.set(k, v)
          }
        })
        logger.info('[Realtime] 已从 localStorage 恢复 lastSeq:', entries.length, '条通道')
      }
    } catch (error) {
      logger.error('[Realtime] 解析 lastSeq 失败:', error)
    }
  }

  /**
   * 将 lastSeq 持久化到 localStorage
   *
   * Task 4 跨标签页防互踩：写入前读取旧值，逐 channel 取最大值后合并写回。
   * 多标签页（同一用户）各自维护 lastSeq Map，直接覆盖会令较新标签页的
   * 已处理 seq 基线被较旧标签页覆盖（陈旧 lastSeq 使重连 replay 重复投递已处理事件）。
   * 取 max 合并保证各标签页写入单调不减。
   */
  const persistLastSeq = () => {
    const key = getLastSeqKey()
    if (!key) return
    try {
      // 读取旧值，构建合并基线（旧值损坏时忽略，用当前值覆盖）
      /** @type {Map<string, number>} */
      const merged = new Map()
      const raw = localStorage.getItem(key)
      if (raw) {
        try {
          const entries = JSON.parse(raw)
          if (Array.isArray(entries)) {
            entries.forEach(([k, v]) => {
              if (typeof v === 'number') merged.set(k, v)
            })
          }
        } catch {
          logger.warn('[Realtime] 解析 localStorage lastSeq 旧值失败，以当前值覆盖')
        }
      }
      // 当前内存值取 max 合并
      for (const [k, v] of lastSeq.entries()) {
        const prev = merged.get(k) || 0
        if (v > prev) merged.set(k, v)
      }
      localStorage.setItem(key, JSON.stringify(Array.from(merged.entries())))
    } catch (error) {
      logger.error('[Realtime] 保存 lastSeq 失败:', error)
    }
  }

  // rd-08：防抖统一 @vueuse useDebounceFn（trailing 语义与原内联 debounce 等价）
  const saveLastSeqDebounced = useDebounceFn(persistLastSeq, 200)

  /**
   * 推进指定通道的 lastSeq（事件级去重单一权威的唯一写入入口）
   *
   * Task 4：
   * - dispatchEvent（回调全部成功后）与 sync.js 的 advanceBaseline（有序队列丢弃联动）
   *   均通过本方法推进 lastSeq，保证多路径基线收敛，避免双基线发散。
   * - 取 max 防回退：多个 dispatchEvent 并发时（浏览器不 await onMessage 的 Promise），
   *   回调完成顺序可能与事件到达顺序不一致，直接 set 会导致 lastSeq 回退，
   *   重连时 replay 从更早 seq 开始，重复处理已处理事件。
   *
   * @param {string} channelKey - 'user' / 'session_<id>' / 'task_<id>'
   * @param {number} seq
   */
  const advanceLastSeq = (channelKey, seq) => {
    if (!channelKey || typeof seq !== 'number') return
    const current = lastSeq.get(channelKey) || 0
    if (seq > current) {
      lastSeq.set(channelKey, seq)
      saveLastSeqDebounced()
    }
  }

  /**
   * 清除当前用户的 lastSeq（登出或切换用户时）
   */
  const clearLastSeq = () => {
    const key = getLastSeqKey()
    if (key) {
      localStorage.removeItem(key)
    }
    lastSeq.clear()
  }

  return {
    lastSeq,
    getLastSeqKey,
    loadLastSeq,
    persistLastSeq,
    advanceLastSeq,
    clearLastSeq,
  }
}
