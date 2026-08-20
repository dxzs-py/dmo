/**
 * approvalHandler 单元测试（C5/cq-04 Task 4）
 *
 * 被测：src/stores/sync/approvalHandler.js —— createHandleApprovalEvent 工厂。
 * sessionStore / approvalStore 以纯对象 + vi.fn() 注入；scheduleSubagentsRefresh
 * 通过 vi.mock('@/composables/useSubagents') 替换；logger 直接 spyOn 抑制输出。
 *
 * 覆盖分支：
 * 1. 未知审批事件类型：warn 后提前 return（不触达 approvalStore / 兜底 / 子代理刷新）；
 * 2. enrichedPayload 构造：type/state 注入并覆盖 payload 同名字段，
 *    approval_approved → approved=true、approval_rejected → approved=false、其余不注入；
 *    options 透传（source/isReplay）；
 * 3. sessionId 路由：会话存在且消息非空直接派发；会话缺失或消息为空 → loadSessionDetail
 *    兜底（含抛异常容错）；chatSessionId 解析优先级 payload.sessionId > payload.chatSessionId > 参数；
 * 4. 仅 taskId 路由（独立深研）：以 { source, taskId, isReplay } 派发并提前 return，
 *    不做 streamState 转换；sessionId 与 taskId 均为空时不派发；
 * 5. approval_pending → INTERRUPTED + isStreaming=true：messageId / extra.messageId 定位、
 *    最后一条 assistant 兜底、已 INTERRUPTED 幂等跳过、无目标消息静默返回；
 * 6. 批量审批（graphInterruptId）：remainingPendingCount 权威计数（>0 保持 / =0 不转换）；
 *    本地 sibling 计算（任一 approved/processing → STREAMING、全部 rejected/timeout →
 *    isFailed=true、全 waiting / 空集不动）；批次 id 双来源解析；非 INTERRUPTED 不进入；
 * 7. 非批量 approved/processing → STREAMING；rejected/timeout/waiting 保持 INTERRUPTED；
 * 8. 子代理审批联动：pending/approved/rejected/timeout 触发 scheduleSubagentsRefresh，
 *    父线程 id 解析 payload.sourceId > chatSessionId > taskId；processing/waiting /
 *    无 subagentThreadId 不触发。
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest'
import { logger } from '@/utils/logger'
import { StreamState, ApprovalState } from '@/types'
import { scheduleSubagentsRefresh } from '@/composables/useSubagents'
import { createHandleApprovalEvent } from '../approvalHandler.js'

vi.mock('@/composables/useSubagents', () => ({
  scheduleSubagentsRefresh: vi.fn(),
}))

// 抑制被测模块的 logger 输出；直接 spy logger 对象使 warn 断言不依赖 import.meta.env.DEV
const loggerSpies = []

beforeAll(() => {
  for (const method of ['log', 'info', 'warn', 'error', 'debug']) {
    loggerSpies.push(vi.spyOn(logger, method).mockImplementation(() => {}))
  }
})

afterAll(() => {
  loggerSpies.forEach(spy => spy.mockRestore())
})

beforeEach(() => {
  vi.clearAllMocks()
})

// ── 测试工厂 ──────────────────────────────────────────────────────────────────
const buildAssistantMessage = (overrides = {}) => ({
  id: 'msg-local',
  backendId: 'msg-backend',
  role: 'assistant',
  ...overrides,
})

const buildSession = (id, messages) => ({ id, messages })

const buildHandler = (sessions = []) => {
  const sessionStore = { sessions, loadSessionDetail: vi.fn() }
  const approvalStore = { handleApprovalEvent: vi.fn() }
  return {
    sessionStore,
    approvalStore,
    ...createHandleApprovalEvent({ sessionStore, approvalStore }),
  }
}

/** 批量审批消息：toolCalls 共享 graphInterruptId='gi-1'，approval.state 由 siblingStates 指定 */
const buildBatchMessage = (siblingStates, overrides = {}) => buildAssistantMessage({
  streamState: StreamState.INTERRUPTED,
  isStreaming: true,
  toolCalls: siblingStates.map((state, index) => ({
    id: `tc-${index + 1}`,
    approval: { graphInterruptId: 'gi-1', state },
  })),
  ...overrides,
})

describe('createHandleApprovalEvent', () => {
  // ── 1. 未知事件类型 ────────────────────────────────────────────────────────
  it('未知事件类型：warn 后提前 return，不触达 approvalStore / 兜底拉取 / 子代理刷新', async () => {
    const { sessionStore, approvalStore, handleApprovalEvent } = buildHandler([
      buildSession('s1', [buildAssistantMessage()]),
    ])

    await handleApprovalEvent('s1', { subagentThreadId: 'sa-1' }, 'approval_unknown', { taskId: 't1' })

    expect(approvalStore.handleApprovalEvent).not.toHaveBeenCalled()
    expect(sessionStore.loadSessionDetail).not.toHaveBeenCalled()
    expect(scheduleSubagentsRefresh).not.toHaveBeenCalled()
    expect(logger.warn).toHaveBeenCalled()
  })

  // ── 2. enrichedPayload 构造 ────────────────────────────────────────────────
  it('enrichedPayload：approval_approved 注入 type/state 并覆盖 payload 同名字段，approved 强制为 true', async () => {
    const { approvalStore, handleApprovalEvent } = buildHandler([
      buildSession('s1', [buildAssistantMessage()]),
    ])

    await handleApprovalEvent(
      's1',
      { toolName: 'search', interruptId: 'i-1', type: 'raw', state: 'stale', approved: false },
      'approval_approved',
    )

    expect(approvalStore.handleApprovalEvent).toHaveBeenCalledTimes(1)
    const [payloadArg, optionsArg] = approvalStore.handleApprovalEvent.mock.calls[0]
    expect(payloadArg).toStrictEqual({
      toolName: 'search',
      interruptId: 'i-1',
      type: 'approval_approved',
      state: ApprovalState.APPROVED,
      approved: true,
    })
    expect(optionsArg).toStrictEqual({ source: 'chat', sessionId: 's1', isReplay: false })
  })

  it('enrichedPayload：approval_rejected 注入 approved=false', async () => {
    const { approvalStore, handleApprovalEvent } = buildHandler([
      buildSession('s1', [buildAssistantMessage()]),
    ])

    await handleApprovalEvent('s1', { toolName: 'bash' }, 'approval_rejected')

    const [payloadArg] = approvalStore.handleApprovalEvent.mock.calls[0]
    expect(payloadArg).toStrictEqual({
      toolName: 'bash',
      type: 'approval_rejected',
      state: ApprovalState.REJECTED,
      approved: false,
    })
  })

  it('enrichedPayload：pending/processing/waiting/timeout 不注入 approved 字段', async () => {
    const { approvalStore, handleApprovalEvent } = buildHandler([
      buildSession('s1', [buildAssistantMessage()]),
    ])

    for (const eventType of ['approval_pending', 'approval_processing', 'approval_waiting', 'approval_timeout']) {
      await handleApprovalEvent('s1', { toolName: 't' }, eventType)
    }

    expect(approvalStore.handleApprovalEvent).toHaveBeenCalledTimes(4)
    for (const [payloadArg] of approvalStore.handleApprovalEvent.mock.calls) {
      expect('approved' in payloadArg).toBe(false)
    }
  })

  it('options 透传：source/isReplay 以 options 为准注入 approvalStore 调用', async () => {
    const { approvalStore, handleApprovalEvent } = buildHandler([
      buildSession('s1', [buildAssistantMessage()]),
    ])

    await handleApprovalEvent('s1', {}, 'approval_approved', { source: 'learning', isReplay: true })

    const [, optionsArg] = approvalStore.handleApprovalEvent.mock.calls[0]
    expect(optionsArg).toStrictEqual({ source: 'learning', sessionId: 's1', isReplay: true })
  })

  // ── 3. sessionId 路由与兜底 ────────────────────────────────────────────────
  it('sessionId 路由：会话存在且消息非空时直接派发，不触发兜底拉取', async () => {
    const { sessionStore, approvalStore, handleApprovalEvent } = buildHandler([
      buildSession('s1', [buildAssistantMessage()]),
    ])

    await handleApprovalEvent('s1', {}, 'approval_approved')

    expect(sessionStore.loadSessionDetail).not.toHaveBeenCalled()
    expect(approvalStore.handleApprovalEvent).toHaveBeenCalledTimes(1)
  })

  it('sessionId 路由：会话不存在时先兜底 loadSessionDetail（forceRefresh）再派发', async () => {
    const { sessionStore, approvalStore, handleApprovalEvent } = buildHandler([])

    await handleApprovalEvent('s1', {}, 'approval_approved')

    expect(sessionStore.loadSessionDetail).toHaveBeenCalledWith('s1', { forceRefresh: true })
    expect(approvalStore.handleApprovalEvent).toHaveBeenCalledTimes(1)
  })

  it('sessionId 路由：会话存在但消息为空时同样触发兜底拉取', async () => {
    const { sessionStore, handleApprovalEvent } = buildHandler([buildSession('s1', [])])

    await handleApprovalEvent('s1', {}, 'approval_approved')

    expect(sessionStore.loadSessionDetail).toHaveBeenCalledWith('s1', { forceRefresh: true })
  })

  it('兜底容错：loadSessionDetail 抛异常时吞掉并继续派发 approvalStore', async () => {
    const { sessionStore, approvalStore, handleApprovalEvent } = buildHandler([])
    sessionStore.loadSessionDetail.mockRejectedValueOnce(new Error('network error'))

    await handleApprovalEvent('s1', {}, 'approval_approved')

    expect(approvalStore.handleApprovalEvent).toHaveBeenCalledTimes(1)
    expect(logger.warn).toHaveBeenCalled()
  })

  it('chatSessionId 解析：payload.sessionId 优先于 WebSocket 会话参数（兜底判断与派发均用它）', async () => {
    const msg = buildAssistantMessage()
    const { sessionStore, approvalStore, handleApprovalEvent } = buildHandler([
      buildSession('real-session', [msg]),
    ])

    await handleApprovalEvent('ws-session', { sessionId: 'real-session' }, 'approval_approved')

    expect(sessionStore.loadSessionDetail).not.toHaveBeenCalled()
    const [, optionsArg] = approvalStore.handleApprovalEvent.mock.calls[0]
    expect(optionsArg.sessionId).toBe('real-session')
    // streamState 定位仍用 WebSocket 会话参数（ws-session 无会话）→ 消息不被改动
    expect(msg.streamState).toBeUndefined()
  })

  it('chatSessionId 解析：payload.chatSessionId 次优先，无 payload 字段时回退参数 sessionId', async () => {
    const { approvalStore, handleApprovalEvent } = buildHandler([
      buildSession('s1', [buildAssistantMessage()]),
    ])

    await handleApprovalEvent('ws-session', { chatSessionId: 's1' }, 'approval_approved')
    await handleApprovalEvent('s1', {}, 'approval_approved')

    expect(approvalStore.handleApprovalEvent.mock.calls[0][1].sessionId).toBe('s1')
    expect(approvalStore.handleApprovalEvent.mock.calls[1][1].sessionId).toBe('s1')
  })

  // ── 4. 仅 taskId 路由（独立深研） ──────────────────────────────────────────
  it('仅 taskId（独立深研）：以 { source, taskId, isReplay } 派发并提前 return，不做 streamState 转换', async () => {
    const msg = buildAssistantMessage({ streamState: StreamState.STREAMING })
    const { approvalStore, handleApprovalEvent } = buildHandler([buildSession('s1', [msg])])

    await handleApprovalEvent(null, { toolName: 'deep_search' }, 'approval_pending', {
      taskId: 'task-1',
      source: 'deep_research',
    })

    expect(approvalStore.handleApprovalEvent).toHaveBeenCalledTimes(1)
    const [payloadArg, optionsArg] = approvalStore.handleApprovalEvent.mock.calls[0]
    expect(payloadArg.type).toBe('approval_pending')
    expect(payloadArg.state).toBe(ApprovalState.PENDING)
    expect(optionsArg).toStrictEqual({ source: 'deep_research', taskId: 'task-1', isReplay: false })
    // 提前 return：approval_pending 不触发消息状态转换
    expect(msg.streamState).toBe(StreamState.STREAMING)
    expect(msg.isStreaming).toBeUndefined()
  })

  it('sessionId 与 taskId 均为空：不派发 approvalStore', async () => {
    const { approvalStore, handleApprovalEvent } = buildHandler([
      buildSession('s1', [buildAssistantMessage()]),
    ])

    await handleApprovalEvent(null, {}, 'approval_approved')

    expect(approvalStore.handleApprovalEvent).not.toHaveBeenCalled()
  })

  // ── 5. approval_pending → INTERRUPTED ──────────────────────────────────────
  it('approval_pending：无 messageId 时定位最后一条 assistant 消息并置 INTERRUPTED + isStreaming', async () => {
    const oldAssistant = buildAssistantMessage({ id: 'a-old', backendId: 'b-old' })
    const lastAssistant = buildAssistantMessage({ id: 'a-last', backendId: 'b-last' })
    const { handleApprovalEvent } = buildHandler([
      buildSession('s1', [{ id: 'u1', role: 'user' }, oldAssistant, lastAssistant]),
    ])

    await handleApprovalEvent('s1', {}, 'approval_pending')

    expect(lastAssistant.streamState).toBe(StreamState.INTERRUPTED)
    expect(lastAssistant.isStreaming).toBe(true)
    expect(oldAssistant.streamState).toBeUndefined()
  })

  it('approval_pending：payload.messageId 优先定位非末尾消息（backendId 匹配）', async () => {
    const oldAssistant = buildAssistantMessage({ id: 'a-old', backendId: 'b-old' })
    const lastAssistant = buildAssistantMessage({ id: 'a-last', backendId: 'b-last' })
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [oldAssistant, lastAssistant])])

    await handleApprovalEvent('s1', { messageId: 'b-old' }, 'approval_pending')

    expect(oldAssistant.streamState).toBe(StreamState.INTERRUPTED)
    expect(oldAssistant.isStreaming).toBe(true)
    expect(lastAssistant.streamState).toBeUndefined()
  })

  it('approval_pending：extra.messageId 兜底定位（id 匹配）', async () => {
    const oldAssistant = buildAssistantMessage({ id: 'a-old', backendId: 'b-old' })
    const lastAssistant = buildAssistantMessage({ id: 'a-last', backendId: 'b-last' })
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [oldAssistant, lastAssistant])])

    await handleApprovalEvent('s1', { extra: { messageId: 'a-old' } }, 'approval_pending')

    expect(oldAssistant.streamState).toBe(StreamState.INTERRUPTED)
    expect(lastAssistant.streamState).toBeUndefined()
  })

  it('approval_pending 幂等：已 INTERRUPTED 的消息不重复转换（isStreaming 保持原值）', async () => {
    const msg = buildAssistantMessage({ streamState: StreamState.INTERRUPTED, isStreaming: false })
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [msg])])

    await handleApprovalEvent('s1', {}, 'approval_pending')

    expect(msg.streamState).toBe(StreamState.INTERRUPTED)
    expect(msg.isStreaming).toBe(false)
  })

  it('approval_pending：目标消息不存在（会话未加载）时静默返回，不抛异常', async () => {
    const { handleApprovalEvent } = buildHandler([])

    await expect(handleApprovalEvent('s1', {}, 'approval_pending')).resolves.toBeUndefined()
  })

  // ── 6. 批量审批（graphInterruptId 存在） ───────────────────────────────────
  it('批量审批（本地计算）：任一 sibling approved → INTERRUPTED → STREAMING', async () => {
    const msg = buildBatchMessage([ApprovalState.PENDING, ApprovalState.APPROVED])
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [msg])])

    await handleApprovalEvent('s1', { graphInterruptId: 'gi-1' }, 'approval_approved')

    expect(msg.streamState).toBe(StreamState.STREAMING)
    expect(msg.isFailed).toBeUndefined()
  })

  it('批量审批（本地计算）：sibling processing 同样触发 → STREAMING', async () => {
    const msg = buildBatchMessage([ApprovalState.PROCESSING])
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [msg])])

    await handleApprovalEvent('s1', { graphInterruptId: 'gi-1' }, 'approval_approved')

    expect(msg.streamState).toBe(StreamState.STREAMING)
  })

  it('批量审批（本地计算）：approved 与 rejected 混合 → STREAMING（some 优先于 every）', async () => {
    const msg = buildBatchMessage([ApprovalState.REJECTED, ApprovalState.APPROVED])
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [msg])])

    await handleApprovalEvent('s1', { graphInterruptId: 'gi-1' }, 'approval_approved')

    expect(msg.streamState).toBe(StreamState.STREAMING)
    expect(msg.isFailed).toBeUndefined()
  })

  it('批量审批（本地计算）：全部 rejected/timeout → 保持 INTERRUPTED 并标记 isFailed', async () => {
    const msg = buildBatchMessage([ApprovalState.REJECTED, ApprovalState.TIMEOUT])
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [msg])])

    await handleApprovalEvent('s1', { graphInterruptId: 'gi-1' }, 'approval_rejected')

    expect(msg.streamState).toBe(StreamState.INTERRUPTED)
    expect(msg.isFailed).toBe(true)
  })

  it('批量审批（本地计算）：全部 waiting → 不转换也不标记 failed', async () => {
    const msg = buildBatchMessage([ApprovalState.WAITING, ApprovalState.WAITING])
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [msg])])

    await handleApprovalEvent('s1', { graphInterruptId: 'gi-1' }, 'approval_waiting')

    expect(msg.streamState).toBe(StreamState.INTERRUPTED)
    expect(msg.isFailed).toBeUndefined()
  })

  it('批量审批（本地计算）：无匹配 sibling（无 approval / 批次 id 不一致）→ 不转换', async () => {
    const msg = buildAssistantMessage({
      streamState: StreamState.INTERRUPTED,
      toolCalls: [
        { id: 'tc-1' },
        { id: 'tc-2', approval: { graphInterruptId: 'gi-other', state: ApprovalState.APPROVED } },
      ],
    })
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [msg])])

    await handleApprovalEvent('s1', { graphInterruptId: 'gi-1' }, 'approval_approved')

    expect(msg.streamState).toBe(StreamState.INTERRUPTED)
    expect(msg.isFailed).toBeUndefined()
  })

  it('批量审批（权威计数）：remainingPendingCount > 0 → 保持 INTERRUPTED（本地状态不参与计算）', async () => {
    const msg = buildBatchMessage([ApprovalState.APPROVED])
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [msg])])

    await handleApprovalEvent('s1', { graphInterruptId: 'gi-1', remainingPendingCount: 2 }, 'approval_approved')

    expect(msg.streamState).toBe(StreamState.INTERRUPTED)
    expect(msg.isFailed).toBeUndefined()
  })

  it('批量审批（权威计数）：remainingPendingCount === 0 → 不做额外转换', async () => {
    const msg = buildBatchMessage([ApprovalState.APPROVED])
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [msg])])

    await handleApprovalEvent('s1', { graphInterruptId: 'gi-1', remainingPendingCount: 0 }, 'approval_approved')

    expect(msg.streamState).toBe(StreamState.INTERRUPTED)
    expect(msg.isFailed).toBeUndefined()
  })

  it('批量审批：消息非 INTERRUPTED 时不进入批量转换', async () => {
    const msg = buildBatchMessage([ApprovalState.APPROVED], {
      streamState: StreamState.COMPLETED,
      isStreaming: false,
    })
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [msg])])

    await handleApprovalEvent('s1', { graphInterruptId: 'gi-1' }, 'approval_approved')

    expect(msg.streamState).toBe(StreamState.COMPLETED)
  })

  it('批量审批：approval_pending 先置 INTERRUPTED，sibling 全 pending 时保持 INTERRUPTED', async () => {
    const msg = buildBatchMessage([ApprovalState.PENDING, ApprovalState.PENDING], {
      streamState: undefined,
    })
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [msg])])

    await handleApprovalEvent('s1', { graphInterruptId: 'gi-1' }, 'approval_pending')

    expect(msg.streamState).toBe(StreamState.INTERRUPTED)
    expect(msg.isStreaming).toBe(true)
    expect(msg.isFailed).toBeUndefined()
  })

  it('批量审批：graphInterruptId 从 payload.extra.graphInterruptId 解析', async () => {
    const msg = buildBatchMessage([ApprovalState.APPROVED])
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [msg])])

    await handleApprovalEvent('s1', { extra: { graphInterruptId: 'gi-1' } }, 'approval_approved')

    expect(msg.streamState).toBe(StreamState.STREAMING)
  })

  it('批量审批：sibling 的批次 id 支持 approval.extra.graphInterruptId 形式', async () => {
    const msg = buildAssistantMessage({
      streamState: StreamState.INTERRUPTED,
      toolCalls: [
        { id: 'tc-1', approval: { extra: { graphInterruptId: 'gi-1' }, state: ApprovalState.APPROVED } },
      ],
    })
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [msg])])

    await handleApprovalEvent('s1', { graphInterruptId: 'gi-1' }, 'approval_approved')

    expect(msg.streamState).toBe(StreamState.STREAMING)
  })

  // ── 7. 非批量已决事件 ──────────────────────────────────────────────────────
  it('非批量 approved/processing：INTERRUPTED → STREAMING', async () => {
    for (const eventType of ['approval_approved', 'approval_processing']) {
      const msg = buildAssistantMessage({ streamState: StreamState.INTERRUPTED, isStreaming: true })
      const { handleApprovalEvent } = buildHandler([buildSession('s1', [msg])])

      await handleApprovalEvent('s1', {}, eventType)

      expect(msg.streamState, eventType).toBe(StreamState.STREAMING)
    }
  })

  it('非批量：消息非 INTERRUPTED 时 approved 不改动状态', async () => {
    const msg = buildAssistantMessage({ streamState: StreamState.COMPLETED })
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [msg])])

    await handleApprovalEvent('s1', {}, 'approval_approved')

    expect(msg.streamState).toBe(StreamState.COMPLETED)
  })

  it('非批量 rejected/timeout/waiting：保持 INTERRUPTED 不转换', async () => {
    for (const eventType of ['approval_rejected', 'approval_timeout', 'approval_waiting']) {
      const msg = buildAssistantMessage({ streamState: StreamState.INTERRUPTED, isStreaming: true })
      const { handleApprovalEvent } = buildHandler([buildSession('s1', [msg])])

      await handleApprovalEvent('s1', {}, eventType)

      expect(msg.streamState, eventType).toBe(StreamState.INTERRUPTED)
      expect(msg.isFailed, eventType).toBeUndefined()
    }
  })

  // ── 8. 子代理审批联动 ──────────────────────────────────────────────────────
  it('子代理审批：pending/approved/rejected/timeout 触发 scheduleSubagentsRefresh（父线程取 sourceId）', async () => {
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [buildAssistantMessage()])])

    for (const eventType of ['approval_pending', 'approval_approved', 'approval_rejected', 'approval_timeout']) {
      await handleApprovalEvent('s1', { subagentThreadId: 'sa-1', sourceId: 'parent-1' }, eventType)
      expect(scheduleSubagentsRefresh).toHaveBeenCalledWith('parent-1')
    }

    expect(scheduleSubagentsRefresh).toHaveBeenCalledTimes(4)
  })

  it('子代理审批：processing/waiting 事件不触发刷新', async () => {
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [buildAssistantMessage()])])

    await handleApprovalEvent('s1', { subagentThreadId: 'sa-1', sourceId: 'parent-1' }, 'approval_processing')
    await handleApprovalEvent('s1', { subagentThreadId: 'sa-1', sourceId: 'parent-1' }, 'approval_waiting')

    expect(scheduleSubagentsRefresh).not.toHaveBeenCalled()
  })

  it('子代理审批：无 subagentThreadId 不触发刷新', async () => {
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [buildAssistantMessage()])])

    await handleApprovalEvent('s1', { sourceId: 'parent-1' }, 'approval_pending')

    expect(scheduleSubagentsRefresh).not.toHaveBeenCalled()
  })

  it('子代理审批：父线程 id 解析 payload.sourceId 优先，缺失时回退 chatSessionId', async () => {
    const { handleApprovalEvent } = buildHandler([buildSession('s1', [buildAssistantMessage()])])

    await handleApprovalEvent('s1', { subagentThreadId: 'sa-1' }, 'approval_pending')
    expect(scheduleSubagentsRefresh).toHaveBeenCalledWith('s1')

    await handleApprovalEvent('s1', { subagentThreadId: 'sa-2', sourceId: 'parent-2' }, 'approval_approved')
    expect(scheduleSubagentsRefresh).toHaveBeenLastCalledWith('parent-2')
  })

  it('子代理审批：taskId 路由（独立深研）时父线程 id 回退 taskId', async () => {
    const { handleApprovalEvent } = buildHandler([])

    await handleApprovalEvent(null, { subagentThreadId: 'sa-1' }, 'approval_pending', { taskId: 'task-9' })

    expect(scheduleSubagentsRefresh).toHaveBeenCalledWith('task-9')
  })
})
