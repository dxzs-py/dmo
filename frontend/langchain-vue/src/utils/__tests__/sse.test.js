/**
 * sse.js / apiErrorHandler.js 单元测试（B1 Task 7）
 *
 * 覆盖：
 * - parseProtocolEvent：深层键转换、type 还原、subagent_thread_id 还原、
 *   非普通对象透传、数组逐项转换、无 type 字段不添加键
 * - extractSSEError：JSON body（message/error 优先级、errors 校验详情、data 详情）、
 *   SSE data 行 body、空 body、非 JSON 无 data 行回退 HTTP 状态描述
 *
 * mock 说明：
 * - '@/stores/user'：sse.js 顶层静态引入（fetchSSE 鉴权头），其依赖链含
 *   @/router（createWebHistory 需 DOM，node 环境不可用），mock 断链
 * - 'element-plus'：apiErrorHandler 顶层引入 ElMessage/ElNotification，
 *   mock 避免组件库在 node 环境的加载开销与潜在 DOM 副作用
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect, vi } from 'vitest'

vi.mock('@/stores/user', () => ({
  useUserStore: () => ({ token: '', refreshToken: '' }),
}))

vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
  ElNotification: Object.assign(vi.fn(), { error: vi.fn() }),
}))

import { parseProtocolEvent } from '../sse.js'
import { extractSSEError } from '../apiErrorHandler.js'

describe('parseProtocolEvent', () => {
  it('深层键转换 + type 还原为后端原始 snake_case', () => {
    const raw = {
      type: 'status_change',
      final_report: 'report.md',
      data: { current_step: 3, agent_name: 'bob' },
    }
    const out = parseProtocolEvent(raw)
    expect(out).toStrictEqual({
      type: 'status_change',
      finalReport: 'report.md',
      data: { currentStep: 3, agentName: 'bob' },
    })
  })

  it('顶层 subagent_thread_id 还原为原始 snake_case（协议路由标识符）', () => {
    const raw = {
      type: 'tool_call_completed',
      subagent_thread_id: 'subagent_abc',
      tool_call_id: 't1',
    }
    const out = parseProtocolEvent(raw)
    expect(out.type).toBe('tool_call_completed')
    expect(out.subagent_thread_id).toBe('subagent_abc')
    expect(out.toolCallId).toBe('t1')
  })

  it('非普通对象透传：null / undefined / 原始值原样返回', () => {
    expect(parseProtocolEvent(null)).toBeNull()
    expect(parseProtocolEvent(undefined)).toBeUndefined()
    expect(parseProtocolEvent('plain')).toBe('plain')
    expect(parseProtocolEvent(42)).toBe(42)
  })

  it('数组逐项转换（SSE data 行可能为数组结构）', () => {
    const out = parseProtocolEvent([{ a_b: 1 }, { c_d: 'x' }])
    expect(out).toStrictEqual([{ aB: 1 }, { cD: 'x' }])
  })

  it('无 type / subagent_thread_id 字段时不添加对应键', () => {
    const out = parseProtocolEvent({ final_report: 'r' })
    expect(out).toStrictEqual({ finalReport: 'r' })
    expect('type' in out).toBe(false)
    expect('subagent_thread_id' in out).toBe(false)
  })
})

describe('extractSSEError', () => {
  const makeResponse = (body, status = 500) => new Response(body, { status })

  it('JSON body：message 提取', async () => {
    const err = await extractSSEError(makeResponse('{"message":"查询失败"}'))
    expect(err).toBeInstanceOf(Error)
    expect(err.message).toBe('查询失败')
  })

  it('JSON body：message 优先于 error', async () => {
    const err = await extractSSEError(makeResponse('{"error":"后端错误","message":"业务提示"}'))
    expect(err.message).toBe('业务提示')
  })

  it('JSON body：无 message 时提取 error', async () => {
    const err = await extractSSEError(makeResponse('{"error":"后端错误"}'))
    expect(err.message).toBe('后端错误')
  })

  it('JSON body：errors 校验详情追加 (...)', async () => {
    const err = await extractSSEError(
      makeResponse('{"message":"验证失败","errors":{"query":["必填","过长"]}}')
    )
    expect(err.message).toBe('验证失败 (query: 必填, 过长)')
  })

  it('JSON body：data 对象详情追加 [...]', async () => {
    const err = await extractSSEError(
      makeResponse('{"message":"失败","data":{"detail":"索引不存在"}}')
    )
    expect(err.message).toBe('失败 [索引不存在]')
  })

  it('SSE data 行 body：回退路径提取（body 只读一次的根因修复验证）', async () => {
    const err = await extractSSEError(makeResponse('data: {"message":"SSE 流错误"}\n\n', 502))
    expect(err.message).toBe('SSE 流错误')
  })

  it('空 body：回退默认 HTTP 状态描述', async () => {
    const err = await extractSSEError(makeResponse('', 404))
    expect(err.message).toBe('HTTP 404')
  })

  it('非 JSON 且无 data 行：回退默认 HTTP 状态描述', async () => {
    const err = await extractSSEError(makeResponse('Internal Server Error', 500))
    expect(err.message).toBe('HTTP 500')
  })

  it('JSON body 读取失败（body 已消费）：回退默认 HTTP 状态描述', async () => {
    const response = makeResponse('{"message":"已消费"}')
    await response.text() // 预先消费 body
    const err = await extractSSEError(response)
    expect(err.message).toBe('HTTP 500')
  })
})
