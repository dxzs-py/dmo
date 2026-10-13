/**
 * research store 子代理正文覆盖语义测试（阶段 2，eliminate-priority-debts）
 *
 * 被测：src/stores/research.js
 * - setTaskSubagentContent：task 频道 stream_subagent_content 事件消费（独立深度
 *   研究模式）。阶段 2 BREAKING：payload 为该子代理**累积全量正文快照**
 *   （后端 TimeWindowThrottle 合流 + persist_history=False），整帧替换对应线程
 *   条目，禁止再按增量追加累计；空帧（content 与 reasoningContent 均空）跳过；
 *   多线程隔离。
 * - setTaskSubagentContentsFromSnapshot：getStatus 快照合并（刷新还原权威源，
 *   本地更完整时保留本地——快照滞后保护）。
 *
 * 运行方式（vitest）：npm run test
 */
import { describe, it, expect, beforeAll, beforeEach, afterAll, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { logger } from '@/utils/logger'
import { useResearchStore } from '../research'

const loggerSpies = []

beforeAll(() => {
  for (const method of ['log', 'info', 'warn', 'error', 'debug']) {
    loggerSpies.push(vi.spyOn(logger, method).mockImplementation(() => {}))
  }
})

afterAll(() => {
  loggerSpies.forEach(spy => spy.mockRestore())
})

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('useResearchStore.setTaskSubagentContent（阶段 2 覆盖语义）', () => {
  it('连续快照帧：整帧覆盖替换，不再追加累计', () => {
    const store = useResearchStore()

    store.setTaskSubagentContent('t1', 'sa-1', '第一帧', '思考一')
    store.setTaskSubagentContent('t1', 'sa-1', '第一帧第二帧', '思考一思考二')

    const contents = store.getTaskSubagentContents('t1')
    expect(contents['sa-1']).toStrictEqual({ content: '第一帧第二帧', reasoningContent: '思考一思考二' })
  })

  it('空帧：content 与 reasoningContent 均空时跳过，不改写既有条目', () => {
    const store = useResearchStore()

    store.setTaskSubagentContent('t1', 'sa-1', '正文', '')
    store.setTaskSubagentContent('t1', 'sa-1', '', '')

    expect(store.getTaskSubagentContents('t1')['sa-1'].content).toBe('正文')
  })

  it('多线程隔离：不同 subagentThreadId 各自独立覆盖', () => {
    const store = useResearchStore()

    store.setTaskSubagentContent('t1', 'sa-1', 'A1', '')
    store.setTaskSubagentContent('t1', 'sa-2', 'B1', '')
    store.setTaskSubagentContent('t1', 'sa-1', 'A1A2', '')

    const contents = store.getTaskSubagentContents('t1')
    expect(contents['sa-1'].content).toBe('A1A2')
    expect(contents['sa-2'].content).toBe('B1')
  })

  it('缺 taskId / threadId 时安全返回不抛错', () => {
    const store = useResearchStore()

    expect(() => store.setTaskSubagentContent('', 'sa-1', 'x', '')).not.toThrow()
    expect(() => store.setTaskSubagentContent('t1', '', 'x', '')).not.toThrow()
  })
})

describe('useResearchStore.setTaskSubagentContentsFromSnapshot（刷新还原权威源）', () => {
  it('本地缺失的线程条目从快照补齐', () => {
    const store = useResearchStore()
    store.setTaskSubagentContent('t1', 'sa-1', '本地已有', '')

    store.setTaskSubagentContentsFromSnapshot('t1', {
      'sa-1': { content: '本地已有', reasoningContent: '' },
      'sa-2': { content: '快照补齐线程', reasoningContent: '思考' },
    })

    const contents = store.getTaskSubagentContents('t1')
    expect(contents['sa-1'].content).toBe('本地已有')
    expect(contents['sa-2'].content).toBe('快照补齐线程')
  })

  it('本地更完整（正文更长）时保留本地——快照滞后保护', () => {
    const store = useResearchStore()
    store.setTaskSubagentContent('t1', 'sa-1', '本地流式更长的正文', '')

    store.setTaskSubagentContentsFromSnapshot('t1', {
      'sa-1': { content: '本地更', reasoningContent: '' },
    })

    expect(store.getTaskSubagentContents('t1')['sa-1'].content).toBe('本地流式更长的正文')
  })
})
