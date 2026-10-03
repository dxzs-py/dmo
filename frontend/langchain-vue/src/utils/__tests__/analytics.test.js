/**
 * analytics 埋点工具单元测试（Spec: fe-be-gap-fill / 埋点补全）
 *
 * 覆盖：
 * - trackFeatureUse / trackEvent 走裸 axios 通道（避开 apiClient 401 触发登出）
 * - 无 token 时不上报（避免 401 刷屏）
 * - 请求体经 toSnakeCase 转换（camelCase 源码 → snake_case 网络契约）
 * - 静默失败：axios.post reject 时调用方不抛出、异常被内部捕获
 *
 * 运行方式（vitest）：
 *   npx vitest run
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const axiosPostMock = vi.fn()
const useUserStoreMock = vi.fn()

vi.mock('axios', () => ({
  default: { post: (...args) => axiosPostMock(...args) },
}))

vi.mock('@/stores/user', () => ({
  useUserStore: (...args) => useUserStoreMock(...args),
}))

import { trackFeatureUse, trackEvent } from '../analytics.js'

/** 等待微任务队列清空（fire-and-forget 的 .catch 回调落地） */
const flushMicrotasks = () => new Promise((resolve) => setTimeout(resolve, 0))

beforeEach(() => {
  axiosPostMock.mockReset()
  useUserStoreMock.mockReset()
  useUserStoreMock.mockReturnValue({ token: 'test-token' })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('analytics trackFeatureUse', () => {
  it('携带 token 上报 feature-use，请求体键为 snake_case', () => {
    axiosPostMock.mockReturnValue(Promise.resolve({ data: {} }))

    trackFeatureUse('chat', { path: '/chat' })

    expect(axiosPostMock).toHaveBeenCalledTimes(1)
    const [url, body, config] = axiosPostMock.mock.calls[0]
    expect(url).toContain('/analytics/track/feature-use/')
    expect(body).toEqual({ feature: 'chat', metadata: { path: '/chat' } })
    expect(config.headers.Authorization).toBe('Bearer test-token')
  })

  it('无 token 时直接跳过上报', () => {
    useUserStoreMock.mockReturnValue({ token: '' })

    trackFeatureUse('chat')

    expect(axiosPostMock).not.toHaveBeenCalled()
  })

  it('feature 为空时不上报', () => {
    trackFeatureUse('')

    expect(axiosPostMock).not.toHaveBeenCalled()
  })

  it('上报请求 reject 时静默失败，不向调用方抛出', async () => {
    axiosPostMock.mockReturnValue(Promise.reject(new Error('network down')))

    expect(() => trackFeatureUse('chat')).not.toThrow()

    await flushMicrotasks()
  })

  it('请求体经 toSnakeCase 转换（camelCase meta 键 → snake_case）', () => {
    axiosPostMock.mockReturnValue(Promise.resolve({ data: {} }))

    trackEvent('cross_kb_search', { queryLength: 5, kbCount: 2 }, { category: 'knowledge' })

    const [url, body] = axiosPostMock.mock.calls[0]
    expect(url).toContain('/analytics/track/event/')
    expect(body).toEqual({
      event_type: 'cross_kb_search',
      event_category: 'knowledge',
      metadata: { query_length: 5, kb_count: 2 },
    })
  })

  it('trackEvent 缺省分类为 system', () => {
    axiosPostMock.mockReturnValue(Promise.resolve({ data: {} }))

    trackEvent('chat.session.create', { mode: 'agent' })

    const [, body] = axiosPostMock.mock.calls[0]
    expect(body.event_category).toBe('system')
    expect(body.event_type).toBe('chat.session.create')
  })
})
