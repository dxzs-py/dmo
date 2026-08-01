import { describe, it, expect, beforeEach, vi } from 'vitest'

const mockGetKnowledgeBases = vi.fn()
vi.mock('@/api', () => ({
  knowledgeAPI: {
    getKnowledgeBases: (...args) => mockGetKnowledgeBases(...args),
  },
}))

vi.mock('element-plus', () => ({
  ElMessage: {
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}))

vi.mock('@/utils/logger', () => ({
  logger: { log: () => {}, info: () => {}, warn: () => {}, error: () => {}, debug: () => {} },
}))

import { useKnowledgeBases } from '../useKnowledgeBases'

describe('useKnowledgeBases', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('refreshKnowledgeBases 加载并填充 filteredKnowledgeBases', async () => {
    mockGetKnowledgeBases.mockResolvedValue({
      data: { data: { items: [{ id: 'kb-1', name: '知识库A', chunk_count: 10 }] } },
    })

    const { knowledgeBases, filteredKnowledgeBases, kbLoading, refreshKnowledgeBases } = useKnowledgeBases()

    expect(kbLoading.value).toBe(false)
    const promise = refreshKnowledgeBases()
    expect(kbLoading.value).toBe(true)
    await promise

    expect(kbLoading.value).toBe(false)
    expect(knowledgeBases.value).toHaveLength(1)
    expect(knowledgeBases.value[0].id).toBe('kb-1')
    expect(filteredKnowledgeBases.value).toHaveLength(1)
  })

  it('refreshKnowledgeBases 失败时调用 ElMessage.error', async () => {
    mockGetKnowledgeBases.mockRejectedValue(new Error('网络错误'))

    const { refreshKnowledgeBases, knowledgeBases } = useKnowledgeBases()
    await refreshKnowledgeBases()

    // 失败时列表保持初始空数组
    expect(knowledgeBases.value).toHaveLength(0)
  })

  it('filterKnowledgeBases 按名称过滤', async () => {
    mockGetKnowledgeBases.mockResolvedValue({
      data: { data: { items: [
        { id: '1', name: 'Vue 知识库', description: '' },
        { id: '2', name: 'React 手册', description: '前端框架' },
      ] } },
    })

    const { refreshKnowledgeBases, filterKnowledgeBases, kbSearchQuery, filteredKnowledgeBases } = useKnowledgeBases()
    await refreshKnowledgeBases()
    expect(filteredKnowledgeBases.value).toHaveLength(2)

    kbSearchQuery.value = 'Vue'
    filterKnowledgeBases()
    expect(filteredKnowledgeBases.value).toHaveLength(1)
    expect(filteredKnowledgeBases.value[0].name).toBe('Vue 知识库')
  })

  it('filterKnowledgeBases 按描述过滤', async () => {
    mockGetKnowledgeBases.mockResolvedValue({
      data: { data: { items: [
        { id: '1', name: 'KB1', description: '关于 LangChain' },
        { id: '2', name: 'KB2', description: '其他内容' },
      ] } },
    })

    const { refreshKnowledgeBases, filterKnowledgeBases, kbSearchQuery, filteredKnowledgeBases } = useKnowledgeBases()
    await refreshKnowledgeBases()

    kbSearchQuery.value = 'langchain'
    filterKnowledgeBases()
    expect(filteredKnowledgeBases.value).toHaveLength(1)
    expect(filteredKnowledgeBases.value[0].id).toBe('1')
  })

  it('kbSearchQuery 为空时 filterKnowledgeBases 返回全部', async () => {
    mockGetKnowledgeBases.mockResolvedValue({
      data: { data: { items: [{ id: '1', name: 'A' }, { id: '2', name: 'B' }] } },
    })

    const { refreshKnowledgeBases, filterKnowledgeBases, kbSearchQuery, filteredKnowledgeBases } = useKnowledgeBases()
    await refreshKnowledgeBases()

    kbSearchQuery.value = 'A'
    filterKnowledgeBases()
    expect(filteredKnowledgeBases.value).toHaveLength(1)

    kbSearchQuery.value = ''
    filterKnowledgeBases()
    expect(filteredKnowledgeBases.value).toHaveLength(2)
  })
})
