/**
 * useSSEStream 单元测试（SSE 骨架抽取批次）
 *
 * 覆盖：
 * - 正常流：事件按序派发（parseProtocolEvent 转换后）、active 标志生命周期
 * - 中途 abort：读流以 AbortError 终止，返回 aborted，isActive 复位
 * - abort 后同批事件守卫：onEvent 内 abort 时，同 chunk 后续事件被丢弃
 * - 服务端错误：!ok 响应经 extractSSEError 提取，返回 error
 * - resolveOnBadResponse：!ok 响应按自然结束返回（审批监听场景）
 *
 * mock 说明：
 * - requestFn 返回普通对象 { ok, body }（readSSEStream 仅消费 body.getReader()，
 *   无需真实 Response）；!ok 场景补充 status / text
 * - '@/utils/sse' 不 mock：走真实 readSSEStream 读流循环与 parseProtocolEvent 转换
 * - '@/stores/user'：@/utils/sse 顶层静态引入（fetchSSE 鉴权头），其依赖链含
 *   @/router（createWebHistory 需 DOM，node 环境不可用），mock 断链
 * - 'element-plus'：apiErrorHandler 顶层引入，mock 断链避免组件库副作用
 */
import { describe, it, expect, vi } from 'vitest'

vi.mock('@/stores/user', () => ({
  useUserStore: () => ({ token: '', refreshToken: '' }),
}))

vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
  ElNotification: Object.assign(vi.fn(), { error: vi.fn() }),
}))

import { useSSEStream } from '../useSSEStream.js'

const encoder = new TextEncoder()

/** 构造手动控制的 ReadableStream + 普通对象 SSE 响应 */
function createManualSSEResponse({ ok = true, status = 200 } = {}) {
  let streamController = null
  const stream = new ReadableStream({
    start(controller) {
      streamController = controller
    },
  })
  return {
    response: {
      ok,
      status,
      body: stream,
      text: async () => '',
    },
    streamController,
  }
}

/** 构造一次性发完即关闭的 SSE 响应 */
function createAutoSSEResponse(rawChunks, { ok = true, status = 200 } = {}) {
  const stream = new ReadableStream({
    start(controller) {
      rawChunks.forEach(chunk => controller.enqueue(encoder.encode(chunk)))
      controller.close()
    },
  })
  return { ok, status, body: stream, text: async () => '' }
}

/** 构造 AbortError（name 判定用，与浏览器 fetch 行为一致） */
function createAbortError() {
  const err = new Error('The operation was aborted')
  err.name = 'AbortError'
  return err
}

/** 等待微任务与定时器队列清空（首块数据读取与事件派发完成） */
const flushAsync = () => new Promise(resolve => setTimeout(resolve, 0))

describe('useSSEStream - 正常流', () => {
  it('事件按序派发（经 parseProtocolEvent 转 camelCase），结束后 active=true 且 isActive 复位', async () => {
    const events = []
    const response = createAutoSSEResponse([
      'data: {"type":"status_change","status":"running","agent_name":"bob"}\n\n',
      'data: {"type":"done","final_message":"ok"}\n\n',
    ])
    const { start, isActive } = useSSEStream({ onEvent: (e) => events.push(e) })

    const promise = start(() => Promise.resolve(response))
    // 中途 isActive=true 由手动流用例断言（自动关闭流在 flush 前已读完）

    const result = await promise

    expect(events).toEqual([
      { type: 'status_change', status: 'running', agentName: 'bob' },
      { type: 'done', finalMessage: 'ok' },
    ])
    expect(result).toEqual({ status: 'completed', error: null, active: true })
    expect(isActive.value).toBe(false)
  })

  it('requestFn 收到 AbortSignal，且事件守卫生效（abort 后事件不派发）', async () => {
    const events = []
    const { response, streamController } = createManualSSEResponse()
    streamController.enqueue(encoder.encode('data: {"type":"first"}\n\n'))

    const { start, abort, isActive } = useSSEStream({ onEvent: (e) => events.push(e) })
    const requestFn = vi.fn(() => Promise.resolve(response))
    const promise = start(requestFn)

    await flushAsync()
    expect(events).toEqual([{ type: 'first' }])
    expect(requestFn).toHaveBeenCalledWith(expect.any(AbortSignal))

    abort()
    expect(isActive.value).toBe(false)

    // 模拟 abort 导致挂起的 read() 以 AbortError 拒绝（浏览器流真实行为）
    streamController.error(createAbortError())
    const result = await promise

    expect(result.status).toBe('aborted')
    expect(isActive.value).toBe(false)
  })
})

describe('useSSEStream - 事件守卫', () => {
  it('onEvent 内 abort：同 chunk 后续事件被丢弃，流以 completed + active=false 结束', async () => {
    const events = []
    const response = createAutoSSEResponse([
      'data: {"type":"a"}\n\ndata: {"type":"b"}\n\n',
    ])
    // 首个事件触发 abort（对齐 status_change 终态 / done 事件的主动关闭语义）
    const { start, abort } = useSSEStream({
      onEvent: (e) => {
        events.push(e)
        if (e.type === 'a') abort()
      },
    })

    const result = await start(() => Promise.resolve(response))

    // b 事件被活跃守卫丢弃（原骨架 `if (!sseReaderActive) return` 语义）
    expect(events).toEqual([{ type: 'a' }])
    expect(result.status).toBe('completed')
    expect(result.active).toBe(false)
  })
})

describe('useSSEStream - 错误处理', () => {
  it('!ok 响应：经 extractSSEError 提取错误信息，返回 error', async () => {
    const events = []
    const response = {
      ok: false,
      status: 500,
      body: null,
      text: async () => '{"message":"查询失败"}',
    }
    const { start, isActive } = useSSEStream({ onEvent: (e) => events.push(e) })

    const result = await start(() => Promise.resolve(response))

    expect(result.status).toBe('error')
    expect(result.error).toBeInstanceOf(Error)
    expect(result.error.message).toBe('查询失败')
    expect(events).toEqual([])
    expect(isActive.value).toBe(false)
  })

  it('requestFn 网络错误：错误对象原样透传为 error', async () => {
    const networkError = new TypeError('Failed to fetch')
    const { start } = useSSEStream({ onEvent: vi.fn() })

    const result = await start(() => Promise.reject(networkError))

    expect(result.status).toBe('error')
    expect(result.error).toBe(networkError)
  })
})

describe('useSSEStream - resolveOnBadResponse', () => {
  it('!ok 响应按自然结束返回（审批监听场景：静默退出不触发重连）', async () => {
    const text = vi.fn(async () => '{"message":"不应被读取"}')
    const response = { ok: false, status: 404, body: null, text }
    const onEvent = vi.fn()
    const { start } = useSSEStream({ onEvent, resolveOnBadResponse: true })

    const result = await start(() => Promise.resolve(response))

    expect(result).toEqual({ status: 'completed', error: null, active: true })
    expect(text).not.toHaveBeenCalled()
    expect(onEvent).not.toHaveBeenCalled()
  })
})
