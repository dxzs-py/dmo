/**
 * chat store 深研沙箱开关单元测试（Spec: 沙箱执行加固 / 任务级开关 3.2/3.4）
 *
 * 覆盖：开关状态默认值（false）、setDeepResearchSandboxEnabled 更新。
 * （请求负载透传逻辑 resolveChatEnableSandbox 由 utils/__tests__/sandbox.test.js 覆盖）
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/composables/useStreamChat', () => {
  const CONNECTION_STATUS = {
    CONNECTED: 'connected',
    RECONNECTING: 'reconnecting',
    CONNECTING: 'connecting',
  }
  return {
    CONNECTION_STATUS,
    useStreamChat: vi.fn(() => ({
      isStreaming: ref(false),
      abortController: null,
      abort: vi.fn(),
      streamChat: vi.fn(),
      connectionStatus: ref(CONNECTION_STATUS.CONNECTED),
      lastError: null,
    })),
  }
})

vi.mock('@/stores/approval', () => ({
  useApprovalStore: vi.fn(() => ({ pendingApprovals: new Map() })),
}))

// 以下 store 为 chat.js 顶层静态 import；chat.js 本身不依赖其运行时行为，
// mock 以避免真实模块加载（session/index.js 链会引入 vue-router 依赖 window）。
vi.mock('@/stores/session', () => ({
  useSessionStore: vi.fn(() => ({ currentSessionId: null })),
}))
vi.mock('@/stores/model', () => ({
  useModelStore: vi.fn(() => ({})),
}))
vi.mock('@/stores/user', () => ({
  useUserStore: vi.fn(() => ({})),
}))
vi.mock('@/stores/sync', () => ({
  useSyncStore: vi.fn(() => ({})),
}))

vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

import { useChatStore } from '../chat.js'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('chat store 深研沙箱任务级开关', () => {
  it('开关默认值：deepResearchSandboxEnabled 初始为 false', () => {
    const store = useChatStore()
    expect(store.deepResearchSandboxEnabled).toBe(false)
  })

  it('setDeepResearchSandboxEnabled(true) 后开关为 true', () => {
    const store = useChatStore()
    store.setDeepResearchSandboxEnabled(true)
    expect(store.deepResearchSandboxEnabled).toBe(true)
  })

  it('setDeepResearchSandboxEnabled(false) 后开关恢复 false', () => {
    const store = useChatStore()
    store.setDeepResearchSandboxEnabled(true)
    store.setDeepResearchSandboxEnabled(false)
    expect(store.deepResearchSandboxEnabled).toBe(false)
  })
})
