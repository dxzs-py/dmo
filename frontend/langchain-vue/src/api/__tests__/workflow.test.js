/**
 * api/workflow.js rawSSEFetch 薄封装单元测试（组11 收敛）
 *
 * 覆盖：
 * - 请求头归一：Accept + Authorization 注入；有 body 时追加 Content-Type 并
 *   camelCase → snake_case 序列化；无 body 端点不携带 Content-Type
 * - streamFetchRaw：token 走 query 参数（encodeURIComponent）
 * - signal 不透传（Vite proxy 缓冲绕行契约）
 * - 错误路径：fetch 拒绝原样向上传播（不捕获归一）
 *
 * mock 说明：
 * - '../axios'：阻断 stores/loading、element-plus 等重依赖链（本测试不覆盖 apiClient）
 * - '@/stores/user'：仅提供 token
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const state = vi.hoisted(() => ({ token: 'test-token' }))

vi.mock('../axios', () => ({ apiClient: {} }))

vi.mock('@/stores/user', () => ({
  useUserStore: () => ({ token: state.token }),
}))

import { workflowAPI } from '../workflow.js'

describe('workflowAPI rawSSEFetch', () => {
  let fetchMock

  beforeEach(() => {
    fetchMock = vi.fn(() => Promise.resolve(new Response()))
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('请求头归一：有 body 补 Content-Type 并 snake_case 序列化；无 body 不带 Content-Type；signal 不透传', async () => {
    await workflowAPI.startStreamRaw({ threadId: 't1', userAnswer: 'a' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    let [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/learning/start/stream/')
    expect(options.method).toBe('POST')
    expect(options.headers).toStrictEqual({
      Accept: 'text/event-stream',
      Authorization: 'Bearer test-token',
      'Content-Type': 'application/json',
    })
    expect(JSON.parse(options.body)).toStrictEqual({ thread_id: 't1', user_answer: 'a' })
    expect(options).not.toHaveProperty('signal')

    await workflowAPI.restartStreamRaw('th1')
    ;[url, options] = fetchMock.mock.calls[1]
    expect(url).toBe('/api/v1/learning/th1/restart/stream/')
    expect(options.method).toBe('POST')
    expect(options.headers).toStrictEqual({
      Accept: 'text/event-stream',
      Authorization: 'Bearer test-token',
    })

    await workflowAPI.streamFetchRaw('th1')
    ;[url, options] = fetchMock.mock.calls[2]
    // 令牌走 Authorization 头，**不再**进查询串（避免进入浏览器历史 / Referer / 访问日志）
    expect(url).toBe('/api/v1/learning/stream/th1/')
    expect(url).not.toContain('token')
    expect(options.method).toBe('GET')
    expect(options.headers).toStrictEqual({
      Accept: 'text/event-stream',
      Authorization: 'Bearer test-token',
    })
  })

  it('错误路径：fetch 拒绝原样向上传播，不做捕获归一', async () => {
    const boom = new Error('network down')
    fetchMock.mockRejectedValue(boom)
    await expect(workflowAPI.startStreamRaw({})).rejects.toBe(boom)
    await expect(workflowAPI.streamFetchRaw('th1')).rejects.toBe(boom)
  })
})
