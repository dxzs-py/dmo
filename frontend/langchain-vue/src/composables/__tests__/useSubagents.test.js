/**
 * useSubagents 单元测试（spec Task 2：服务端单请求全树查询，消除 429 请求放大）
 *
 * 被测：src/composables/useSubagents.js —— _fetchSubagents 单请求契约。
 * getSubagents 通过 vi.mock('@/api/subagent') 替换；logger spyOn 抑制输出。
 *
 * 覆盖分支：
 * 1. fetchSubagents 单请求：getSubagents 仅调用一次且携带 recursive=true
 *    （旧实现客户端递归逐层拉取：每个 parent_thread_id 一次请求，嵌套场景
 *    单次刷新 1+N+M 请求 × 4 浏览器并发 → 打满 user 限流 200/min → 429）
 * 2. 服务端全树响应（含嵌套后代）直接合并进 subagentsMetaMap，无二次请求
 * 3. 响应异常/空结构：返回 [] 且缓存不污染
 * 4. 请求失败：logger.warn + 返回 []，不抛出
 * 5. scheduleSubagentsRefresh 去抖：同批次多次调度合并为单请求
 *
 * 运行方式（frontend/langchain-vue 目录）：
 *   npx vitest run src/composables/__tests__/useSubagents.test.js
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { logger } from '@/utils/logger'

vi.mock('@/api/subagent', () => ({
  getSubagents: vi.fn(),
}))

import { getSubagents } from '@/api/subagent'
import { subagentsMetaMap, useSubagents, scheduleSubagentsRefresh } from '../useSubagents.js'

// 三层嵌套树：root → B1/B2 → C1（服务端 recursive=true 单请求返回全树）
const FULL_TREE = [
  { threadId: 'sub-b1', parentThreadId: 'session-root', status: 'running', depth: 1 },
  { threadId: 'sub-b2', parentThreadId: 'session-root', status: 'done', depth: 1 },
  { threadId: 'sub-c1', parentThreadId: 'sub-b1', status: 'running', depth: 2 },
]

const okResponse = (subagents) => ({ data: { data: { subagents } } })

let loggerSpies = []

beforeEach(() => {
  vi.clearAllMocks()
  subagentsMetaMap.value = {}
  loggerSpies = []
  for (const method of ['log', 'info', 'warn', 'error', 'debug']) {
    loggerSpies.push(vi.spyOn(logger, method).mockImplementation(() => {}))
  }
})

afterEach(() => {
  loggerSpies.forEach(spy => spy.mockRestore())
  vi.useRealTimers()
})

describe('useSubagents - 单请求全树拉取（Task 2 契约）', () => {
  it('fetchSubagents 只发起一次请求且携带 recursive=true', async () => {
    getSubagents.mockResolvedValue(okResponse(FULL_TREE))
    const { fetchSubagents } = useSubagents()

    const list = await fetchSubagents('session-root')

    expect(getSubagents).toHaveBeenCalledTimes(1)
    expect(getSubagents).toHaveBeenCalledWith('session-root', true)
    expect(list).toHaveLength(3)
  })

  it('嵌套后代单请求全量入缓存，不再按层递归请求', async () => {
    getSubagents.mockResolvedValue(okResponse(FULL_TREE))
    const { fetchSubagents } = useSubagents()

    await fetchSubagents('session-root')

    // 全树（含 depth=2 嵌套后代 sub-c1）均合并进缓存
    expect(Object.keys(subagentsMetaMap.value).sort()).toEqual(['sub-b1', 'sub-b2', 'sub-c1'])
    // 请求次数恒为 1：旧实现会再对 sub-b1/sub-b2 发起递归请求（请求放大根因）
    expect(getSubagents).toHaveBeenCalledTimes(1)
  })

  it('响应缺少 subagents 结构：返回 [] 且缓存保持为空', async () => {
    getSubagents.mockResolvedValue({ data: {} })
    const { fetchSubagents } = useSubagents()

    const list = await fetchSubagents('session-root')

    expect(list).toEqual([])
    expect(subagentsMetaMap.value).toEqual({})
    expect(getSubagents).toHaveBeenCalledTimes(1)
  })

  it('请求失败：logger.warn + 返回 []，不向上抛出', async () => {
    getSubagents.mockRejectedValue(new Error('boom'))
    const { fetchSubagents } = useSubagents()

    const list = await fetchSubagents('session-root')

    expect(list).toEqual([])
    expect(logger.warn).toHaveBeenCalledTimes(1)
    expect(subagentsMetaMap.value).toEqual({})
  })

  it('parentThreadId 为空：不发请求直接返回 []', async () => {
    const { fetchSubagents } = useSubagents()

    const list = await fetchSubagents('')

    expect(list).toEqual([])
    expect(getSubagents).not.toHaveBeenCalled()
  })

  it('getSubagentsByMessage 按 assistantMessageId 过滤全树缓存', async () => {
    getSubagents.mockResolvedValue(
      okResponse([
        { threadId: 'sub-b1', assistantMessageId: 'msg-1' },
        { threadId: 'sub-c1', assistantMessageId: 'msg-2' },
        { threadId: 'sub-b2', assistantMessageId: 'msg-1' },
      ]),
    )
    const { fetchSubagents, getSubagentsByMessage } = useSubagents()

    await fetchSubagents('session-root')
    const matched = getSubagentsByMessage('msg-1')

    expect(matched.map(sa => sa.threadId).sort()).toEqual(['sub-b1', 'sub-b2'])
  })
})

describe('scheduleSubagentsRefresh - 去抖合并单请求', () => {
  it('去抖窗口内多次调度合并为一次 fetch（单请求）', async () => {
    vi.useFakeTimers()
    getSubagents.mockResolvedValue(okResponse(FULL_TREE))

    scheduleSubagentsRefresh('session-root')
    scheduleSubagentsRefresh('session-root')
    scheduleSubagentsRefresh('session-root')
    await vi.advanceTimersByTimeAsync(800)

    expect(getSubagents).toHaveBeenCalledTimes(1)
    expect(getSubagents).toHaveBeenCalledWith('session-root', true)
    expect(Object.keys(subagentsMetaMap.value)).toHaveLength(3)
  })

  it('去抖窗口内不同父线程各合并为一次请求', async () => {
    vi.useFakeTimers()
    getSubagents.mockResolvedValue(okResponse([]))

    scheduleSubagentsRefresh('session-a')
    scheduleSubagentsRefresh('session-a')
    scheduleSubagentsRefresh('session-b')
    await vi.advanceTimersByTimeAsync(800)

    expect(getSubagents).toHaveBeenCalledTimes(2)
    expect(getSubagents).toHaveBeenNthCalledWith(1, 'session-a', true)
    expect(getSubagents).toHaveBeenNthCalledWith(2, 'session-b', true)
  })
})
