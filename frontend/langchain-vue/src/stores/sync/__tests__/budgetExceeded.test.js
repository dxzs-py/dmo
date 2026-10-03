/**
 * budget_exceeded 消费单测（spec D6：事件断链双向补全——前端补消费）
 *
 * 被测：
 * - src/stores/sync/streamStateHandlers.js —— handleBudgetExceeded
 *   （ElNotification.warning 提示，payload 含 usedTokens/budgetLimit 细节）
 * - src/stores/sync/handleSessionEvent.js —— applySessionEvent 路由
 *   （budget_exceeded case 委托 ctx.handleBudgetExceeded）
 *
 * mock 说明：element-plus（ElNotification）以 vi.mock 替换；logger spyOn 抑制输出；
 * handleSessionEvent 的 ctx 以纯对象 + vi.fn() 注入（orderedQueue 直通、seqDedup 恒 0）。
 *
 * 运行方式（vitest）：npm run test
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest'
import { ElNotification } from 'element-plus'
import { logger } from '@/utils/logger'
import { createStreamStateHandlers } from '../streamStateHandlers.js'
import { createHandleSessionEvent } from '../handleSessionEvent.js'

vi.mock('element-plus', () => ({
  ElNotification: vi.fn(),
}))

// 与 approvalHandler.test.js 同款：斩断 useSubagents → api/axios → stores/user →
// router 的模块级依赖链（node 环境无 window，router 创建会抛异常）
vi.mock('@/composables/useSubagents', () => ({
  scheduleSubagentsRefresh: vi.fn(),
}))

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

// 后端 loop.py 发布 payload（WS 命名边界 toCamelCase 后的前端视角）
const buildBudgetPayload = (overrides = {}) => ({
  source: 'chat',
  sourceId: 's1',
  sessionId: 's1',
  data: { sessionId: 's1', usedTokens: 12000, budgetLimit: 12000 },
  ...overrides,
})

describe('handleBudgetExceeded（streamStateHandlers）', () => {
  it('弹出 warning 通知，含预算上限细节字段', () => {
    const { handleBudgetExceeded } = createStreamStateHandlers({})

    handleBudgetExceeded('s1', buildBudgetPayload())

    expect(ElNotification).toHaveBeenCalledTimes(1)
    const arg = ElNotification.mock.calls[0][0]
    expect(arg.type).toBe('warning')
    expect(arg.title).toBe('Token 预算已达上限')
    expect(arg.message).toContain('已用 12000 / 上限 12000 tokens')
  })

  it('payload 缺少计数细节时仍弹出通用提示（不抛异常）', () => {
    const { handleBudgetExceeded } = createStreamStateHandlers({})

    handleBudgetExceeded('s1', { data: null })

    expect(ElNotification).toHaveBeenCalledTimes(1)
    const arg = ElNotification.mock.calls[0][0]
    expect(arg.type).toBe('warning')
    expect(arg.message).not.toContain('undefined')
    expect(arg.message).toContain('Token 预算已耗尽')
  })
})

describe('applySessionEvent 路由 budget_exceeded（handleSessionEvent）', () => {
  const buildCtx = (overrides = {}) => ({
    sessionStore: { sessions: [{ id: 's1' }] },
    approvalStore: {},
    researchStore: {},
    seqDedup: { getPrevSeq: () => 0, setSeenSeq: () => {} },
    orderedQueue: {
      process: async (_sessionId, event, apply) => apply(event),
    },
    fullSyncPending: new Set(),
    requestFullSync: vi.fn(),
    handleBudgetExceeded: vi.fn(),
    ...overrides,
  })

  it('budget_exceeded 事件路由到 ctx.handleBudgetExceeded', async () => {
    const ctx = buildCtx()
    const { applySessionEvent } = createHandleSessionEvent(ctx)

    await applySessionEvent({ type: 'budget_exceeded', seq: 1, payload: buildBudgetPayload() })

    expect(ctx.handleBudgetExceeded).toHaveBeenCalledTimes(1)
    expect(ctx.handleBudgetExceeded).toHaveBeenCalledWith('s1', buildBudgetPayload())
  })
})
