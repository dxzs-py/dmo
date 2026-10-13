/**
 * 流式降级事件消费单测（E5-02：检索器注入失败用户可见降级提示）
 *
 * 被测：
 * - src/stores/sync/streamStateHandlers.js —— handleStreamEvent（degradation
 *   子类型 → ElNotification warning 提示；属"流继续"类事件，不改 streamState）
 *
 * mock 说明：element-plus（ElMessage/ElNotification）以 vi.mock 替换；logger
 * spyOn 抑制输出。payload 为 WS 命名边界 toCamelCase 后的前端视角（camelCase）。
 *
 * 运行方式（vitest）：npm run test
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest'
import { ElNotification } from 'element-plus'
import { logger } from '@/utils/logger'
import { StreamState } from '@/types'
import { createStreamStateHandlers } from '../streamStateHandlers.js'

vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn(), warning: vi.fn(), info: vi.fn(), success: vi.fn() },
  ElNotification: vi.fn(),
}))

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

const makeMessage = (overrides = {}) => ({
  id: 'm1',
  backendId: '42',
  role: 'assistant',
  content: '',
  streamState: StreamState.STREAMING,
  isStreaming: true,
  ...overrides,
})

const makeCtx = (message) => ({
  sessionStore: { sessions: [{ id: 's1', messages: [message] }] },
  approvalStore: {},
  researchStore: {},
  streamingSessions: new Set(),
  requestFullSync: vi.fn(),
  finalizeToolCalls: vi.fn(),
  verifyMessageIntegrity: vi.fn(),
})

describe('handleStreamEvent degradation 子类型（E5-02）', () => {
  it('degradation 事件 → ElNotification warning 提示，消息保持 STREAMING（流继续）', () => {
    const message = makeMessage()
    const ctx = makeCtx(message)
    const { handleStreamEvent } = createStreamStateHandlers(ctx)

    handleStreamEvent('s1', {
      messageId: '42',
      eventSubtype: 'degradation',
      data: { kbId: 'kb-a', message: '知识库「kb-a」暂时无法检索，本次回答将不使用该知识库内容' },
    })

    expect(ElNotification).toHaveBeenCalledTimes(1)
    expect(ElNotification).toHaveBeenCalledWith(
      expect.objectContaining({
        title: '知识库降级提示',
        type: 'warning',
        message: '知识库「kb-a」暂时无法检索，本次回答将不使用该知识库内容',
      })
    )
    // 流继续类事件：不改变消息状态、不打断渲染
    expect(message.streamState).toBe(StreamState.STREAMING)
    expect(message.isStreaming).toBe(true)
  })

  it('degradation 缺 message → 不弹窗（防御空帧）', () => {
    const message = makeMessage()
    const ctx = makeCtx(message)
    const { handleStreamEvent } = createStreamStateHandlers(ctx)

    handleStreamEvent('s1', { messageId: '42', eventSubtype: 'degradation', data: { kbId: 'kb-a' } })

    expect(ElNotification).not.toHaveBeenCalled()
    expect(message.streamState).toBe(StreamState.STREAMING)
  })

  it('未知子类型不弹窗（回归保护：仅 degradation 新增消费面）', () => {
    const message = makeMessage()
    const ctx = makeCtx(message)
    const { handleStreamEvent } = createStreamStateHandlers(ctx)

    handleStreamEvent('s1', { messageId: '42', eventSubtype: 'retry', data: { attempt: 1 } })

    expect(ElNotification).not.toHaveBeenCalled()
  })
})
