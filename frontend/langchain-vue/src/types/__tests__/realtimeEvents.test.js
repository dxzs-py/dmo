/**
 * 实时事件枚举一致性测试（Spec: consistency-audit-fixes / R2）
 *
 * 背景：`types/realtimeEvents.js` 的 EventType 此前未导出且滞后于后端
 * `common/event_schema.py`，派发逻辑散落字符串字面量。本次已补齐 8 项并改为
 * 枚举引用，本测试防止其再次退化。
 *
 * 覆盖：
 * - 补齐的 8 项均已登记（防止被误删）
 * - 枚举值无重复（重复值会让 Set/switch 静默失效）
 * - TASK_STATUS_CHANGE / TASK_STATUS_CHANGED 的**值**不被写反（已实证的命名陷阱）
 *
 * 局限（有意为之，非遗漏）：本测试只守护前端枚举自身。后端新增事件仍需**人工**
 * 同步到前端——自动同步需引入构建期代码生成或 CI 校验，成本高于当前收益，
 * 已在 design.md 列为非目标。
 *
 * 运行方式（vitest）：npx vitest run
 */
import { describe, it, expect } from 'vitest'

import { EventType } from '../realtimeEvents'

describe('realtimeEvents.EventType', () => {
  it('应导出事件类型枚举', () => {
    expect(EventType).toBeTypeOf('object')
    expect(Object.keys(EventType).length).toBeGreaterThan(0)
  })

  it('应包含补齐的 8 个此前缺失的事件类型', () => {
    const addedMembers = {
      STREAM_SUBAGENT_CONTENT: 'stream_subagent_content',
      SUBAGENT_STATUS_CHANGE: 'subagent_status_change',
      MESSAGE_FINALIZED: 'message_finalized',
      TASK_CREATED: 'task_created',
      TASK_DELETED: 'task_deleted',
      TASK_STATUS_CHANGED: 'task_status_changed',
      TASK_PROGRESS: 'task_progress',
      TASK_STATUS_CHANGE: 'status_change',
    }
    for (const [member, value] of Object.entries(addedMembers)) {
      expect(EventType[member], `缺少成员 ${member}`).toBe(value)
    }
  })

  it('枚举值不应重复', () => {
    const values = Object.values(EventType)
    expect(new Set(values).size).toBe(values.length)
  })

  it('TASK_STATUS_CHANGE 与 TASK_STATUS_CHANGED 的值不得互换（命名陷阱回归）', () => {
    // 成员名仅差一个 D，但值完全不同：
    // - TASK_STATUS_CHANGED = 'task_status_changed'（任务进入终态 → 列表刷新）
    // - TASK_STATUS_CHANGE  = 'status_change'（执行器运行中细粒度状态 → 详情标签）
    expect(EventType.TASK_STATUS_CHANGED).toBe('task_status_changed')
    expect(EventType.TASK_STATUS_CHANGE).toBe('status_change')
    expect(EventType.TASK_STATUS_CHANGE).not.toBe(EventType.TASK_STATUS_CHANGED)
  })
})
