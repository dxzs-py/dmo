/**
 * 流式错误事件消费单测（整改 A1.5：审计 F1-1 前端补消费）
 *
 * 被测：
 * - src/stores/sync/streamStateHandlers.js —— handleStreamCompleted
 *   （失败终态 payload.data.success===false + error/errorCode → 消息 ERROR
 *   终态 + ElMessage.error 提示）
 * - src/stores/sync/streamStateHandlers.js —— handleStreamEvent（error 子类型
 *   流内事件 → 消息 ERROR 终态 + 错误文案落 content，不弹窗防双提示）
 *
 * mock 说明：element-plus（ElMessage/ElNotification）以 vi.mock 替换；logger
 * spyOn 抑制输出。payload 为 WS 命名边界 toCamelCase 后的前端视角。
 *
 * 运行方式（vitest）：npm run test
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest'
import { ElMessage, ElNotification } from 'element-plus'
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

// 后端 chat_executor_core 失败终态 payload（toCamelCase 后前端视角）
const buildFailurePayload = (overrides = {}) => ({
  messageId: '42',
  data: { success: false, stopped: false, incomplete: false, error: '请求超时，请稍后重试', errorCode: 'MODEL_CALL_ERROR' },
  ...overrides,
})

describe('handleStreamCompleted 失败终态（A1.5）', () => {
  it('success=false + error → 消息置 ERROR 终态并弹出错误提示', () => {
    const message = makeMessage()
    const ctx = makeCtx(message)
    const { handleStreamCompleted } = createStreamStateHandlers(ctx)

    handleStreamCompleted('s1', buildFailurePayload())

    expect(message.streamState).toBe(StreamState.ERROR)
    expect(message.isStreaming).toBe(false)
    expect(message.content).toBe('执行失败：请求超时，请稍后重试')
    expect(ElMessage.error).toHaveBeenCalledTimes(1)
    expect(ElMessage.error).toHaveBeenCalledWith('请求超时，请稍后重试')
  })

  it('已有部分输出内容时保留正文，仅标记 ERROR（不覆盖已生成内容）', () => {
    const message = makeMessage({ content: '已输出的部分回答' })
    const ctx = makeCtx(message)
    const { handleStreamCompleted } = createStreamStateHandlers(ctx)

    handleStreamCompleted('s1', buildFailurePayload())

    expect(message.content).toBe('已输出的部分回答')
    expect(message.streamState).toBe(StreamState.ERROR)
  })

  it('success=true 时走正常终态路径，不弹错误提示（回归保护）', () => {
    const message = makeMessage()
    const ctx = makeCtx(message)
    const { handleStreamCompleted } = createStreamStateHandlers(ctx)

    handleStreamCompleted('s1', { messageId: '42', data: { success: true, stopped: false, incomplete: false } })

    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(message.streamState).not.toBe(StreamState.ERROR)
  })

  it('用户停止（success=true + stopped=true）不触发失败分支（现状保持）', () => {
    const message = makeMessage()
    const ctx = makeCtx(message)
    const { handleStreamCompleted } = createStreamStateHandlers(ctx)

    handleStreamCompleted('s1', { messageId: '42', data: { success: true, stopped: true, incomplete: false } })

    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(message.stopped).toBe(true)
  })
})

describe('handleStreamEvent error 子类型（A1.5）', () => {
  it('流内 error 事件 → 消息标记 ERROR + content 落错误文案，不弹窗（防双提示）', () => {
    const message = makeMessage()
    const ctx = makeCtx(message)
    const { handleStreamEvent } = createStreamStateHandlers(ctx)

    handleStreamEvent('s1', {
      messageId: '42',
      eventSubtype: 'error',
      data: { code: 'MODEL_CALL_ERROR', message: '模型服务暂时不可用，请稍后重试或切换模型' },
    })

    expect(message.streamState).toBe(StreamState.ERROR)
    expect(message.isStreaming).toBe(false)
    expect(message.content).toBe('执行失败：模型服务暂时不可用，请稍后重试或切换模型')
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(ElNotification).not.toHaveBeenCalled()
  })

  it('非 error 子类型（model_fallback）不受新分支影响（回归保护）', () => {
    const message = makeMessage()
    const ctx = makeCtx(message)
    const { handleStreamEvent } = createStreamStateHandlers(ctx)

    handleStreamEvent('s1', {
      messageId: '42',
      eventSubtype: 'model_fallback',
      data: { message: '主模型不可用，已降级' },
    })

    expect(message.streamState).toBe(StreamState.STREAMING)
    expect(ElNotification).toHaveBeenCalledTimes(1)
  })
})
