import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('@/utils/logger', () => ({
  logger: {
    log: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
    group: vi.fn(),
    groupEnd: vi.fn(),
  },
}))

import { logger } from '@/utils/logger'
import { setupErrorHandler, teardownErrorHandler } from '../useErrorHandler'

/** window 桩：记录 add/removeEventListener 并支持手动派发（node 环境无真实 window） */
const createWindowStub = () => {
  const listeners = { unhandledrejection: [], error: [] }
  return {
    location: { href: 'http://localhost:5173/' },
    addEventListener: vi.fn((type, handler) => { listeners[type]?.push(handler) }),
    removeEventListener: vi.fn((type, handler) => {
      const arr = listeners[type]
      if (!arr) return
      const idx = arr.indexOf(handler)
      if (idx >= 0) arr.splice(idx, 1)
    }),
    dispatch(type, event) {
      // 拷贝数组：处理期间不会因 remove 而漏触发/错触发
      ;[...listeners[type]].forEach((handler) => handler(event))
    },
    listenerCount(type) {
      return listeners[type].length
    },
  }
}

let windowStub

const setup = () => {
  const app = { config: {} }
  const cleanup = setupErrorHandler(app)
  return { app, cleanup }
}

beforeEach(() => {
  vi.clearAllMocks()
  windowStub = createWindowStub()
  vi.stubGlobal('window', windowStub)
})

afterEach(() => {
  teardownErrorHandler()
  vi.unstubAllGlobals()
})

describe('setupErrorHandler - 注册 / 防重复 / 对称清理', () => {
  it('注册 unhandledrejection 与 error 各一个 window 监听', () => {
    setup()
    expect(windowStub.listenerCount('unhandledrejection')).toBe(1)
    expect(windowStub.listenerCount('error')).toBe(1)
  })

  it('重复调用不堆叠监听（防重复注册）', () => {
    setup()
    setup()
    setup()
    expect(windowStub.listenerCount('unhandledrejection')).toBe(1)
    expect(windowStub.listenerCount('error')).toBe(1)
  })

  it('返回清理函数且与 teardownErrorHandler 等效', () => {
    const { cleanup } = setup()
    expect(typeof cleanup).toBe('function')
    cleanup()
    expect(windowStub.listenerCount('unhandledrejection')).toBe(0)
    expect(windowStub.listenerCount('error')).toBe(0)
  })

  it('teardownErrorHandler 幂等：重复调用不抛错', () => {
    setup()
    teardownErrorHandler()
    expect(() => teardownErrorHandler()).not.toThrow()
    expect(windowStub.listenerCount('unhandledrejection')).toBe(0)
  })

  it('teardown 后重新 setup 可再次注册（对称）', () => {
    setup()
    teardownErrorHandler()
    setup()
    expect(windowStub.listenerCount('unhandledrejection')).toBe(1)
    expect(windowStub.listenerCount('error')).toBe(1)
  })

  it('teardown 后派发事件不再被处理', () => {
    setup()
    teardownErrorHandler()
    windowStub.dispatch('unhandledrejection', { reason: new Error('after teardown') })
    expect(logger.error).not.toHaveBeenCalled()
  })
})

describe('window 监听行为', () => {
  it('unhandledrejection：reason 记录到 logger.error', () => {
    setup()
    const reason = new Error('promise boom')
    windowStub.dispatch('unhandledrejection', { reason })
    expect(logger.error).toHaveBeenCalledWith('❌ [Unhandled Promise Rejection]', reason)
  })

  it('error：普通运行时错误记录到 logger.error', () => {
    setup()
    const error = new Error('runtime boom')
    windowStub.dispatch('error', { error })
    expect(logger.error).toHaveBeenCalledWith('❌ [Runtime Error]', error)
  })

  it('error：浏览器扩展错误降级 logger.debug 并 preventDefault', () => {
    setup()
    const event = {
      filename: 'chrome-extension://abc/content.js',
      error: new Error('runtime.lastError'),
      preventDefault: vi.fn(),
    }
    windowStub.dispatch('error', event)
    expect(logger.debug).toHaveBeenCalled()
    expect(event.preventDefault).toHaveBeenCalled()
    expect(logger.error).not.toHaveBeenCalled()
  })
})
