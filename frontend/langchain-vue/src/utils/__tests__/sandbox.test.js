/**
 * sandbox 纯逻辑单元测试（Spec: 沙箱执行加固 / 任务级开关 3.4）
 *
 * 覆盖聊天模块深研模式的 enableSandbox 解析：
 * 仅 deep-research 模式且开关开启时为 true，其余组合均为 false。
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect } from 'vitest'
import { resolveChatEnableSandbox } from '../sandbox.js'

describe('resolveChatEnableSandbox', () => {
  it('deep-research 模式 + 开关开启 → true', () => {
    expect(resolveChatEnableSandbox('deep-research', true)).toBe(true)
  })

  it('deep-research 模式 + 开关关闭 → false（开关默认值）', () => {
    expect(resolveChatEnableSandbox('deep-research', false)).toBe(false)
  })

  it('agent 模式 + 开关开启 → false（沙箱仅深研模式生效）', () => {
    expect(resolveChatEnableSandbox('agent', true)).toBe(false)
  })

  it('agent 模式 + 开关关闭 → false', () => {
    expect(resolveChatEnableSandbox('agent', false)).toBe(false)
  })
})
