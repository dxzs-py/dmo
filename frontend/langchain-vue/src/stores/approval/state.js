import { ref } from 'vue'

/**
 * approval store 状态定义（ARCH-13 拆分：状态权威切片）
 *
 * 所有状态在 defineStore setup 内经 createApprovalState() 创建，
 * 保证每个 store 实例（每个 pinia 实例）独立持有，无模块级共享可变状态。
 */

/**
 * 创建 approval store 状态切片
 *
 * @returns {{
 *   pendingApprovals: import('vue').Ref<Map<string, Object>>,
 *   pendingBindQueue: import('vue').Ref<Array<Object>>,
 *   _timeoutResumeKeys: Set<string>,
 *   _approvalQueues: Map<string, Promise>,
 *   _approvalSyncTimers: Object<string, number>,
 *   cleanupTimerBox: { value: number|null },
 * }}
 */
export const createApprovalState = () => {
  /**
   * 统一待审批 Map
   * key: interruptId
   * value: { source: 'chat'|'deep_research', taskId?, sessionId?, approvalData,
   *          createdAt, expiresAt?（后端 payload 真实超时点 epoch ms，FE-6） }
   */
  const pendingApprovals = ref(new Map())

  /**
   * 待绑定审批队列：审批事件到达时 toolCall 可能尚未创建（LLM 流式输出渐进），
   * 暂存到队列，由 tool_call_* 系列事件触发重绑定
   * @type {import('vue').Ref<Array<{sessionId: string, toolCallId: string, approvalData: Object, createdAt: number}>>}
   */
  const pendingBindQueue = ref([])

  /**
   * 已触发超时恢复的去重键集合（Task 5 事件驱动化）
   *
   * key: `${sessionId}:${graphInterruptId}`。
   * 仅在后端已实际开始恢复（返回 SSE 流 / resumed）时标记；
   * waiting（批次未决断）不标记，等待后续超时事件再次触发；
   * 失败不标记，允许重试。
   * @type {Set<string>}
   */
  const _timeoutResumeKeys = new Set()

  /**
   * 审批串行执行队列（按 sessionId 隔离）
   *
   * 同一会话的审批恢复流必须串行执行，避免多个 SSE 流并发写入同一会话
   * 的最后一条消息（content/toolCalls 竞态）。不同会话可并行。
   * deep_research 独立模式（无 sessionId）直接执行不入队。
   *
   * 结构：Map<sessionId, Promise>
   * @type {Map<string, Promise>}
   */
  const _approvalQueues = new Map()

  /** debounced 同步到后端的定时器表（key: sessionId，仅 eventHandlers 使用） */
  const _approvalSyncTimers = {}

  /** 自动清理定时器句柄容器（setup 内 let 无法跨模块共享，改用 box 传引用） */
  const cleanupTimerBox = { value: null }

  return {
    pendingApprovals,
    pendingBindQueue,
    _timeoutResumeKeys,
    _approvalQueues,
    _approvalSyncTimers,
    cleanupTimerBox,
  }
}

/**
 * 惰性加载 research store（统一审批中心对 research 模块的依赖解耦）
 *
 * Task 9 / spec Change 5：approval 不静态 import research，避免与业务模块
 * 形成模块加载期依赖；仅在审批执行/事件处理（async 上下文）实际需要更新
 * 研究任务工具审批状态时动态 import 并缓存模块 Promise，实例按当前 pinia 解析。
 * 本函数只应在 async 上下文调用。
 */
/** @type {Promise<Object> | null} */
let _researchStoreModulePromise = null
export const getResearchStore = async () => {
  if (!_researchStoreModulePromise) {
    _researchStoreModulePromise = import('@/stores/research')
  }
  const mod = await _researchStoreModulePromise
  return mod.useResearchStore()
}
