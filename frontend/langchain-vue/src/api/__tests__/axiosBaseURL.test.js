import { describe, it, expect, vi, beforeEach } from 'vitest'

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
vi.mock('@/stores/user', () => ({ useUserStore: vi.fn() }))
vi.mock('@/stores/loading', () => ({ useLoadingStore: vi.fn() }))

import apiClient, { updateBaseURL } from '../axios'
import { logger } from '@/utils/logger'

describe('updateBaseURL - 写入路径白名单校验', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiClient.defaults.baseURL = '/api/v1'
  })

  it('合法相对路径应用成功并返回 true', () => {
    expect(updateBaseURL('/api/v2')).toBe(true)
    expect(apiClient.defaults.baseURL).toBe('/api/v2')
  })

  it('非法绝对域名拒绝生效并告警', () => {
    expect(updateBaseURL('https://evil.com/api')).toBe(false)
    expect(apiClient.defaults.baseURL).toBe('/api/v1')
    expect(logger.warn).toHaveBeenCalledTimes(1)
  })

  it('危险 scheme 拒绝生效', () => {
    expect(updateBaseURL('javascript:alert(1)')).toBe(false)
    expect(apiClient.defaults.baseURL).toBe('/api/v1')
  })

  it('协议相对地址与非字符串输入拒绝生效', () => {
    expect(updateBaseURL('//evil.com/api')).toBe(false)
    expect(updateBaseURL(undefined)).toBe(false)
    expect(apiClient.defaults.baseURL).toBe('/api/v1')
  })
})
