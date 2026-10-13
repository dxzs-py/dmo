/**
 * useSnapshotSync 子代理正文状态提升测试（阶段 2，eliminate-priority-debts）
 *
 * 被测：src/composables/useSnapshotSync.js `_reconcileMessageField` 的
 * subagentContents 按线程提升分支（经 session 快照校对全链路驱动）。
 *
 * 背景（2.1 权威源核实结论）：stream_subagent_content 快照帧不入回放历史
 * （persist_history=False），断线/重连浏览器错过的实时帧由入历史的权威事件
 * （message_updated / stream_finalized 等）触发快照校对，经后端落库的
 * ChatMessage.subagent_contents（camelCase 后 subagentContents）补齐恢复。
 *
 * 提升规则（与 content/reasoning 的"后端更长才覆盖"状态提升一致）：
 * - 本地缺失该线程条目 → 以快照补齐；
 * - 后端正文更长（落库快照更完整）→ 覆盖该线程条目；
 * - 本地正文更长（流式进行中，DB 落库滞后 ≤ 节流窗口）→ 保留本地。
 *
 * mock 策略：跟随现有范例（approval.test.js）——api 层 mock '@/api/realtime'
 * 与 '@/api/research'；session/research store mock 为纯对象。
 *
 * 运行方式（vitest）：npm run test
 */
import { describe, it, expect, vi, beforeEach, afterEach, beforeAll, afterAll } from 'vitest'
import { logger } from '@/utils/logger'
import { useSnapshotSync } from '../useSnapshotSync'

vi.mock('@/api/realtime', () => ({
  getSessionSnapshot: vi.fn(),
}))

vi.mock('@/api/research', () => ({
  deepResearchAPI: { getStatus: vi.fn() },
}))

const sessionStoreMock = { sessions: [] }

vi.mock('@/stores/session', () => ({
  useSessionStore: () => sessionStoreMock,
}))

vi.mock('@/stores/research', () => ({
  useResearchStore: () => ({ setTaskToolCallsFromSnapshot: vi.fn(), setTaskSubagentContentsFromSnapshot: vi.fn(), setTaskStatus: vi.fn() }),
}))

import { getSessionSnapshot } from '@/api/realtime'

const loggerSpies = []

beforeAll(() => {
  for (const method of ['log', 'info', 'warn', 'error', 'debug']) {
    loggerSpies.push(vi.spyOn(logger, method).mockImplementation(() => {}))
  }
})

afterAll(() => {
  loggerSpies.forEach(spy => spy.mockRestore())
})

const buildBackendMessage = overrides => ({
  id: 'b1',
  role: 'assistant',
  content: '',
  toolCalls: [],
  subagentContents: {},
  ...overrides,
})

const flushSnapshotSync = async () => {
  // debounce 500ms（fake timers）+ 微任务排空（Promise.race / finally 链）
  await vi.advanceTimersByTimeAsync(500)
}

describe('useSnapshotSync subagentContents 按线程状态提升（阶段 2）', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    sessionStoreMock.sessions = []
    getSessionSnapshot.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('本地缺失线程条目：以快照补齐（断线错过的快照帧经校对收敛）', async () => {
    const localMsg = {
      id: 'm1',
      backendId: 'b1',
      role: 'assistant',
      content: '',
      subagentContents: {},
    }
    sessionStoreMock.sessions = [{ id: 's1', messages: [localMsg] }]
    getSessionSnapshot.mockResolvedValue({
      data: { data: { messages: [
        buildBackendMessage({
          subagentContents: { 'sa-1': { content: '落库全量正文', reasoningContent: '思考' } },
        }),
      ] } },
    })

    const { syncFromSnapshot } = useSnapshotSync('s1')
    syncFromSnapshot()
    await flushSnapshotSync()

    expect(localMsg.subagentContents['sa-1'].content).toBe('落库全量正文')
    expect(localMsg.subagentContents['sa-1'].reasoningContent).toBe('思考')
  })

  it('后端正文更长（落库快照更完整）：覆盖该线程条目', async () => {
    const localMsg = {
      id: 'm1',
      backendId: 'b1',
      role: 'assistant',
      content: '',
      subagentContents: { 'sa-1': { content: '短', reasoningContent: '' } },
    }
    sessionStoreMock.sessions = [{ id: 's1', messages: [localMsg] }]
    getSessionSnapshot.mockResolvedValue({
      data: { data: { messages: [
        buildBackendMessage({
          subagentContents: { 'sa-1': { content: '落库更完整的长正文', reasoningContent: '' } },
        }),
      ] } },
    })

    const { syncFromSnapshot } = useSnapshotSync('s1')
    syncFromSnapshot()
    await flushSnapshotSync()

    expect(localMsg.subagentContents['sa-1'].content).toBe('落库更完整的长正文')
  })

  it('本地正文更长（流式进行中，DB 落库滞后）：保留本地不被陈旧快照回退', async () => {
    const localMsg = {
      id: 'm1',
      backendId: 'b1',
      role: 'assistant',
      content: '',
      subagentContents: { 'sa-1': { content: '本地流式更新的更长正文', reasoningContent: '' } },
    }
    sessionStoreMock.sessions = [{ id: 's1', messages: [localMsg] }]
    getSessionSnapshot.mockResolvedValue({
      data: { data: { messages: [
        buildBackendMessage({
          subagentContents: { 'sa-1': { content: '本地更', reasoningContent: '' } },
        }),
      ] } },
    })

    const { syncFromSnapshot } = useSnapshotSync('s1')
    syncFromSnapshot()
    await flushSnapshotSync()

    expect(localMsg.subagentContents['sa-1'].content).toBe('本地流式更新的更长正文')
  })

  it('本地 subagentContents 缺失（undefined）：整体从快照初始化', async () => {
    const localMsg = {
      id: 'm1',
      backendId: 'b1',
      role: 'assistant',
      content: '',
    }
    sessionStoreMock.sessions = [{ id: 's1', messages: [localMsg] }]
    getSessionSnapshot.mockResolvedValue({
      data: { data: { messages: [
        buildBackendMessage({
          subagentContents: { 'sa-1': { content: '正文', reasoningContent: '' } },
        }),
      ] } },
    })

    const { syncFromSnapshot } = useSnapshotSync('s1')
    syncFromSnapshot()
    await flushSnapshotSync()

    expect(localMsg.subagentContents['sa-1'].content).toBe('正文')
  })
})
