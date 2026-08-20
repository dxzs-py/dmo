/**
 * subagentHandlers 单元测试（C5/cq-04 Task 4）
 *
 * 被测：src/stores/sync/subagentHandlers.js —— createSubagentHandlers 工厂。
 * sessionStore / researchStore 以纯对象 + vi.fn() 注入；scheduleSubagentsRefresh
 * 通过 vi.mock('@/composables/useSubagents') 替换；logger 直接 spyOn 抑制输出。
 *
 * 覆盖分支：
 * 1. applySubagentContent：subagentThreadId 缺失丢弃（含 data.subagentThreadId 次级来源）；
 *    会话未加载丢弃；目标消息定位（payload.messageId 命中 / 未命中回退最后 assistant /
 *    无 assistant 丢弃）；content/reasoningContent 幂等追加合并（首片动态初始化、
 *    连续分片累计、空分片不追加、agentName/depth 透传与保留、多线程隔离、平铺 payload 读取）；
 *    活跃版本引用收敛（引用不一致时恢复为同一引用、别名成立时不二次追加）；
 * 2. handleSubagentStatusChange：subagentThreadId 存在触发 scheduleSubagentsRefresh
 *    （父线程 payload.sourceId 优先、回退 sessionId），缺失不触发；
 * 3. handleResearchStatusChange：sourceId 存在调用 researchStore.setTaskStatus，缺失不调用。
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest'
import { logger } from '@/utils/logger'
import { scheduleSubagentsRefresh } from '@/composables/useSubagents'
import { createSubagentHandlers } from '../subagentHandlers.js'

vi.mock('@/composables/useSubagents', () => ({
  scheduleSubagentsRefresh: vi.fn(),
}))

// 抑制被测模块的 logger 输出；直接 spy logger 对象避免依赖 import.meta.env.DEV
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
  vi.clearAllMocks()
})

// ── 测试工厂 ──────────────────────────────────────────────────────────────────
const buildAssistantMessage = (overrides = {}) => ({
  id: 'msg-local',
  backendId: 'msg-backend',
  role: 'assistant',
  ...overrides,
})

const buildSession = (id, messages) => ({ id, messages })

const buildHandlers = (sessions = []) => {
  const sessionStore = { sessions }
  const researchStore = { setTaskStatus: vi.fn() }
  return {
    researchStore,
    ...createSubagentHandlers({ sessionStore, researchStore }),
  }
}

describe('createSubagentHandlers', () => {
  describe('applySubagentContent', () => {
    it('缺少 subagentThreadId：丢弃分片，不写入消息', () => {
      const msg = buildAssistantMessage()
      const { applySubagentContent } = buildHandlers([buildSession('s1', [msg])])

      applySubagentContent('s1', { data: { content: '片段' } })

      expect(msg.subagentContents).toBeUndefined()
    })

    it('subagentThreadId 顶层缺失时从 data.subagentThreadId 读取', () => {
      const msg = buildAssistantMessage()
      const { applySubagentContent } = buildHandlers([buildSession('s1', [msg])])

      applySubagentContent('s1', { data: { subagentThreadId: 'sa-inner', content: '片段' } })

      expect(msg.subagentContents['sa-inner']).toStrictEqual({ content: '片段', reasoningContent: '' })
    })

    it('会话不存在：丢弃分片不抛异常', () => {
      const { applySubagentContent } = buildHandlers([])

      expect(() => applySubagentContent('s1', { subagentThreadId: 'sa-1', data: { content: 'x' } })).not.toThrow()
    })

    it('会话存在但 messages 缺失：丢弃分片不抛异常', () => {
      const { applySubagentContent } = buildHandlers([{ id: 's1' }])

      expect(() => applySubagentContent('s1', { subagentThreadId: 'sa-1', data: { content: 'x' } })).not.toThrow()
    })

    it('payload.messageId 命中：写入指定消息（backendId 匹配）', () => {
      const oldMsg = buildAssistantMessage({ id: 'a-old', backendId: 'b-old' })
      const lastMsg = buildAssistantMessage({ id: 'a-last', backendId: 'b-last' })
      const { applySubagentContent } = buildHandlers([buildSession('s1', [oldMsg, lastMsg])])

      applySubagentContent('s1', { messageId: 'b-old', subagentThreadId: 'sa-1', data: { content: 'x' } })

      expect(oldMsg.subagentContents['sa-1'].content).toBe('x')
      expect(lastMsg.subagentContents).toBeUndefined()
    })

    it('payload.messageId 未命中：回退最后一条 assistant 消息', () => {
      const oldMsg = buildAssistantMessage({ id: 'a-old', backendId: 'b-old' })
      const lastMsg = buildAssistantMessage({ id: 'a-last', backendId: 'b-last' })
      const { applySubagentContent } = buildHandlers([buildSession('s1', [oldMsg, lastMsg])])

      applySubagentContent('s1', { messageId: 'not-exist', subagentThreadId: 'sa-1', data: { content: 'x' } })

      expect(lastMsg.subagentContents['sa-1'].content).toBe('x')
      expect(oldMsg.subagentContents).toBeUndefined()
    })

    it('无 assistant 消息：丢弃分片（user 消息不被写入）', () => {
      const userMsg = { id: 'u1', role: 'user', content: '问题' }
      const { applySubagentContent } = buildHandlers([buildSession('s1', [userMsg])])

      applySubagentContent('s1', { subagentThreadId: 'sa-1', data: { content: 'x' } })

      expect(userMsg.subagentContents).toBeUndefined()
    })

    it('首片动态初始化：subagentContents 条目从零创建并透传 agentName/depth', () => {
      const msg = buildAssistantMessage()
      const { applySubagentContent } = buildHandlers([buildSession('s1', [msg])])

      applySubagentContent('s1', {
        subagentThreadId: 'sa-1',
        data: { content: '你好', reasoningContent: '思考', agentName: '研究员', depth: 2 },
      })

      expect(msg.subagentContents).toStrictEqual({
        'sa-1': { content: '你好', reasoningContent: '思考', agentName: '研究员', depth: 2 },
      })
    })

    it('连续分片：content/reasoningContent 各自追加累计（幂等合并）', () => {
      const msg = buildAssistantMessage()
      const { applySubagentContent } = buildHandlers([buildSession('s1', [msg])])

      applySubagentContent('s1', { subagentThreadId: 'sa-1', data: { content: 'a', reasoningContent: 'r1' } })
      applySubagentContent('s1', { subagentThreadId: 'sa-1', data: { content: 'b', reasoningContent: 'r2' } })

      expect(msg.subagentContents['sa-1']).toStrictEqual({ content: 'ab', reasoningContent: 'r1r2' })
    })

    it('单字段分片：仅 content / 仅 reasoningContent 时另一字段保持原值', () => {
      const msg = buildAssistantMessage()
      const { applySubagentContent } = buildHandlers([buildSession('s1', [msg])])

      applySubagentContent('s1', { subagentThreadId: 'sa-1', data: { content: 'a' } })
      applySubagentContent('s1', { subagentThreadId: 'sa-1', data: { reasoningContent: 'r' } })

      expect(msg.subagentContents['sa-1']).toStrictEqual({ content: 'a', reasoningContent: 'r' })
    })

    it('空分片：content 与 reasoningContent 均为空时直接返回，不追加也不初始化', () => {
      const msg = buildAssistantMessage()
      const { applySubagentContent } = buildHandlers([buildSession('s1', [msg])])

      applySubagentContent('s1', { subagentThreadId: 'sa-1', data: { agentName: '研究员' } })
      expect(msg.subagentContents).toBeUndefined()

      applySubagentContent('s1', { subagentThreadId: 'sa-1', data: { content: 'a' } })
      applySubagentContent('s1', { subagentThreadId: 'sa-1', data: {} })
      expect(msg.subagentContents['sa-1']).toStrictEqual({ content: 'a', reasoningContent: '' })
    })

    it('元数据保留：后续分片缺 agentName/depth 时保留已有值（展开合并）', () => {
      const msg = buildAssistantMessage()
      const { applySubagentContent } = buildHandlers([buildSession('s1', [msg])])

      applySubagentContent('s1', {
        subagentThreadId: 'sa-1',
        data: { content: 'a', agentName: '研究员', depth: 1 },
      })
      applySubagentContent('s1', { subagentThreadId: 'sa-1', data: { content: 'b' } })

      expect(msg.subagentContents['sa-1']).toStrictEqual({
        content: 'ab',
        reasoningContent: '',
        agentName: '研究员',
        depth: 1,
      })
    })

    it('depth 类型守卫：number（含 0）写入，字符串不写入', () => {
      const msg = buildAssistantMessage()
      const { applySubagentContent } = buildHandlers([buildSession('s1', [msg])])

      applySubagentContent('s1', { subagentThreadId: 'sa-1', data: { content: 'a', depth: 0 } })
      applySubagentContent('s1', { subagentThreadId: 'sa-2', data: { content: 'b', depth: '2' } })

      expect(msg.subagentContents['sa-1'].depth).toBe(0)
      expect('depth' in msg.subagentContents['sa-2']).toBe(false)
    })

    it('多线程隔离：不同 subagentThreadId 各自独立累计', () => {
      const msg = buildAssistantMessage()
      const { applySubagentContent } = buildHandlers([buildSession('s1', [msg])])

      applySubagentContent('s1', { subagentThreadId: 'sa-1', data: { content: 'a1' } })
      applySubagentContent('s1', { subagentThreadId: 'sa-2', data: { content: 'a2' } })
      applySubagentContent('s1', { subagentThreadId: 'sa-1', data: { content: 'b1' } })

      expect(msg.subagentContents['sa-1'].content).toBe('a1b1')
      expect(msg.subagentContents['sa-2'].content).toBe('a2')
    })

    it('平铺 payload（无 data 包裹）：从顶层读取字段', () => {
      const msg = buildAssistantMessage()
      const { applySubagentContent } = buildHandlers([buildSession('s1', [msg])])

      applySubagentContent('s1', { subagentThreadId: 'sa-1', content: '平铺内容' })

      expect(msg.subagentContents['sa-1']).toStrictEqual({ content: '平铺内容', reasoningContent: '' })
    })

    it('活跃版本收敛：版本与消息的 subagentContents 引用不一致时恢复为同一引用', () => {
      const msg = buildAssistantMessage({ versions: [{ subagentContents: undefined }], currentVersion: 0 })
      const { applySubagentContent } = buildHandlers([buildSession('s1', [msg])])

      applySubagentContent('s1', { subagentThreadId: 'sa-1', data: { content: 'a' } })

      expect(msg.versions[0].subagentContents).toBe(msg.subagentContents)
      expect(msg.versions[0].subagentContents['sa-1'].content).toBe('a')
    })

    it('活跃版本别名成立：不替换引用且内容只追加一次', () => {
      const shared = {}
      const msg = buildAssistantMessage({
        subagentContents: shared,
        versions: [{ subagentContents: shared }],
        currentVersion: 0,
      })
      const { applySubagentContent } = buildHandlers([buildSession('s1', [msg])])

      applySubagentContent('s1', { subagentThreadId: 'sa-1', data: { content: 'a' } })

      expect(msg.subagentContents).toBe(shared)
      expect(msg.versions[0].subagentContents).toBe(shared)
      expect(shared['sa-1']).toStrictEqual({ content: 'a', reasoningContent: '' })
    })

    it('无 versions / 空版本列表：正常写入不报错', () => {
      const msgNoVer = buildAssistantMessage({ id: 'a-no-ver', backendId: 'b-no-ver' })
      const msgEmptyVer = buildAssistantMessage({
        id: 'a-empty-ver',
        backendId: 'b-empty-ver',
        versions: [],
        currentVersion: 0,
      })
      const { applySubagentContent } = buildHandlers([buildSession('s1', [msgNoVer, msgEmptyVer])])

      applySubagentContent('s1', { messageId: 'b-no-ver', subagentThreadId: 'sa-1', data: { content: 'a' } })
      applySubagentContent('s1', { messageId: 'b-empty-ver', subagentThreadId: 'sa-2', data: { content: 'b' } })

      expect(msgNoVer.subagentContents['sa-1'].content).toBe('a')
      expect(msgEmptyVer.subagentContents['sa-2'].content).toBe('b')
    })
  })

  describe('handleSubagentStatusChange', () => {
    it('subagentThreadId 存在：触发子代理刷新，父线程取 payload.sourceId', () => {
      const { handleSubagentStatusChange } = buildHandlers([])

      handleSubagentStatusChange('s1', {
        subagentThreadId: 'sa-1',
        sourceId: 'parent-1',
        data: { status: 'completed' },
      })

      expect(scheduleSubagentsRefresh).toHaveBeenCalledWith('parent-1')
    })

    it('sourceId 缺失：父线程回退 sessionId', () => {
      const { handleSubagentStatusChange } = buildHandlers([])

      handleSubagentStatusChange('s1', { subagentThreadId: 'sa-1', data: { status: 'running' } })

      expect(scheduleSubagentsRefresh).toHaveBeenCalledWith('s1')
    })

    it('无 subagentThreadId：不触发刷新', () => {
      const { handleSubagentStatusChange } = buildHandlers([])

      handleSubagentStatusChange('s1', { sourceId: 'parent-1' })

      expect(scheduleSubagentsRefresh).not.toHaveBeenCalled()
    })
  })

  describe('handleResearchStatusChange', () => {
    it('sourceId 存在：调用 researchStore.setTaskStatus(taskId, payload)', () => {
      const { researchStore, handleResearchStatusChange } = buildHandlers([])

      handleResearchStatusChange('s1', { sourceId: 'task-1', status: 'running' })

      expect(researchStore.setTaskStatus).toHaveBeenCalledWith('task-1', { sourceId: 'task-1', status: 'running' })
    })

    it('无 sourceId：不调用 setTaskStatus', () => {
      const { researchStore, handleResearchStatusChange } = buildHandlers([])

      handleResearchStatusChange('s1', { status: 'running' })

      expect(researchStore.setTaskStatus).not.toHaveBeenCalled()
    })
  })
})
