/**
 * useSandboxAvailability 单元测试（Spec: 沙箱执行加固 / 任务级开关 3.3/3.4）
 *
 * 覆盖：初始默认值（false）、后端 sandbox_enabled=true 时置 true、
 * 拉取失败（异常）与响应缺省字段时回落 false（开关置灰依据）。
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/api/model', () => ({
  modelAPI: {
    getSandboxAvailability: vi.fn(),
  },
}))

import { modelAPI } from '@/api/model'
import { useSandboxAvailability } from '../useSandboxAvailability.js'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useSandboxAvailability', () => {
  it('初始状态：sandboxEnabled 默认 false（后端未启用视为置灰）', () => {
    const { sandboxEnabled, sandboxStatusLoading } = useSandboxAvailability()
    expect(sandboxEnabled.value).toBe(false)
    expect(sandboxStatusLoading.value).toBe(false)
  })

  it('拉取成功且 sandbox_enabled=true：sandboxEnabled 置 true', async () => {
    modelAPI.getSandboxAvailability.mockResolvedValue({
      data: { data: { sandboxEnabled: true } },
    })
    const { sandboxEnabled, loadSandboxStatus } = useSandboxAvailability()
    const result = await loadSandboxStatus()
    expect(result).toBe(true)
    expect(sandboxEnabled.value).toBe(true)
    expect(modelAPI.getSandboxAvailability).toHaveBeenCalledTimes(1)
  })

  it('拉取成功且 sandbox_enabled=false：sandboxEnabled 为 false（开关置灰）', async () => {
    modelAPI.getSandboxAvailability.mockResolvedValue({
      data: { data: { sandboxEnabled: false } },
    })
    const { sandboxEnabled, loadSandboxStatus } = useSandboxAvailability()
    expect(await loadSandboxStatus()).toBe(false)
    expect(sandboxEnabled.value).toBe(false)
  })

  it('响应缺省 sandbox_enabled 字段：回落 false（后端字段未就绪）', async () => {
    modelAPI.getSandboxAvailability.mockResolvedValue({
      data: { data: {} },
    })
    const { sandboxEnabled, loadSandboxStatus } = useSandboxAvailability()
    expect(await loadSandboxStatus()).toBe(false)
    expect(sandboxEnabled.value).toBe(false)
  })

  it('拉取失败（网络异常）：回落 false 且不抛出', async () => {
    modelAPI.getSandboxAvailability.mockRejectedValue(new Error('Network Error'))
    const { sandboxEnabled, loadSandboxStatus } = useSandboxAvailability()
    await expect(loadSandboxStatus()).resolves.toBe(false)
    expect(sandboxEnabled.value).toBe(false)
  })

  it('已为 true 时幂等：重复调用不重复请求', async () => {
    modelAPI.getSandboxAvailability.mockResolvedValue({
      data: { data: { sandboxEnabled: true } },
    })
    const { loadSandboxStatus } = useSandboxAvailability()
    await loadSandboxStatus()
    await loadSandboxStatus()
    expect(modelAPI.getSandboxAvailability).toHaveBeenCalledTimes(1)
  })
})
