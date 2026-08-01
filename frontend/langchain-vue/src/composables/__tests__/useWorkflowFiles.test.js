import { describe, it, expect, beforeEach, vi } from 'vitest'
import { ref, nextTick } from 'vue'

const mockGetFiles = vi.fn()
const mockGetFileContent = vi.fn()
vi.mock('@/api', () => ({
  workflowAPI: {
    getFiles: (...args) => mockGetFiles(...args),
    getFileContent: (...args) => mockGetFileContent(...args),
  },
}))

vi.mock('@/utils/logger', () => ({
  logger: { log: () => {}, info: () => {}, warn: () => {}, error: () => {}, debug: () => {} },
}))

import { useWorkflowFiles } from '../useWorkflowFiles'

describe('useWorkflowFiles', () => {
  let execution

  beforeEach(() => {
    vi.clearAllMocks()
    execution = ref({ thread_id: 'wf-1' })
  })

  function createFiles() {
    return useWorkflowFiles({ execution })
  }

  describe('_findKeyFile', () => {
    it('execution 无 thread_id 时返回 null', async () => {
      execution.value = null
      const { _findKeyFile } = createFiles()
      expect(await _findKeyFile()).toBeNull()
      expect(mockGetFiles).not.toHaveBeenCalled()
    })

    it('优先返回 notes/ 目录下的 .md 文件路径', async () => {
      mockGetFiles.mockResolvedValue({
        data: {
          files: [
            { name: 'notes', type: 'directory', children: [{ name: 'lesson.md' }] },
            { name: 'report.md', type: 'file' },
          ],
        },
      })
      const { _findKeyFile } = createFiles()
      expect(await _findKeyFile()).toBe('notes/lesson.md')
    })

    it('notes/ 无 .md 时返回 .txt 文件路径', async () => {
      mockGetFiles.mockResolvedValue({
        data: {
          files: [
            { name: 'notes', type: 'directory', children: [{ name: 'notes.txt' }] },
          ],
        },
      })
      const { _findKeyFile } = createFiles()
      expect(await _findKeyFile()).toBe('notes/notes.txt')
      // 已找到 .txt，不应再尝试根目录 .md
      expect(mockGetFiles).toHaveBeenCalledTimes(1)
    })

    it('无 notes/ 目录时返回根目录非 report 的 .md 文件', async () => {
      mockGetFiles.mockResolvedValue({
        data: {
          files: [
            { name: 'report.md', type: 'file', relative_path: 'report.md' },
            { name: 'summary.md', type: 'file', relative_path: 'summary.md' },
          ],
        },
      })
      const { _findKeyFile } = createFiles()
      // report.md 被排除，返回 summary.md
      expect(await _findKeyFile()).toBe('summary.md')
    })

    it('无 .md 时返回根目录 .txt 文件', async () => {
      mockGetFiles.mockResolvedValue({
        data: {
          files: [
            { name: 'readme.txt', type: 'file', relative_path: 'readme.txt' },
          ],
        },
      })
      const { _findKeyFile } = createFiles()
      expect(await _findKeyFile()).toBe('readme.txt')
    })

    it('无匹配文件时返回 null', async () => {
      mockGetFiles.mockResolvedValue({ data: { files: [] } })
      const { _findKeyFile } = createFiles()
      expect(await _findKeyFile()).toBeNull()
    })

    it('getFiles 抛错时返回 null（不抛出）', async () => {
      mockGetFiles.mockRejectedValue(new Error('网络错误'))
      const { _findKeyFile } = createFiles()
      expect(await _findKeyFile()).toBeNull()
    })

    it('兼容 data 直接为数组结构', async () => {
      mockGetFiles.mockResolvedValue({
        data: [{ name: 'intro.md', type: 'file', relative_path: 'intro.md' }],
      })
      const { _findKeyFile } = createFiles()
      expect(await _findKeyFile()).toBe('intro.md')
    })
  })

  describe('autoLoadKeyFile', () => {
    it('execution 无 thread_id 时直接返回，不设置 loading', async () => {
      execution.value = null
      const { autoLoadKeyFile, autoLoadLoading, autoLoadContent } = createFiles()
      await autoLoadKeyFile()
      expect(autoLoadLoading.value).toBe(false)
      expect(autoLoadContent.value).toBeNull()
    })

    it('找到文件时读取内容写入 autoLoadContent', async () => {
      mockGetFiles.mockResolvedValue({
        data: { files: [{ name: 'notes', type: 'directory', children: [{ name: 'a.md' }] }] },
      })
      mockGetFileContent.mockResolvedValue({ data: { content: '# 学习笔记内容' } })
      const { autoLoadKeyFile, autoLoadContent, autoLoadLoading } = createFiles()
      await autoLoadKeyFile()
      expect(autoLoadContent.value).toBe('# 学习笔记内容')
      expect(autoLoadLoading.value).toBe(false)
      expect(mockGetFileContent).toHaveBeenCalledWith('wf-1', 'notes/a.md')
    })

    it('未找到文件时 autoLoadContent 保持 null', async () => {
      mockGetFiles.mockResolvedValue({ data: { files: [] } })
      const { autoLoadKeyFile, autoLoadContent } = createFiles()
      await autoLoadKeyFile()
      expect(autoLoadContent.value).toBeNull()
      expect(mockGetFileContent).not.toHaveBeenCalled()
    })

    it('读取内容失败时静默处理（不抛错），autoLoadContent 为 null', async () => {
      mockGetFiles.mockResolvedValue({
        data: { files: [{ name: 'x.md', type: 'file', relative_path: 'x.md' }] },
      })
      mockGetFileContent.mockRejectedValue(new Error('读取失败'))
      const { autoLoadKeyFile, autoLoadLoading } = createFiles()
      await expect(autoLoadKeyFile()).resolves.toBeUndefined()
      expect(autoLoadLoading.value).toBe(false)
    })

    it('加载前清空旧内容（autoLoadContent 置 null）', async () => {
      mockGetFiles.mockResolvedValue({
        data: { files: [{ name: 'n.md', type: 'file', relative_path: 'n.md' }] },
      })
      mockGetFileContent.mockResolvedValue({ data: { content: 'new' } })
      const { autoLoadKeyFile, autoLoadContent } = createFiles()
      autoLoadContent.value = 'old content'
      await autoLoadKeyFile()
      expect(autoLoadContent.value).toBe('new')
    })

    it('兼容 content 直接作为字符串返回', async () => {
      mockGetFiles.mockResolvedValue({
        data: { files: [{ name: 'y.md', type: 'file', relative_path: 'y.md' }] },
      })
      mockGetFileContent.mockResolvedValue({ data: 'plain string content' })
      const { autoLoadKeyFile, autoLoadContent } = createFiles()
      await autoLoadKeyFile()
      expect(autoLoadContent.value).toBe('plain string content')
    })
  })

  describe('clearAutoLoad', () => {
    it('清空 autoLoadContent', async () => {
      mockGetFiles.mockResolvedValue({
        data: { files: [{ name: 'z.md', type: 'file', relative_path: 'z.md' }] },
      })
      mockGetFileContent.mockResolvedValue({ data: { content: 'loaded' } })
      const { autoLoadKeyFile, autoLoadContent, clearAutoLoad } = createFiles()
      await autoLoadKeyFile()
      expect(autoLoadContent.value).toBe('loaded')
      clearAutoLoad()
      expect(autoLoadContent.value).toBeNull()
      // 响应式：清空后下次 nextTick 仍为 null
      await nextTick()
      expect(autoLoadContent.value).toBeNull()
    })
  })
})
