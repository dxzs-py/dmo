/**
 * async.js（rafThrottle）单元测试（D-6 回归）
 *
 * rafThrottle 是 MarkdownRenderer 流式节流渲染的基础件：
 * - 窗口内高频调用合并为每帧最多一次
 * - 始终以最后一次调用的参数执行（尾帧语义 → 流结束终态正确）
 * - cancel() 取消未执行的尾帧（组件卸载防泄漏）
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { rafThrottle } from '../async.js'

// node 环境无 requestAnimationFrame：以 setTimeout(16) 模拟帧调度
let frameTimers
let nextFrameId

beforeEach(() => {
  vi.useFakeTimers()
  frameTimers = new Map()
  nextFrameId = 1
  vi.stubGlobal('requestAnimationFrame', (cb) => {
    const id = nextFrameId++
    frameTimers.set(id, setTimeout(() => {
      frameTimers.delete(id)
      cb(0)
    }, 16))
    return id
  })
  vi.stubGlobal('cancelAnimationFrame', (id) => {
    const timer = frameTimers.get(id)
    if (timer !== undefined) {
      clearTimeout(timer)
      frameTimers.delete(id)
    }
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('rafThrottle', () => {
  it('一帧内高频调用只执行一次，且携带最后一次调用的参数（尾帧语义）', () => {
    const fn = vi.fn()
    const throttled = rafThrottle(fn)

    throttled('a')
    throttled('b')
    throttled('c')

    expect(fn).not.toHaveBeenCalled()
    vi.advanceTimersByTime(16)
    expect(fn).toHaveBeenCalledTimes(1)
    expect(fn).toHaveBeenCalledWith('c')
  })

  it('cancel() 取消未执行的尾帧调用', () => {
    const fn = vi.fn()
    const throttled = rafThrottle(fn)

    throttled('x')
    throttled.cancel()
    vi.advanceTimersByTime(50)

    expect(fn).not.toHaveBeenCalled()
  })

  it('帧执行后可再次调度（逐帧持续渲染）', () => {
    const fn = vi.fn()
    const throttled = rafThrottle(fn)

    throttled(1)
    vi.advanceTimersByTime(16)
    throttled(2)
    vi.advanceTimersByTime(16)

    expect(fn).toHaveBeenCalledTimes(2)
    expect(fn).toHaveBeenNthCalledWith(1, 1)
    expect(fn).toHaveBeenNthCalledWith(2, 2)
  })

  it('cancel 后仍可重新调度新帧', () => {
    const fn = vi.fn()
    const throttled = rafThrottle(fn)

    throttled('drop')
    throttled.cancel()
    throttled('keep')
    vi.advanceTimersByTime(16)

    expect(fn).toHaveBeenCalledTimes(1)
    expect(fn).toHaveBeenCalledWith('keep')
  })
})
