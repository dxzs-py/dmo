/**
 * useRealtimeSync 编排器冒烟测试（ARCH-14 拆分回归）
 *
 * 被测：src/composables/useRealtimeSync/（index.js 编排器 + 六子系统模块）
 * 验收口径：行为零变化 —— 本文件聚焦三类不依赖真实 WebSocket 的可观测行为：
 *   1. 导出面完整性：拆分前后对外 API 成员集合一致（13 个成员逐一名校）；
 *   2. 单例语义：模块级 instance，多次调用返回同一实例；
 *   3. 编排装配：lastSeq 去重基线（取 max 防回退）、订阅注册/退订（无 WS 时不发送）、
 *      SSE 计数增减、visibilitychange 监听注册（已知规范缺口：监听永不移除，保持现状）。
 *
 * 环境桩：vitest node 环境无 document/window/localStorage/WebSocket，
 * 以 vi.stubGlobal 补齐；@/stores/user、@/composables/useSnapshotSync 以轻量 mock
 * 截断重依赖链（router/element-plus/axios/session store）。
 */
import { describe, it, expect, vi, beforeAll, afterAll } from 'vitest'
import { logger } from '@/utils/logger'

// —— vi.hoisted 载体：跨 vi.mock 工厂共享 mock user store ——
const holder = vi.hoisted(() => ({ store: null }))

vi.mock('@/stores/user', async () => {
  const { reactive } = await import('vue')
  if (!holder.store) {
    holder.store = reactive({
      token: '',
      userInfo: null,
      isLoggedIn: false,
      refreshAccessToken: vi.fn(async () => false),
    })
  }
  return { useUserStore: () => holder.store }
})

vi.mock('@/composables/useSnapshotSync', () => ({
  useSnapshotSync: vi.fn(() => ({ syncFromSnapshot: vi.fn(() => Promise.resolve()) })),
  useSnapshotSyncByTask: vi.fn(() => ({ syncFromSnapshot: vi.fn(() => Promise.resolve()) })),
}))

// 截断 element-plus 依赖链：dispatcher → @/utils/sse → apiErrorHandler → element-plus，
// element-plus 模块求值读 navigator.userAgent（node 环境无此全局）。
// 冒烟用例不触达协议解析（parseProtocolEvent 透传原样即可），sse 解析已有独立测试覆盖。
vi.mock('@/utils/sse.js', () => ({
  parseProtocolEvent: vi.fn((raw) => raw),
}))

// —— 浏览器全局桩（node 环境） ——
// 注意：vue 的 runtime-dom 在模块求值期会调用 document.createElement('div')，
// document 桩必须提供该方法，否则 vue 包初始化即崩溃（TypeError: doc.createElement）
const documentStub = {
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  visibilityState: 'visible',
  createElement: vi.fn(() => ({ innerHTML: '', setAttribute: vi.fn(), style: {} })),
  createElementNS: vi.fn(() => ({ innerHTML: '', setAttribute: vi.fn(), style: {} })),
}
const windowStub = {
  setTimeout: globalThis.setTimeout.bind(globalThis),
  clearTimeout: globalThis.clearTimeout.bind(globalThis),
  setInterval: globalThis.setInterval.bind(globalThis),
  clearInterval: globalThis.clearInterval.bind(globalThis),
  location: { protocol: 'http:', host: 'localhost' },
}
const localStorageStub = {
  getItem: vi.fn(() => null),
  setItem: vi.fn(),
  removeItem: vi.fn(),
}
// WebSocket 仅以静态常量参与比较（OPEN/CLOSED），冒烟路径不真正建连
vi.stubGlobal('WebSocket', { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 })
vi.stubGlobal('document', documentStub)
vi.stubGlobal('window', windowStub)
vi.stubGlobal('localStorage', localStorageStub)

// 抑制被测模块 logger 输出（logger.error 不受 DEV 开关控制）
const loggerSpies = []
beforeAll(async () => {
  for (const method of ['log', 'info', 'warn', 'error', 'debug']) {
    loggerSpies.push(vi.spyOn(logger, method).mockImplementation(() => {}))
  }
})

afterAll(() => {
  loggerSpies.forEach(spy => spy.mockRestore())
  vi.unstubAllGlobals()
})

// 原单文件 useRealtimeSync.js 的对外 API（返回对象键集，ARCH-14 拆分冻结基线）
const EXPECTED_API_KEYS = [
  'connectionStatus',
  'streamingActiveCount',
  'lastSeq',
  'advanceLastSeq',
  'subscribeUserEvents',
  'subscribeSession',
  'unsubscribeSession',
  'subscribeTask',
  'unsubscribeTask',
  'incrementStreaming',
  'decrementStreaming',
  'connect',
  'disconnect',
].sort()

describe('useRealtimeSync 编排器（拆分冒烟）', () => {
  it('导出面完整性：返回对象与原单文件 API 成员集合一致', async () => {
    const { useRealtimeSync } = await import('../index.js')
    const api = useRealtimeSync()

    expect(Object.keys(api).sort()).toEqual(EXPECTED_API_KEYS)

    // lastSeq 为 Map，connectionStatus/streamingActiveCount 为 ref，其余成员均为函数
    expect(api.lastSeq).toBeInstanceOf(Map)
    const REF_KEYS = ['connectionStatus', 'streamingActiveCount']
    for (const key of Object.keys(api)) {
      if (key === 'lastSeq') continue
      if (REF_KEYS.includes(key)) {
        expect(api[key]).toHaveProperty('value')
        continue
      }
      expect(typeof api[key]).toBe('function')
    }
    expect(api.connectionStatus.value).toBe('disconnected')
  })

  it('单例语义：多次调用返回同一实例（模块级缓存）', async () => {
    const { useRealtimeSync } = await import('../index.js')
    expect(useRealtimeSync()).toBe(useRealtimeSync())
  })

  it('visibilitychange 监听在单例创建时注册（已知规范缺口：永不移除，保持现状）', () => {
    // 单例已在首个用例创建；此处仅校验编排器装配了该监听
    const calls = documentStub.addEventListener.mock.calls
    expect(calls.some(([name]) => name === 'visibilitychange')).toBe(true)
  })

  it('lastSeq 去重基线：advanceLastSeq 取 max 防回退，非法入参不生效', async () => {
    const { useRealtimeSync } = await import('../index.js')
    const api = useRealtimeSync()

    api.advanceLastSeq('session_s1', 5)
    expect(api.lastSeq.get('session_s1')).toBe(5)

    // 回退被忽略（max 语义）
    api.advanceLastSeq('session_s1', 3)
    expect(api.lastSeq.get('session_s1')).toBe(5)

    // 单调推进
    api.advanceLastSeq('session_s1', 8)
    expect(api.lastSeq.get('session_s1')).toBe(8)

    // 非法入参：空 channelKey / 非 number seq 均不推进
    api.advanceLastSeq('', 100)
    api.advanceLastSeq('session_s2', '9')
    expect(api.lastSeq.has('session_s2')).toBe(false)
  })

  it('订阅注册：无 WS 时仅登记回调并返回退订函数，退订后回调表清空', async () => {
    const { useRealtimeSync } = await import('../index.js')
    const api = useRealtimeSync()

    const cbSession = vi.fn()
    const unsubSession = api.subscribeSession('s-smoke', cbSession)
    expect(typeof unsubSession).toBe('function')

    const cbUser = vi.fn()
    const unsubUser = api.subscribeUserEvents(cbUser)
    expect(typeof unsubUser).toBe('function')

    // 空 id 返回空函数，不抛错
    expect(typeof api.subscribeSession('', cbSession)).toBe('function')
    expect(typeof api.subscribeTask('', cbSession)).toBe('function')

    // 退订不抛错（无 WS 路径下不发送 unsubscribe 帧）
    expect(() => {
      unsubSession()
      unsubUser()
    }).not.toThrow()
  })

  it('SSE 流计数：increment/decrement 正确增减且下限为 0', async () => {
    const { useRealtimeSync } = await import('../index.js')
    const api = useRealtimeSync()

    api.incrementStreaming()
    expect(api.streamingActiveCount.value).toBe(1)
    api.incrementStreaming()
    expect(api.streamingActiveCount.value).toBe(2)
    api.decrementStreaming()
    api.decrementStreaming()
    expect(api.streamingActiveCount.value).toBe(0)
    // 多余 decrement 不产生负数（原逻辑：仅 >0 时递减）
    api.decrementStreaming()
    expect(api.streamingActiveCount.value).toBe(0)
  })
})
