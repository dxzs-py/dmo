import { describe, expect, it } from 'vitest'
import { formatSourceTitle } from '../ragSources'

describe('formatSourceTitle（C-4 结构化来源契约）', () => {
  it('有顶层 source 显示名时拼接序号与文件名', () => {
    expect(formatSourceTitle({ content: '片段', source: 'intro.md', metadata: {} }, 0)).toBe(
      '来源 1: intro.md'
    )
    expect(formatSourceTitle({ source: 'b.md' }, 2)).toBe('来源 3: b.md')
  })

  it('无 source 字段时回退"来源 N"（不再读取 metadata.server 路径）', () => {
    expect(formatSourceTitle({ content: '片段', metadata: {} }, 1)).toBe('来源 2')
    expect(formatSourceTitle(undefined, 0)).toBe('来源 1')
  })

  it('metadata.source（服务器路径）不再参与标题——C-7 白名单后该键不存在', () => {
    // 旧实现读取 source.metadata.source（C-7 后恒 undefined，回退"来源 N"），
    // 新实现只认顶层 source，锁定该口径防回归
    expect(formatSourceTitle({ metadata: { source: '/srv/secret/a.md' } }, 0)).toBe('来源 1')
  })
})
