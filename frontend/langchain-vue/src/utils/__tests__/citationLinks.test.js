import { describe, it, expect } from 'vitest'
import { escapeHtmlAttr, sanitizeCitationHref } from '../citationLinks'

describe('escapeHtmlAttr - HTML 属性转义', () => {
  it('转义 & < > " \' 五类字符（单趟替换不二次转义）', () => {
    expect(escapeHtmlAttr('&<>"\'&')).toBe('&amp;&lt;&gt;&quot;&#39;&amp;')
  })

  it('script 标签内容被转义为纯文本', () => {
    expect(escapeHtmlAttr('<script>alert(1)</script>')).toBe(
      '&lt;script&gt;alert(1)&lt;/script&gt;'
    )
  })

  it('null/undefined 归一为空串，数字转字符串', () => {
    expect(escapeHtmlAttr(null)).toBe('')
    expect(escapeHtmlAttr(undefined)).toBe('')
    expect(escapeHtmlAttr(123)).toBe('123')
  })
})

describe('sanitizeCitationHref - href 白名单', () => {
  it('放行 http/https 绝对 URL（大小写不敏感）', () => {
    expect(sanitizeCitationHref('https://example.com/a?b=1')).toBe('https://example.com/a?b=1')
    expect(sanitizeCitationHref('HTTP://EXAMPLE.COM/A')).toBe('HTTP://EXAMPLE.COM/A')
  })

  it('放行 / 开头同源相对路径与 # 锚点', () => {
    expect(sanitizeCitationHref('/api/v1/docs/1')).toBe('/api/v1/docs/1')
    expect(sanitizeCitationHref('#ref-3')).toBe('#ref-3')
  })

  it('首尾空白修剪', () => {
    expect(sanitizeCitationHref('  https://example.com  ')).toBe('https://example.com')
  })

  it('拒绝 javascript:/data:/vbscript: 危险 scheme', () => {
    expect(sanitizeCitationHref('javascript:alert(1)')).toBe('')
    expect(sanitizeCitationHref('JAVASCRIPT:alert(1)')).toBe('')
    expect(sanitizeCitationHref('data:text/html;base64,PHNjcmlwdD4=')).toBe('')
    expect(sanitizeCitationHref('vbscript:msgbox')).toBe('')
  })

  it('拒绝协议相对地址 //host（跨源）', () => {
    expect(sanitizeCitationHref('//evil.com/x')).toBe('')
  })

  it('拒绝其他相对形态与空值/非字符串', () => {
    expect(sanitizeCitationHref('relative/path')).toBe('')
    expect(sanitizeCitationHref('./x')).toBe('')
    expect(sanitizeCitationHref('')).toBe('')
    expect(sanitizeCitationHref(null)).toBe('')
    expect(sanitizeCitationHref(undefined)).toBe('')
    expect(sanitizeCitationHref(123)).toBe('')
  })

  it('放行 URL 内的属性逃逸载荷被转义（引号闭合攻击不穿透）', () => {
    const out = sanitizeCitationHref('https://example.com/" onmouseover="alert(1)')
    expect(out).toContain('&quot;')
    expect(out).not.toContain('" onmouseover')
  })
})
