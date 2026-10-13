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

import settings, { isAllowedApiBaseUrl, ALLOWED_API_BASE_HOSTS } from '../settings'
import { logger } from '@/utils/logger'

/** 构造内存版 localStorage 桩（vitest node 环境无真实 window/localStorage） */
const createStorageStub = () => {
  const store = new Map()
  return {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => { store.set(key, String(value)) },
    removeItem: (key) => { store.delete(key) },
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('window', { localStorage: createStorageStub(), chrome: undefined })
})

afterEach(() => {
  ALLOWED_API_BASE_HOSTS.length = 0
  vi.unstubAllGlobals()
})

describe('isAllowedApiBaseUrl - 白名单校验', () => {
  it('放行 /api 开头的同源相对路径（首尾空白忽略）', () => {
    expect(isAllowedApiBaseUrl('/api/v1')).toBe(true)
    expect(isAllowedApiBaseUrl('/api/v1/')).toBe(true)
    expect(isAllowedApiBaseUrl('  /api/v1  ')).toBe(true)
  })

  it('拒绝协议相对地址 //host（跨源）', () => {
    expect(isAllowedApiBaseUrl('//evil.com/api')).toBe(false)
  })

  it('拒绝 javascript:/data: 等危险 scheme', () => {
    expect(isAllowedApiBaseUrl('javascript:alert(1)')).toBe(false)
    expect(isAllowedApiBaseUrl('data:text/html,<script>alert(1)</script>')).toBe(false)
  })

  it('拒绝未登记的绝对域名（默认白名单为空）', () => {
    expect(isAllowedApiBaseUrl('https://evil.com/api')).toBe(false)
    expect(isAllowedApiBaseUrl('http://localhost:8000/api')).toBe(false)
  })

  it('放行命中 ALLOWED_API_BASE_HOSTS 的 http(s) URL（host 含端口精确匹配）', () => {
    ALLOWED_API_BASE_HOSTS.push('api.example.com')
    expect(isAllowedApiBaseUrl('https://api.example.com/api/v1')).toBe(true)
    expect(isAllowedApiBaseUrl('http://api.example.com/api/v1')).toBe(true)
    // host 必须精确匹配，后缀/子域拼接与端口变更均不放行
    expect(isAllowedApiBaseUrl('https://api.example.com.evil.com/api')).toBe(false)
    expect(isAllowedApiBaseUrl('https://evil-api.example.com/api')).toBe(false)
    expect(isAllowedApiBaseUrl('https://api.example.com:8443/api')).toBe(false)
  })

  it('拒绝空值与非字符串', () => {
    expect(isAllowedApiBaseUrl('')).toBe(false)
    expect(isAllowedApiBaseUrl('   ')).toBe(false)
    expect(isAllowedApiBaseUrl(null)).toBe(false)
    expect(isAllowedApiBaseUrl(undefined)).toBe(false)
    expect(isAllowedApiBaseUrl(123)).toBe(false)
  })
})

describe('settings.apiBaseUrl - localStorage 读取与回退', () => {
  it('无存储值时返回默认 /api/v1', () => {
    expect(settings.apiBaseUrl).toBe('/api/v1')
    expect(logger.warn).not.toHaveBeenCalled()
  })

  it('合法存储值生效（setter 写入 → getter 读取，无告警）', () => {
    settings.apiBaseUrl = '/api/v2'
    expect(window.localStorage.getItem('dx-study-agent-api-url')).toBe('/api/v2')
    expect(settings.apiBaseUrl).toBe('/api/v2')
    expect(logger.warn).not.toHaveBeenCalled()
  })

  it('非法存储值回退默认并告警一次（同一原始值重复读取不再告警）', () => {
    window.localStorage.setItem('dx-study-agent-api-url', 'https://evil.com/api')
    expect(settings.apiBaseUrl).toBe('/api/v1')
    expect(logger.warn).toHaveBeenCalledTimes(1)

    expect(settings.apiBaseUrl).toBe('/api/v1')
    expect(logger.warn).toHaveBeenCalledTimes(1)
  })

  it('存储值变更后重新按白名单解析', () => {
    window.localStorage.setItem('dx-study-agent-api-url', 'https://evil-a.com/api')
    expect(settings.apiBaseUrl).toBe('/api/v1')
    window.localStorage.setItem('dx-study-agent-api-url', '/api/v9')
    expect(settings.apiBaseUrl).toBe('/api/v9')
  })

  it('非法值回退后写入合法值恢复正常', () => {
    settings.apiBaseUrl = 'javascript:alert(1)'
    expect(settings.apiBaseUrl).toBe('/api/v1')
    settings.apiBaseUrl = '/api/v3'
    expect(settings.apiBaseUrl).toBe('/api/v3')
  })
})
