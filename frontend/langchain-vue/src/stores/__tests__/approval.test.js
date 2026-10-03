/**
 * approval store REST 路径单元测试（ARCH-44 拆分前保护网）
 *
 * 被测：src/stores/approval/（index.js 装配器 + 职责切片，ARCH-13 拆分）
 *       —— 审批唯一状态权威（REST 侧行为）。
 * mock 策略：跟随现有范例（chatSandbox.test.js / approvalHandler.test.js），
 * 在 api 层 mock '@/api/approval' 的 resumeApprovalStream（不 mock axios 网络层）；
 * session / research / sync store 以稳定纯对象 + vi.fn() mock（useXxxStore() 每次
 * 返回同一实例，便于断言与逐用例改写 currentSessionId / sessions）。
 *
 * 覆盖分支：
 * 1. executeApproval：approve/reject 请求体与 REST 调用、pending→processing→（resumed/
 *    rejected）状态迁移、confirm_with_input 的 userInput 透传、独立深研 taskId 路由、
 *    waiting_for_others / idempotent 标记错误跳过终态更新与回滚、通用失败回滚 pending、
 *    processing 防重复忽略；
 * 2. 审批超时：approval_timeout（chat 非回放）触发 resume 恢复流（REST）、恢复 waiting
 *    不回退 pending、回放事件不触发恢复流；
 * 3. 过期清理：cleanupExpired 过期删除 + timeout 标记、clearByTaskId / clearAll；
 * 4. 审批列表填充与去重：新审批事件填充 Map（camelCase 入站，ARCH-13 F⑥ 后
 *    toCamelCase 已上移至事件入口，store 不再二次转换）+ 双端推送去重 +
 *    debounce 同步后端、restoreFromSSEHistory（pending 恢复 / 去重 / 终态不入 Map /
 *    无 interruptId 告警）、restoreFromSession（pending 恢复 + 异会话清理 / 未知会话）；
 * 5. effectiveSessionId 推导：按当前行为锁定（entry.sessionId 优先、chat 回退
 *    currentSessionId、deep_research 不回退走 researchStore），已知近似重复不修；
 * 6. 状态映射：APPROVAL_STATE_MAP（权威）与 ApprovalState 枚举一致，
 *    6 个真实事件类型在 store 中的路由与取值。
 *
 * 不覆盖：WS approvalHandler 路径（已有 src/stores/sync/__tests__/approvalHandler.test.js）。
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect, vi, beforeAll, afterAll, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { StreamState, ApprovalState } from '@/types'
import { APPROVAL_STATE_MAP } from '@/stores/sync/constants'
import { logger } from '@/utils/logger'
import { ElMessage } from 'element-plus'
import { useApprovalStore } from '../approval/index.js'
import { CLEANUP_INTERVAL_MS } from '../approval/constants'
import { resumeApprovalStream } from '@/api/approval'
import { useSessionStore } from '@/stores/session'
import { useResearchStore } from '@/stores/research'
import { useSyncStore } from '@/stores/sync'

vi.mock('@/api/approval', () => ({
  resumeApprovalStream: vi.fn(),
}))

vi.mock('@/stores/session', () => {
  const sessionStoreMock = {
    currentSessionId: null,
    sessions: [],
    updateToolCallApprovalState: vi.fn(),
    setApprovalToLastMessage: vi.fn(),
    setApprovalToToolCall: vi.fn(),
    appendToLastMessage: vi.fn(),
    syncLastMessageToBackend: vi.fn(() => Promise.resolve()),
    setStreamStateToLastMessage: vi.fn(),
  }
  return { useSessionStore: vi.fn(() => sessionStoreMock) }
})

vi.mock('@/stores/research', () => {
  const researchStoreMock = {
    setApprovalToToolCall: vi.fn(),
    updateToolCallApprovalState: vi.fn(),
  }
  return { useResearchStore: vi.fn(() => researchStoreMock) }
})

vi.mock('@/stores/sync', () => {
  const syncStoreMock = { startStreaming: vi.fn() }
  return { useSyncStore: vi.fn(() => syncStoreMock) }
})

vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

// 抑制被测模块 logger 输出（跟随 approvalHandler.test.js 的 spy 方式）
const loggerSpies = []

beforeAll(() => {
  for (const method of ['log', 'info', 'warn', 'error', 'debug']) {
    loggerSpies.push(vi.spyOn(logger, method).mockImplementation(() => {}))
  }
})

afterAll(() => {
  loggerSpies.forEach((spy) => spy.mockRestore())
})

/** resumed 统一响应（后端 ApprovalGateway 恢复信令已发布） */
const RESUMED_RESPONSE = { data: { data: { status: 'resumed' }, message: '恢复信令已发布' } }

/** 排空微任务队列：覆盖审批串行执行队列 + 动态 import 的 Promise 链 */
const flushMicrotasks = async (ticks = 50) => {
  for (let i = 0; i < ticks; i += 1) {
    await Promise.resolve()
  }
}

/** 构造 pendingApprovals Map 条目 */
const buildEntry = (toolCallId, overrides = {}) => ({
  source: 'chat',
  taskId: null,
  sessionId: 's1',
  approvalData: { interruptId: toolCallId, toolName: 'bash', state: 'pending' },
  createdAt: Date.now(),
  ...overrides,
})

/** 预置审批条目到 pendingApprovals */
const seedApproval = (store, toolCallId, overrides = {}) => {
  const entry = buildEntry(toolCallId, overrides)
  store.pendingApprovals.set(toolCallId, entry)
  return entry
}

beforeEach(() => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  vi.clearAllMocks()
  // mockReset 清掉上一用例可能设置的 rejected/一次性行为，再统一给 resumed 默认响应
  resumeApprovalStream.mockReset()
  resumeApprovalStream.mockResolvedValue(RESUMED_RESPONSE)
  const sessionStore = useSessionStore()
  sessionStore.currentSessionId = null
  sessionStore.sessions = []
})

afterEach(() => {
  // 排空残留定时器（审批同步 debounce / 清理 interval）后恢复真实定时器，避免跨用例泄漏
  vi.runOnlyPendingTimers()
  vi.useRealTimers()
})

describe('executeApproval：REST 审批执行（/approvals/{interrupt_id}/resume/）', () => {
  it('确认审批：调用 resumeApprovalStream（approved=true），条目删除、会话进入流式恢复状态', async () => {
    const store = useApprovalStore()
    seedApproval(store, 'tc-1', { sessionId: 's1' })

    await store.executeApproval({ interruptId: 'tc-1', toolName: 'bash', state: 'pending' }, true, null, {})

    expect(resumeApprovalStream).toHaveBeenCalledTimes(1)
    expect(resumeApprovalStream).toHaveBeenCalledWith(
      'tc-1',
      { approved: true },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
    expect(store.pendingApprovals.has('tc-1')).toBe(false)

    const sessionStore = useSessionStore()
    expect(sessionStore.updateToolCallApprovalState).toHaveBeenCalledWith('s1', 'tc-1', 'processing')
    expect(sessionStore.setApprovalToLastMessage).toHaveBeenCalledWith(
      's1',
      expect.objectContaining({ state: 'processing' }),
    )
    const syncStore = useSyncStore()
    expect(syncStore.startStreaming).toHaveBeenCalledWith('s1')
    expect(sessionStore.setStreamStateToLastMessage).toHaveBeenCalledWith('s1', StreamState.STREAMING)
    expect(sessionStore.syncLastMessageToBackend).toHaveBeenCalledWith('s1', { allowCreate: false })
    // 通过时不设 approved（避免覆盖"处理中"状态），失败也不应误标 rejected
    expect(sessionStore.updateToolCallApprovalState).not.toHaveBeenCalledWith('s1', 'tc-1', 'approved')
    expect(sessionStore.updateToolCallApprovalState).not.toHaveBeenCalledWith('s1', 'tc-1', 'rejected')
  })

  it('确认审批（confirm_with_input）：userInput 透传请求体', async () => {
    const store = useApprovalStore()
    seedApproval(store, 'tc-2', {
      sessionId: 's2',
      approvalData: { interruptId: 'tc-2', toolName: 'bash', action: 'confirm_with_input', state: 'pending' },
    })

    await store.executeApproval(
      { interruptId: 'tc-2', action: 'confirm_with_input', state: 'pending' },
      true,
      'ls -la',
      {},
    )

    expect(resumeApprovalStream).toHaveBeenCalledWith(
      'tc-2',
      { approved: true, userInput: 'ls -la' },
      expect.anything(),
    )
  })

  it('拒绝审批：请求体 approved=false，resumed 后本地标记 rejected', async () => {
    const store = useApprovalStore()
    seedApproval(store, 'tc-3', { sessionId: 's3' })

    await store.executeApproval({ interruptId: 'tc-3', toolName: 'bash', state: 'pending' }, false, null, {})

    expect(resumeApprovalStream).toHaveBeenCalledWith('tc-3', { approved: false }, expect.anything())
    const sessionStore = useSessionStore()
    expect(sessionStore.updateToolCallApprovalState).toHaveBeenCalledWith('s3', 'tc-3', 'rejected')
    expect(sessionStore.setApprovalToLastMessage).toHaveBeenCalledWith(
      's3',
      expect.objectContaining({ state: 'rejected' }),
    )
    expect(store.pendingApprovals.has('tc-3')).toBe(false)
  })

  it('独立深度研究（无 sessionId）：processing 走 researchStore，resumed 提示走 ElMessage.success', async () => {
    const store = useApprovalStore()
    seedApproval(store, 'tc-4', {
      source: 'deep_research',
      taskId: 'task-4',
      sessionId: null,
      approvalData: { interruptId: 'tc-4', toolName: 'deep_search', source: 'deep_research', state: 'pending' },
    })

    await store.executeApproval(
      { interruptId: 'tc-4', toolName: 'deep_search', source: 'deep_research', state: 'pending' },
      true,
      null,
      {},
    )

    const researchStore = useResearchStore()
    expect(researchStore.updateToolCallApprovalState).toHaveBeenCalledWith('task-4', 'tc-4', 'processing')
    // 通过（approved=true）时 researchStore 不再追加 rejected 终态
    expect(researchStore.updateToolCallApprovalState).toHaveBeenCalledTimes(1)
    expect(ElMessage.success).toHaveBeenCalledWith('恢复信令已发布')
    const sessionStore = useSessionStore()
    expect(sessionStore.updateToolCallApprovalState).not.toHaveBeenCalled()
    expect(sessionStore.setStreamStateToLastMessage).not.toHaveBeenCalled()
  })

  it('waiting_for_others：本地状态置 waiting 并提示，不回滚条目、不回退 pending（失败路径）', async () => {
    const store = useApprovalStore()
    seedApproval(store, 'tc-5', { sessionId: 's5' })
    resumeApprovalStream.mockResolvedValueOnce({
      data: { data: { status: 'waiting_for_others', state: 'waiting', message: '等待同批次其他工具审批' } },
    })

    await store.executeApproval({ interruptId: 'tc-5', toolName: 'bash', state: 'pending' }, true, null, {})

    const sessionStore = useSessionStore()
    expect(sessionStore.updateToolCallApprovalState).toHaveBeenCalledWith('s5', 'tc-5', 'waiting')
    expect(ElMessage.info).toHaveBeenCalledWith('等待同批次其他工具审批')
    // __approvalWaiting 提前 return：不回滚条目、不回退 pending
    expect(store.pendingApprovals.has('tc-5')).toBe(false)
    expect(sessionStore.updateToolCallApprovalState).not.toHaveBeenCalledWith('s5', 'tc-5', 'pending')
  })

  it('幂等响应（另一端已处理）：按返回状态更新 approved，不回退 pending（失败路径）', async () => {
    const store = useApprovalStore()
    seedApproval(store, 'tc-6', { sessionId: 's6' })
    resumeApprovalStream.mockResolvedValueOnce({
      data: { data: { idempotent: true, state: 'approved' }, message: '审批已被处理' },
    })

    await store.executeApproval({ interruptId: 'tc-6', toolName: 'bash', state: 'pending' }, true, null, {})

    const sessionStore = useSessionStore()
    expect(sessionStore.updateToolCallApprovalState).toHaveBeenCalledWith('s6', 'tc-6', 'approved')
    expect(store.pendingApprovals.size).toBe(0)
    expect(sessionStore.updateToolCallApprovalState).not.toHaveBeenCalledWith('s6', 'tc-6', 'pending')
    expect(sessionStore.updateToolCallApprovalState).not.toHaveBeenCalledWith('s6', 'tc-6', 'rejected')
  })

  it('通用失败：条目回滚为 pending 并同步会话状态（失败路径不破坏状态）', async () => {
    const store = useApprovalStore()
    seedApproval(store, 'tc-7', { sessionId: 's7' })
    resumeApprovalStream.mockRejectedValueOnce(new Error('网络错误'))

    await store.executeApproval({ interruptId: 'tc-7', toolName: 'bash', state: 'pending' }, true, null, {})

    expect(store.pendingApprovals.size).toBe(1)
    const entry = store.pendingApprovals.get('tc-7')
    expect(entry).toMatchObject({ source: 'chat', sessionId: 's7', taskId: null })
    expect(entry.approvalData.state).toBe('pending')
    const sessionStore = useSessionStore()
    expect(sessionStore.updateToolCallApprovalState).toHaveBeenCalledWith('s7', 'tc-7', 'pending')
    expect(sessionStore.syncLastMessageToBackend).toHaveBeenCalledWith('s7', { allowCreate: false })
  })

  it('防重复：approvalData 已是 processing 时直接忽略，不触发 REST 调用（失败路径）', async () => {
    const store = useApprovalStore()
    seedApproval(store, 'tc-8', {
      sessionId: 's8',
      approvalData: { interruptId: 'tc-8', toolName: 'bash', state: 'processing' },
    })

    await store.executeApproval({ interruptId: 'tc-8', toolName: 'bash', state: 'processing' }, true, null, {})

    expect(resumeApprovalStream).not.toHaveBeenCalled()
    expect(store.pendingApprovals.has('tc-8')).toBe(true)
    expect(store.pendingApprovals.get('tc-8').approvalData.state).toBe('processing')
  })
})

describe('审批超时：REST 触发恢复流（timeout_resume）', () => {
  it('approval_timeout（chat、非回放）：条目删除 + 追加超时提示 + 触发 resume 恢复流', async () => {
    const store = useApprovalStore()
    seedApproval(store, 'tc-t', {
      sessionId: 's1',
      approvalData: { interruptId: 'tc-t', graphInterruptId: 'gi-t', toolName: 'bash', state: 'pending' },
    })

    store.handleApprovalEvent(
      { type: 'approval_timeout', interruptId: 'tc-t', toolName: 'bash', graphInterruptId: 'gi-t' },
      { source: 'chat', sessionId: 's1' },
    )
    await flushMicrotasks()

    expect(store.pendingApprovals.has('tc-t')).toBe(false)
    const sessionStore = useSessionStore()
    expect(sessionStore.appendToLastMessage).toHaveBeenCalledWith('s1', expect.stringContaining('超时'))
    expect(sessionStore.updateToolCallApprovalState).toHaveBeenCalledWith('s1', 'tc-t', 'timeout')
    // timeoutResume：请求体与普通拒绝一致（approved=false），本地不回退 pending / rejected
    expect(resumeApprovalStream).toHaveBeenCalledWith('tc-t', { approved: false }, expect.anything())
    expect(sessionStore.setStreamStateToLastMessage).toHaveBeenCalledWith('s1', StreamState.STREAMING)
    expect(sessionStore.updateToolCallApprovalState).not.toHaveBeenCalledWith('s1', 'tc-t', 'pending')
    expect(sessionStore.updateToolCallApprovalState).not.toHaveBeenCalledWith('s1', 'tc-t', 'rejected')
    // chat 来源超时不弹 ElMessage.warning（仅独立深研弹）
    expect(ElMessage.warning).not.toHaveBeenCalled()
  })

  it('超时恢复 waiting：批次未决断，本地保持 timeout/waiting、不回退 pending（失败路径）', async () => {
    const store = useApprovalStore()
    seedApproval(store, 'tc-tw', {
      sessionId: 's-tw',
      approvalData: { interruptId: 'tc-tw', graphInterruptId: 'gi-tw', toolName: 'bash', state: 'pending' },
    })
    // 真实路径：后端返回 waiting_for_others（resolved JSON）→ _executeApprovalStream
    // 置 waiting 并抛 __approvalWaiting → executeApproval 吞掉标记正常返回（不回滚）
    resumeApprovalStream.mockResolvedValueOnce({
      data: { data: { status: 'waiting_for_others', state: 'waiting', message: '等待同批次其他工具审批' } },
    })

    store.handleApprovalEvent(
      { type: 'approval_timeout', interruptId: 'tc-tw', toolName: 'bash' },
      { source: 'chat', sessionId: 's-tw' },
    )
    await flushMicrotasks()

    const sessionStore = useSessionStore()
    expect(resumeApprovalStream).toHaveBeenCalledTimes(1)
    expect(sessionStore.updateToolCallApprovalState).toHaveBeenCalledWith('s-tw', 'tc-tw', 'timeout')
    expect(sessionStore.updateToolCallApprovalState).toHaveBeenCalledWith('s-tw', 'tc-tw', 'waiting')
    expect(store.pendingApprovals.has('tc-tw')).toBe(false)
    expect(sessionStore.updateToolCallApprovalState).not.toHaveBeenCalledWith('s-tw', 'tc-tw', 'pending')
    expect(sessionStore.updateToolCallApprovalState).not.toHaveBeenCalledWith('s-tw', 'tc-tw', 'rejected')
  })

  it('approval_timeout 回放（isReplay）：仅状态重建，不追加提示、不触发 resume（失败路径）', async () => {
    const store = useApprovalStore()
    seedApproval(store, 'tc-tr', {
      sessionId: 's1',
      approvalData: { interruptId: 'tc-tr', graphInterruptId: 'gi-tr', toolName: 'bash', state: 'pending' },
    })

    store.handleApprovalEvent(
      { type: 'approval_timeout', interruptId: 'tc-tr', toolName: 'bash' },
      { source: 'chat', sessionId: 's1', isReplay: true },
    )
    await flushMicrotasks()

    const sessionStore = useSessionStore()
    expect(sessionStore.appendToLastMessage).not.toHaveBeenCalled()
    expect(sessionStore.updateToolCallApprovalState).toHaveBeenCalledWith('s1', 'tc-tr', 'timeout')
    expect(resumeApprovalStream).not.toHaveBeenCalled()
  })
})

describe('过期清理', () => {
  it('cleanupExpired：过期条目删除并标记 timeout，未过期保留，无 sessionId 条目仅删除', () => {
    const store = useApprovalStore()
    const sessionStore = useSessionStore()
    const now = Date.now()
    store.pendingApprovals.set('tc-old', {
      source: 'chat',
      taskId: null,
      sessionId: 's1',
      approvalData: { interruptId: 'tc-old', toolName: 'bash', state: 'pending' },
      createdAt: now - 31 * 60 * 1000,
    })
    store.pendingApprovals.set('tc-fresh', {
      source: 'chat',
      taskId: null,
      sessionId: 's1',
      approvalData: { interruptId: 'tc-fresh', toolName: 'bash', state: 'pending' },
      createdAt: now,
    })
    store.pendingApprovals.set('tc-orphan', {
      source: 'deep_research',
      taskId: 'task-x',
      sessionId: null,
      approvalData: { interruptId: 'tc-orphan', toolName: 'deep_search', state: 'pending' },
      createdAt: now - 40 * 60 * 1000,
    })

    // cleanupExpired 已收敛为切片内部方法（不经 store 面导出），
    // 经装配时 startCleanup 注册的自动清理定时器路径触发
    vi.advanceTimersByTime(CLEANUP_INTERVAL_MS)

    expect(store.pendingApprovals.has('tc-old')).toBe(false)
    expect(store.pendingApprovals.has('tc-orphan')).toBe(false)
    expect(store.pendingApprovals.has('tc-fresh')).toBe(true)
    expect(sessionStore.updateToolCallApprovalState).toHaveBeenCalledWith('s1', 'tc-old', 'timeout')
    expect(sessionStore.updateToolCallApprovalState).not.toHaveBeenCalledWith('s1', 'tc-orphan', 'timeout')
    expect(sessionStore.setApprovalToLastMessage).toHaveBeenCalledWith(
      's1',
      expect.objectContaining({ state: 'timeout' }),
    )
  })

  it('clearByTaskId 清理同任务全部条目（空 taskId 直接返回），clearAll 清空', () => {
    const store = useApprovalStore()
    seedApproval(store, 'tc-c1', { taskId: 'task-1' })
    seedApproval(store, 'tc-c2', { taskId: 'task-1' })
    seedApproval(store, 'tc-c3', { taskId: 'task-2' })

    store.clearByTaskId('task-1')
    expect(store.pendingApprovals.size).toBe(1)
    expect(store.pendingApprovals.has('tc-c3')).toBe(true)

    store.clearByTaskId('')
    expect(store.pendingApprovals.size).toBe(1)

    store.clearAll()
    expect(store.pendingApprovals.size).toBe(0)
  })
})

describe('审批列表填充与去重（pendingApprovals Map）', () => {
  it('新审批事件填充 Map（camelCase 入站，toCamelCase 已上移至事件入口），重复推送去重，debounce 同步后端', () => {
    const store = useApprovalStore()
    const sessionStore = useSessionStore()
    const payload = { type: 'approval_pending', interruptId: 'tc-n', toolName: 'bash' }

    store.handleApprovalEvent(payload, { source: 'chat', sessionId: 's-fill' })

    expect(store.pendingApprovals.size).toBe(1)
    const entry = store.pendingApprovals.get('tc-n')
    expect(entry).toMatchObject({ source: 'chat', sessionId: 's-fill', taskId: null })
    expect(entry.approvalData.state).toBe(ApprovalState.PENDING)
    expect(entry.approvalData.toolName).toBe('bash')
    expect(sessionStore.setApprovalToToolCall).toHaveBeenCalledTimes(1)

    // 双端 SSE 同时推送：同 interruptId 且 pending → 去重跳过
    store.handleApprovalEvent({ ...payload }, { source: 'chat', sessionId: 's-fill' })
    expect(store.pendingApprovals.size).toBe(1)
    expect(sessionStore.setApprovalToToolCall).toHaveBeenCalledTimes(1)

    // 审批事件到达后 debounce(200ms) 同步到后端，且去重后仅一次
    vi.advanceTimersByTime(200)
    expect(sessionStore.syncLastMessageToBackend).toHaveBeenCalledTimes(1)
    expect(sessionStore.syncLastMessageToBackend).toHaveBeenCalledWith('s-fill', { allowCreate: false })
  })

  it('restoreFromSSEHistory：pending 恢复 deep_research 条目并同步会话，重复恢复跳过', () => {
    const store = useApprovalStore()
    const sessionStore = useSessionStore()

    store.restoreFromSSEHistory({ interruptId: 'tc-h1', toolName: 'search', state: 'pending' }, 'task-9', 's1')

    expect(store.pendingApprovals.size).toBe(1)
    expect(store.pendingApprovals.get('tc-h1')).toMatchObject({
      source: 'deep_research',
      taskId: 'task-9',
      sessionId: 's1',
    })
    expect(store.pendingApprovals.get('tc-h1').approvalData.state).toBe(ApprovalState.PENDING)
    expect(sessionStore.setApprovalToToolCall).toHaveBeenCalledWith(
      's1',
      'tc-h1',
      expect.objectContaining({ state: 'pending' }),
    )

    // 已存在则跳过（可能已通过实时推送收到）
    store.restoreFromSSEHistory({ interruptId: 'tc-h1', toolName: 'search', state: 'pending' }, 'task-9', 's1')
    expect(store.pendingApprovals.size).toBe(1)
  })

  it('restoreFromSSEHistory：终态（approved）不入 Map，仅更新会话 UI 状态', () => {
    const store = useApprovalStore()
    const sessionStore = useSessionStore()

    store.restoreFromSSEHistory({ interruptId: 'tc-h2', toolName: 'search', state: 'approved' }, 'task-9', 's1')

    expect(store.pendingApprovals.size).toBe(0)
    expect(sessionStore.updateToolCallApprovalState).toHaveBeenCalledWith('s1', 'tc-h2', 'approved')
    expect(sessionStore.setApprovalToLastMessage).toHaveBeenCalledWith(
      's1',
      expect.objectContaining({ state: 'approved' }),
    )
  })

  it('restoreFromSSEHistory：无法提取 interruptId 时告警且不填充（失败路径）', () => {
    const store = useApprovalStore()

    store.restoreFromSSEHistory({ toolName: 'search' }, 'task-9', null)

    expect(store.pendingApprovals.size).toBe(0)
    expect(logger.warn).toHaveBeenCalled()
  })

  it('restoreFromSession：恢复 pending toolCalls，清理异会话旧条目，跳过非 pending', () => {
    const store = useApprovalStore()
    const sessionStore = useSessionStore()
    seedApproval(store, 'tc-stale', { sessionId: 's-other' })
    sessionStore.sessions = [
      {
        id: 's-restore',
        messages: [
          {
            role: 'assistant',
            toolCalls: [
              { id: 'tc-r1', approval: { interruptId: 'tc-r1', toolName: 'bash', state: 'pending' } },
              { id: 'tc-r2', approval: { interruptId: 'tc-r2', state: 'approved' } },
            ],
          },
        ],
      },
    ]

    store.restoreFromSession('s-restore')

    expect(store.pendingApprovals.has('tc-stale')).toBe(false)
    expect(store.pendingApprovals.has('tc-r2')).toBe(false)
    expect(store.pendingApprovals.size).toBe(1)
    const entry = store.pendingApprovals.get('tc-r1')
    expect(entry).toMatchObject({ source: 'chat', sessionId: 's-restore', taskId: null })
    expect(entry.approvalData.state).toBe(ApprovalState.PENDING)
  })

  it('restoreFromSession：未知会话时先清理异会话条目再返回（锁定现状）', () => {
    const store = useApprovalStore()
    seedApproval(store, 'tc-stale2', { sessionId: 's-other' })

    store.restoreFromSession('unknown-session')

    expect(store.pendingApprovals.size).toBe(0)
  })
})

describe('effectiveSessionId 推导（按当前行为锁定，已知近似重复不修）', () => {
  it('timeout：entry.sessionId 优先于 sessionStore.currentSessionId', async () => {
    const store = useApprovalStore()
    const sessionStore = useSessionStore()
    sessionStore.currentSessionId = 'current-s1'
    seedApproval(store, 'tc-e1', {
      sessionId: 'entry-s1',
      approvalData: { interruptId: 'tc-e1', graphInterruptId: 'gi-e1', toolName: 'bash', state: 'pending' },
    })

    store.handleApprovalEvent(
      { type: 'approval_timeout', interruptId: 'tc-e1', toolName: 'bash' },
      { source: 'chat' },
    )
    await flushMicrotasks()

    expect(sessionStore.updateToolCallApprovalState).toHaveBeenCalledWith('entry-s1', 'tc-e1', 'timeout')
    expect(sessionStore.updateToolCallApprovalState).not.toHaveBeenCalledWith('current-s1', 'tc-e1', 'timeout')
  })

  it('processing：chat 来源且 entry 无 sessionId 时回退 currentSessionId（锁定现状）', () => {
    const store = useApprovalStore()
    const sessionStore = useSessionStore()
    sessionStore.currentSessionId = 'cur-1'
    seedApproval(store, 'tc-e2', { sessionId: null })

    store.handleApprovalEvent(
      { type: 'approval_processing', interruptId: 'tc-e2', toolName: 'bash' },
      { source: 'chat' },
    )

    expect(sessionStore.updateToolCallApprovalState).toHaveBeenCalledWith('cur-1', 'tc-e2', 'processing')
    expect(store.pendingApprovals.get('tc-e2').approvalData.state).toBe('processing')
  })

  it('waiting：deep_research 来源不回退 currentSessionId，改走 researchStore（锁定现状）', async () => {
    const store = useApprovalStore()
    const sessionStore = useSessionStore()
    sessionStore.currentSessionId = 'cur-2'
    seedApproval(store, 'tc-e3', {
      source: 'deep_research',
      taskId: 'task-e3',
      sessionId: null,
      approvalData: { interruptId: 'tc-e3', toolName: 'deep_search', source: 'deep_research', state: 'pending' },
    })

    store.handleApprovalEvent(
      { type: 'approval_waiting', interruptId: 'tc-e3', toolName: 'deep_search' },
      { source: 'deep_research' },
    )
    await flushMicrotasks()

    const researchStore = useResearchStore()
    expect(researchStore.updateToolCallApprovalState).toHaveBeenCalledWith('task-e3', 'tc-e3', 'waiting')
    expect(sessionStore.updateToolCallApprovalState).not.toHaveBeenCalled()
    expect(store.pendingApprovals.get('tc-e3').approvalData.state).toBe('waiting')
  })
})

describe('状态映射：6 个真实事件类型（APPROVAL_STATE_MAP 为权威）', () => {
  it('APPROVAL_STATE_MAP 与 ApprovalState 枚举一一对应（唯一权威锁定）', () => {
    expect(APPROVAL_STATE_MAP).toEqual({
      approval_pending: ApprovalState.PENDING,
      approval_processing: ApprovalState.PROCESSING,
      approval_waiting: ApprovalState.WAITING,
      approval_approved: ApprovalState.APPROVED,
      approval_rejected: ApprovalState.REJECTED,
      approval_timeout: ApprovalState.TIMEOUT,
    })
  })

  it('approval_pending → 新建条目 state=PENDING 并填充 Map', () => {
    const store = useApprovalStore()

    store.handleApprovalEvent(
      { type: 'approval_pending', interruptId: 'mp-1', toolName: 'bash' },
      { source: 'chat', sessionId: 's-map' },
    )

    expect(store.pendingApprovals.size).toBe(1)
    expect(store.pendingApprovals.get('mp-1').approvalData.state).toBe(APPROVAL_STATE_MAP.approval_pending)
  })

  it('approval_processing → state=PROCESSING 且保留在 Map（面板禁用按钮）', () => {
    const store = useApprovalStore()
    seedApproval(store, 'mp-2', { sessionId: 's-map' })

    store.handleApprovalEvent(
      { type: 'approval_processing', interruptId: 'mp-2', toolName: 'bash' },
      { source: 'chat', sessionId: 's-map' },
    )

    expect(store.pendingApprovals.size).toBe(1)
    expect(store.pendingApprovals.get('mp-2').approvalData.state).toBe(APPROVAL_STATE_MAP.approval_processing)
    const sessionStore = useSessionStore()
    expect(sessionStore.updateToolCallApprovalState).toHaveBeenCalledWith('s-map', 'mp-2', ApprovalState.PROCESSING)
  })

  it('approval_waiting → state=WAITING 且保留在 Map（等待同批次）', () => {
    const store = useApprovalStore()
    seedApproval(store, 'mp-3', { sessionId: 's-map' })

    store.handleApprovalEvent(
      { type: 'approval_waiting', interruptId: 'mp-3', toolName: 'bash' },
      { source: 'chat', sessionId: 's-map' },
    )

    expect(store.pendingApprovals.size).toBe(1)
    expect(store.pendingApprovals.get('mp-3').approvalData.state).toBe(APPROVAL_STATE_MAP.approval_waiting)
  })

  it('approval_approved → 条目移除且会话状态更新为 APPROVED', () => {
    const store = useApprovalStore()
    seedApproval(store, 'mp-4', { sessionId: 's-map' })

    store.handleApprovalEvent(
      { type: 'approval_approved', state: ApprovalState.APPROVED, approved: true, interruptId: 'mp-4', toolName: 'bash' },
      { source: 'chat', sessionId: 's-map' },
    )

    expect(store.pendingApprovals.size).toBe(0)
    const sessionStore = useSessionStore()
    expect(sessionStore.updateToolCallApprovalState).toHaveBeenCalledWith(
      's-map',
      'mp-4',
      APPROVAL_STATE_MAP.approval_approved,
    )
  })

  it('approval_rejected → 条目移除且会话状态更新为 REJECTED', () => {
    const store = useApprovalStore()
    seedApproval(store, 'mp-5', { sessionId: 's-map' })

    store.handleApprovalEvent(
      { type: 'approval_rejected', state: ApprovalState.REJECTED, approved: false, interruptId: 'mp-5', toolName: 'bash' },
      { source: 'chat', sessionId: 's-map' },
    )

    expect(store.pendingApprovals.size).toBe(0)
    const sessionStore = useSessionStore()
    expect(sessionStore.updateToolCallApprovalState).toHaveBeenCalledWith(
      's-map',
      'mp-5',
      APPROVAL_STATE_MAP.approval_rejected,
    )
  })

  it('approval_timeout → 条目移除且会话状态更新为 TIMEOUT（恢复流联动 REST）', async () => {
    const store = useApprovalStore()
    seedApproval(store, 'mp-6', {
      sessionId: 's-map',
      approvalData: { interruptId: 'mp-6', graphInterruptId: 'gi-mp-6', toolName: 'bash', state: 'pending' },
    })

    store.handleApprovalEvent(
      { type: 'approval_timeout', interruptId: 'mp-6', toolName: 'bash' },
      { source: 'chat', sessionId: 's-map' },
    )

    expect(store.pendingApprovals.size).toBe(0)
    const sessionStore = useSessionStore()
    expect(sessionStore.updateToolCallApprovalState).toHaveBeenCalledWith(
      's-map',
      'mp-6',
      APPROVAL_STATE_MAP.approval_timeout,
    )
    await flushMicrotasks()
    expect(resumeApprovalStream).toHaveBeenCalled()
  })
})
