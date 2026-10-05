/**
 * utils/format.js 数字格式化单元测试（F1 Token 可见性新增 formatTokenCount）
 *
 * 覆盖：原样区间 / k 缩写（1 位小数四舍五入）/ M 缩写 / 非法输入兜底 0
 *
 * 运行方式（vitest）：npm run test
 */
import { describe, it, expect } from 'vitest'
import { formatTokenCount } from '../format.js'

describe('formatTokenCount', () => {
  it('小于 1000 原样返回（整数字符串）', () => {
    expect(formatTokenCount(0)).toBe('0')
    expect(formatTokenCount(5)).toBe('5')
    expect(formatTokenCount(999)).toBe('999')
  })

  it('1000 ~ 1万 缩写为 k，保留 1 位小数', () => {
    expect(formatTokenCount(1000)).toBe('1k')
    expect(formatTokenCount(1234)).toBe('1.2k')
    expect(formatTokenCount(9876)).toBe('9.9k')
  })

  it('1万 ~ 100万 缩写为 k（12.3k 风格）', () => {
    expect(formatTokenCount(12345)).toBe('12.3k')
    expect(formatTokenCount(123456)).toBe('123.5k')
  })

  it('≥ 100万 缩写为 M（1 位小数）', () => {
    expect(formatTokenCount(1000000)).toBe('1M')
    expect(formatTokenCount(1234567)).toBe('1.2M')
  })

  it('非法输入按 0 兜底', () => {
    expect(formatTokenCount(null)).toBe('0')
    expect(formatTokenCount(undefined)).toBe('0')
    expect(formatTokenCount('abc')).toBe('0')
    expect(formatTokenCount(-5)).toBe('0')
  })
})
