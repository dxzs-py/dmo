/**
 * sse.js / apiErrorHandler.js 单元测试（B1 Task 7 + D-8 回归）
 *
 * 覆盖：
 * - parseProtocolEvent：深层键转换、type 还原、subagent_thread_id 还原、
 *   非普通对象透传、数组逐项转换、无 type 字段不添加键
 * - extractSSEError：JSON body（message/error 优先级、errors 校验详情、data 详情）、
 *   SSE data 行 body、空 body、非 JSON 无 data 行回退 HTTP 状态描述
 * - readSSEStream（D-8）：多行 data 合并、事件空行分发、分包边界、
 *   [DONE] 关流（reader.cancel + 后续数据不再消费）、data: 无空格、尾部无空行兜底
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

import { parseProtocolEvent, readSSEStream } from '../sse.js'
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

// ============================================================
// readSSEStream（D-8：SSE 规范解析——多行 data 合并 / 空行分发 / [DONE] 关流）
// ============================================================

const encoder = new TextEncoder()

/**
 * 构造 readSSEStream 可消费的伪 response（仅依赖 body.getReader()）。
 * closeAfter=false 模拟服务端保持连接不关闭（验证 [DONE] 主动关流）。
 */
function makeStreamResponse(chunks, { closeAfter = true } = {}) {
  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk))
      }
      if (closeAfter) controller.close()
    },
  })
  const reader = stream.getReader()
  const cancelSpy = vi.spyOn(reader, 'cancel').mockResolvedValue(undefined)
  return { response: { body: { getReader: () => reader } }, cancelSpy }
}

describe('readSSEStream', () => {
  it('同一事件的多行 data: 按规范以 \\n 拼接后整体 JSON.parse', async () => {
    const { response } = makeStreamResponse(['data: {"final_report":\ndata: "line1\\nline2"}\n\n'])
    const events = []
    await readSSEStream(response, (e) => events.push(e))
    expect(events).toStrictEqual([{ finalReport: 'line1\nline2' }])
  })

  it('事件以空行分发：连续两个事件按序到达', async () => {
    const { response } = makeStreamResponse([
      'data: {"tool_call_id":"t1"}\n\n',
      'data: {"tool_call_id":"t2"}\n\n',
    ])
    const events = []
    await readSSEStream(response, (e) => events.push(e))
    expect(events).toStrictEqual([
      { toolCallId: 't1' },
      { toolCallId: 't2' },
    ])
  })

  it('分包边界：单个 JSON 事件跨两个 chunk 到达仍完整解析', async () => {
    const { response } = makeStreamResponse(['data: {"spl', 'it":"ok"}\n\n'])
    const events = []
    await readSSEStream(response, (e) => events.push(e))
    expect(events).toStrictEqual([{ split: 'ok' }])
  })

  it('[DONE] 后调用 reader.cancel 主动关流，且不再消费后续数据', async () => {
    const { response, cancelSpy } = makeStreamResponse(
      [
        'data: {"x_y":1}\n\n',
        'data: [DONE]\n\n',
        'data: {"after_done":true}\n\n',
      ],
      { closeAfter: false }
    )
    const events = []
    await readSSEStream(response, (e) => events.push(e))
    expect(cancelSpy).toHaveBeenCalledTimes(1)
    expect(events).toStrictEqual([{ xY: 1 }])
  })

  it('[DONE] 仅有换行无空行收尾（data 行已完整）同样关流', async () => {
    const { response, cancelSpy } = makeStreamResponse(
      ['data: {"a_b":1}\n\n', 'data: [DONE]\n'],
      { closeAfter: false }
    )
    const events = []
    await readSSEStream(response, (e) => events.push(e))
    expect(cancelSpy).toHaveBeenCalledTimes(1)
    expect(events).toStrictEqual([{ aB: 1 }])
  })

  it("data: 后无空格（规范允许）仍可解析", async () => {
    const { response } = makeStreamResponse(['data:{"no_space":true}\n\n'])
    const events = []
    await readSSEStream(response, (e) => events.push(e))
    expect(events).toStrictEqual([{ noSpace: true }])
  })

  it('流结束但最后一个事件无空行收尾：兜底分发', async () => {
    const { response } = makeStreamResponse(['data: {"tail":1}\n', 'data: {"tail":2}'])
    const events = []
    await readSSEStream(response, (e) => events.push(e))
    expect(events).toStrictEqual([{ tail: 1 }, { tail: 2 }])
  })

  it('非 data 行（注释/其他字段）被忽略', async () => {
    const { response } = makeStreamResponse([': keep-alive\nevent: message\ndata: {"ok":1}\n\n'])
    const events = []
    await readSSEStream(response, (e) => events.push(e))
    expect(events).toStrictEqual([{ ok: 1 }])
  })
})
