/**
 * handleTaskEvent 单元测试（FE-1 回归）
 *
 * 被测：src/stores/sync/handleTaskEvent.js —— createHandleTaskEvent 工厂。
 *
 * 背景：该模块 switch 分支引用 EventType.TASK_STATUS_CHANGE / STREAM_SUBAGENT_CONTENT /
 * SUBAGENT_STATUS_CHANGE，但此前头部 import 漏导 EventType → 首条对应事件到达即
 * ReferenceError，task 通道 WS 同步失效。本测试用 realtimeEvents 导出的常量构造
 * task 通道事件走 handleTaskEvent，断言不抛错且产生预期状态效果。
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest'
import { logger } from '@/utils/logger'
import { EventType } from '@/types/realtimeEvents'
import { createHandleTaskEvent } from '../handleTaskEvent.js'

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
// 与后端 _publish_to_task_async 对齐：task_id 注入事件顶层，payload 内不含 taskId。
// 入站经 useRealtimeSync dispatcher toCamelCase 后顶层即为 camelCase 的 taskId。
const buildTaskEvent = (type, payload = {}, overrides = {}) => ({
  type,
  taskId: 'task-1',
  payload,
  ...overrides,
})

const buildCtx = () => {
  const researchStore = {
    tasks: new Map(),
    setTaskStatus: vi.fn(),
    setTaskSubagentContent: vi.fn(),
    updateTaskFromEvent: vi.fn(),
  }
  const handleToolCallEvent = vi.fn()
  const handleApprovalEvent = vi.fn()
  const ctx = { researchStore, handleToolCallEvent, handleApprovalEvent, onWorkflowEvent: undefined }
  return { researchStore, handleToolCallEvent, handleApprovalEvent, ...createHandleTaskEvent(ctx) }
}

describe('createHandleTaskEvent', () => {
  it('status_change 事件（EventType.TASK_STATUS_CHANGE）路由到 researchStore.setTaskStatus 且不抛错', async () => {
    const { researchStore, handleTaskEvent } = buildCtx()
    const payload = { status: 'running', currentStep: '检索文献' }

    await expect(
      handleTaskEvent(buildTaskEvent(EventType.TASK_STATUS_CHANGE, payload))
    ).resolves.toBeUndefined()

    expect(researchStore.setTaskStatus).toHaveBeenCalledTimes(1)
    expect(researchStore.setTaskStatus).toHaveBeenCalledWith('task-1', payload)
  })

  it('stream_subagent_content 事件（EventType.STREAM_SUBAGENT_CONTENT）写入子代理图层且不抛错', async () => {
    const { researchStore, handleTaskEvent } = buildCtx()
    const payload = {
      subagentThreadId: 'sa-1',
      data: { content: '片段', reasoningContent: '思考' },
    }

    await expect(
      handleTaskEvent(buildTaskEvent(EventType.STREAM_SUBAGENT_CONTENT, payload))
    ).resolves.toBeUndefined()

    expect(researchStore.setTaskSubagentContent).toHaveBeenCalledTimes(1)
    expect(researchStore.setTaskSubagentContent).toHaveBeenCalledWith('task-1', 'sa-1', '片段', '思考')
  })
})
