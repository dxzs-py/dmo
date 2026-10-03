/**
 * model store loadProviders 单元测试（Spec P4：模型列表加载零 403）
 *
 * 覆盖：
 * - 系统默认（providers 响应 defaultProvider/defaultModel）可用 → 初始选中系统默认
 * - 系统默认不可用 → 回退第一个可用 provider + warning
 * - 响应无默认标记 → 回退当前/第一个可用 provider
 * - 全程不调用 modelAPI.getAISettings（IsAdminUser 端点，403 噪音根因）
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/model', () => ({
  modelAPI: {
    getProviders: vi.fn(),
    getAISettings: vi.fn(),
    testConnection: vi.fn(),
  },
}))

vi.mock('@/stores/user', () => ({
  useUserStore: vi.fn(() => ({ isLoggedIn: true })),
}))

vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn(), success: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('@/utils/specialParams', () => ({
  initSpecialParams: vi.fn((provider) => ({ ...(provider?.specialParams || {}) })),
  isThinkingEnabled: vi.fn(() => false),
  applySpecialParamChange: vi.fn((params) => params),
}))

vi.mock('@/utils/logger', () => ({
  logger: { error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() },
}))

vi.mock('@/utils/apiErrorHandler', () => ({
  extractErrorMessage: vi.fn((e) => String(e)),
}))

import { ElMessage } from 'element-plus'
import { modelAPI } from '@/api/model'
import { useModelStore } from '../model.js'

// 模拟 axios 响应拦截器转换后的 camelCase 数据形状
const AVAILABLE_PROVIDERS = [
  {
    id: 'openai',
    label: 'OpenAI',
    available: true,
    defaultModel: 'gpt-4o-mini',
    models: [{ name: 'gpt-4o-mini', capabilities: ['tool_calling', 'streaming'] }],
    specialParams: {},
  },
  {
    id: 'deepseek',
    label: 'DeepSeek',
    available: true,
    defaultModel: 'deepseek-v4-flash',
    models: [{ name: 'deepseek-v4-flash', capabilities: ['tool_calling', 'streaming'] }],
    specialParams: {},
  },
]

const UNAVAILABLE_GROQ = {
  id: 'groq',
  label: 'Groq',
  available: false,
  defaultModel: 'llama-3.3-70b-versatile',
  models: [{ name: 'llama-3.3-70b-versatile', capabilities: ['tool_calling', 'streaming'] }],
  specialParams: {},
}

const _providersResponse = (providers, defaults = {}) => ({
  data: {
    code: 200,
    data: { providers, ...defaults },
  },
})

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('model store loadProviders 系统默认解析', () => {
  it('① 系统默认可用：初始选中 defaultProvider/defaultModel，且不请求 getAISettings', async () => {
    modelAPI.getProviders.mockResolvedValue(
      _providersResponse(AVAILABLE_PROVIDERS, {
        defaultProvider: 'deepseek',
        defaultModel: 'deepseek-v4-flash',
      })
    )

    const store = useModelStore()
    await store.loadProviders()

    expect(store.currentProviderId).toBe('deepseek')
    expect(store.currentModelName).toBe('deepseek-v4-flash')
    expect(modelAPI.getAISettings).not.toHaveBeenCalled()
    expect(ElMessage.warning).not.toHaveBeenCalled()
  })

  it('② 系统默认不可用：回退第一个可用 provider 并 warning，不请求 getAISettings', async () => {
    modelAPI.getProviders.mockResolvedValue(
      _providersResponse([AVAILABLE_PROVIDERS[0], UNAVAILABLE_GROQ], {
        defaultProvider: 'groq',
        defaultModel: 'llama-3.3-70b-versatile',
      })
    )

    const store = useModelStore()
    await store.loadProviders()

    expect(store.currentProviderId).toBe('openai')
    expect(store.currentModelName).toBe('gpt-4o-mini')
    expect(ElMessage.warning).toHaveBeenCalledTimes(1)
    expect(ElMessage.warning.mock.calls[0][0]).toContain('Groq')
    expect(modelAPI.getAISettings).not.toHaveBeenCalled()
  })

  it('③ 响应无默认标记：回退当前可用 provider，不请求 getAISettings', async () => {
    modelAPI.getProviders.mockResolvedValue(_providersResponse(AVAILABLE_PROVIDERS))

    const store = useModelStore()
    await store.loadProviders()

    expect(store.currentProviderId).toBe('openai')
    expect(store.currentModelName).toBe('gpt-4o-mini')
    expect(ElMessage.warning).not.toHaveBeenCalled()
    expect(modelAPI.getAISettings).not.toHaveBeenCalled()
  })
})
