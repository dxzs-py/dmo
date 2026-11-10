/**
 * 附件处理进度文案派生纯函数测试
 *
 * 覆盖 hasRunningAttachmentSearch / resolveAttachmentProcessingText：
 * - attachment_search 非终态 → 索引文案
 * - attachment_search 终态（completed/failed/timeout/rejected）→ 不算索引中
 * - 其他工具 / 子代理工具事件不受影响
 * - 空值防御
 *
 * 运行方式（vitest）：
 *   npm run test -- src/utils/__tests__/attachmentProcessing.test.js
 */
import { describe, it, expect } from 'vitest'
import {
  ATTACHMENT_SEARCH_TOOL_NAME,
  hasRunningAttachmentSearch,
  resolveAttachmentProcessingText,
} from '../attachmentProcessing'

describe('hasRunningAttachmentSearch', () => {
  it('常量为后端注册名 attachment_search', () => {
    expect(ATTACHMENT_SEARCH_TOOL_NAME).toBe('attachment_search')
  })

  it('attachment_search 且 status=running → true', () => {
    expect(
      hasRunningAttachmentSearch([{ toolName: 'attachment_search', status: 'running' }]),
    ).toBe(true)
  })

  it('attachment_search 且 status=pending/waiting（非终态）→ true', () => {
    expect(
      hasRunningAttachmentSearch([{ toolName: 'attachment_search', status: 'pending' }]),
    ).toBe(true)
    expect(
      hasRunningAttachmentSearch([{ toolName: 'attachment_search', status: 'waiting' }]),
    ).toBe(true)
  })

  it.each(['completed', 'failed', 'timeout', 'rejected'])(
    'attachment_search 终态 %s → false',
    status => {
      expect(hasRunningAttachmentSearch([{ toolName: 'attachment_search', status }])).toBe(false)
    },
  )

  it('status 缺失（未定状态）→ 视为仍在执行 → true', () => {
    expect(hasRunningAttachmentSearch([{ toolName: 'attachment_search' }])).toBe(true)
  })

  it('兼容 name 字段（toolCall.name）→ true', () => {
    expect(hasRunningAttachmentSearch([{ name: 'attachment_search', status: 'running' }])).toBe(true)
  })

  it('其他工具非终态 → false', () => {
    expect(hasRunningAttachmentSearch([{ toolName: 'web_search', status: 'running' }])).toBe(false)
    expect(hasRunningAttachmentSearch([{ toolName: 'attachment_reader', status: 'running' }])).toBe(false)
  })

  it('混合列表：attachment_search 已终态 + 其他工具执行中 → false', () => {
    expect(
      hasRunningAttachmentSearch([
        { toolName: 'attachment_search', status: 'completed' },
        { toolName: 'web_search', status: 'running' },
      ]),
    ).toBe(false)
  })

  it('混合列表：attachment_search 执行中排在后面 → true', () => {
    expect(
      hasRunningAttachmentSearch([
        { toolName: 'web_search', status: 'completed' },
        { toolName: 'attachment_search', status: 'running' },
      ]),
    ).toBe(true)
  })

  it.each([undefined, null, [], 'not-array', [{}, null]])('空值/异常输入 %p → false', input => {
    expect(hasRunningAttachmentSearch(input)).toBe(false)
  })
})

describe('resolveAttachmentProcessingText', () => {
  it('存在执行中的 attachment_search → 索引文案（优先于 fallback）', () => {
    expect(
      resolveAttachmentProcessingText([{ toolName: 'attachment_search', status: 'running' }], '正在处理附件...'),
    ).toBe('正在建立附件检索索引...')
  })

  it('无索引中工具 → 回退 fallbackMessage', () => {
    expect(resolveAttachmentProcessingText([], '正在处理附件...')).toBe('正在处理附件...')
    expect(
      resolveAttachmentProcessingText([{ toolName: 'attachment_search', status: 'completed' }], '正在读取附件信息'),
    ).toBe('正在读取附件信息')
  })

  it('无 fallbackMessage → 通用兜底文案', () => {
    expect(resolveAttachmentProcessingText([], undefined)).toBe('正在处理附件...')
    expect(resolveAttachmentProcessingText(undefined, '')).toBe('正在处理附件...')
  })
})
