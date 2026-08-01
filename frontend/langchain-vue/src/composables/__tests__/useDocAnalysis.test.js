import { describe, it, expect, beforeEach, vi } from 'vitest'
import { ref } from 'vue'

const mockGetFiles = vi.fn()
const mockGetFileContent = vi.fn()
vi.mock('@/api', () => ({
  deepResearchAPI: {
    getFiles: (...args) => mockGetFiles(...args),
    getFileContent: (...args) => mockGetFileContent(...args),
  },
}))

vi.mock('@/utils/logger', () => ({
  logger: { log: () => {}, info: () => {}, warn: () => {}, error: () => {}, debug: () => {} },
}))

import { useDocAnalysis } from '../useDocAnalysis'

describe('useDocAnalysis', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('checkDocAnalysisFile 重置文件与内容状态', () => {
    const task = ref(null)
    const { docAnalysisContent, docAnalysisFile, checkDocAnalysisFile } = useDocAnalysis({ task })

    docAnalysisContent.value = 'old'
    docAnalysisFile.value = 'old.md'

    checkDocAnalysisFile()

    expect(docAnalysisContent.value).toBeNull()
    expect(docAnalysisFile.value).toBeNull()
  })

  it('autoLoadDocAnalysis 未启用文档分析时不加载', async () => {
    const task = ref({ task_id: 't-1', enable_doc_analysis: false })
    const { autoLoadDocAnalysis } = useDocAnalysis({ task })

    await autoLoadDocAnalysis()
    expect(mockGetFiles).not.toHaveBeenCalled()
  })

  it('autoLoadDocAnalysis task_id 缺失时不加载', async () => {
    const task = ref({ enable_doc_analysis: true })
    const { autoLoadDocAnalysis } = useDocAnalysis({ task })

    await autoLoadDocAnalysis()
    expect(mockGetFiles).not.toHaveBeenCalled()
  })

  it('loadDocAnalysis 优先从 notes 目录加载 .md 文件', async () => {
    const task = ref({
      task_id: 't-1',
      enable_doc_analysis: true,
      knowledge_base_ids: ['kb-1'],
    })
    mockGetFiles.mockResolvedValue({
      data: { data: { files: [
        { name: 'notes', type: 'directory', children: [{ name: 'analysis.md' }] },
        { name: 'report.md', type: 'file' },
      ] } },
    })
    mockGetFileContent.mockResolvedValue({
      data: { data: { content: '# 分析依据' } },
    })

    const { loadDocAnalysis, docAnalysisContent, docAnalysisFile, docAnalysisLoading } = useDocAnalysis({ task })

    const promise = loadDocAnalysis()
    expect(docAnalysisLoading.value).toBe(true)
    await promise

    expect(mockGetFiles).toHaveBeenCalledWith('t-1')
    expect(mockGetFileContent).toHaveBeenCalledWith('t-1', 'notes/analysis.md')
    expect(docAnalysisContent.value).toBe('# 分析依据')
    expect(docAnalysisFile.value).toBe('notes/analysis.md')
    expect(docAnalysisLoading.value).toBe(false)
  })

  it('loadDocAnalysis notes 目录无 .md 时回退到 .txt', async () => {
    const task = ref({
      task_id: 't-1',
      knowledge_base_ids: ['kb-1'],
    })
    mockGetFiles.mockResolvedValue({
      data: { data: { files: [
        { name: 'notes', type: 'directory', children: [{ name: 'notes.txt' }] },
      ] } },
    })
    mockGetFileContent.mockResolvedValue({ data: { data: 'plain text' } })

    const { loadDocAnalysis, docAnalysisFile } = useDocAnalysis({ task })
    await loadDocAnalysis()

    expect(mockGetFileContent).toHaveBeenCalledWith('t-1', 'notes/notes.txt')
    expect(docAnalysisFile.value).toBe('notes/notes.txt')
  })

  it('loadDocAnalysis 无 notes 目录时回退到根目录 .md（排除 report）', async () => {
    const task = ref({
      task_id: 't-1',
      knowledge_base_ids: ['kb-1'],
    })
    mockGetFiles.mockResolvedValue({
      data: { data: { files: [
        { name: 'report.md', type: 'file', relative_path: 'report.md' },
        { name: 'summary.md', type: 'file', relative_path: 'summary.md' },
      ] } },
    })
    mockGetFileContent.mockResolvedValue({ data: { data: { content: 'summary' } } })

    const { loadDocAnalysis, docAnalysisFile } = useDocAnalysis({ task })
    await loadDocAnalysis()

    expect(mockGetFileContent).toHaveBeenCalledWith('t-1', 'summary.md')
    expect(docAnalysisFile.value).toBe('summary.md')
  })

  it('loadDocAnalysis 未找到分析文件时清空内容', async () => {
    const task = ref({
      task_id: 't-1',
      knowledge_base_ids: ['kb-1'],
    })
    mockGetFiles.mockResolvedValue({ data: { data: { files: [] } } })

    const { loadDocAnalysis, docAnalysisContent, docAnalysisFile } = useDocAnalysis({ task })
    await loadDocAnalysis()

    expect(docAnalysisContent.value).toBeNull()
    expect(docAnalysisFile.value).toBeNull()
  })

  it('loadDocAnalysis knowledge_base_ids 为空时不加载', async () => {
    const task = ref({ task_id: 't-1', knowledge_base_ids: [] })
    const { loadDocAnalysis } = useDocAnalysis({ task })

    await loadDocAnalysis()
    expect(mockGetFiles).not.toHaveBeenCalled()
  })

  it('loadDocAnalysis getFileContent 失败时清空内容', async () => {
    const task = ref({
      task_id: 't-1',
      knowledge_base_ids: ['kb-1'],
    })
    mockGetFiles.mockResolvedValue({
      data: { data: { files: [{ name: 'notes', type: 'directory', children: [{ name: 'a.md' }] }] } },
    })
    mockGetFileContent.mockRejectedValue(new Error('获取内容失败'))

    const { loadDocAnalysis, docAnalysisContent } = useDocAnalysis({ task })
    await loadDocAnalysis()

    expect(docAnalysisContent.value).toBeNull()
  })

  it('autoLoadDocAnalysis 启用文档分析时自动加载', async () => {
    const task = ref({
      task_id: 't-1',
      enable_doc_analysis: true,
      knowledge_base_ids: ['kb-1'],
    })
    mockGetFiles.mockResolvedValue({ data: { data: { files: [] } } })

    const { autoLoadDocAnalysis } = useDocAnalysis({ task })
    await autoLoadDocAnalysis()

    expect(mockGetFiles).toHaveBeenCalledWith('t-1')
  })
})
