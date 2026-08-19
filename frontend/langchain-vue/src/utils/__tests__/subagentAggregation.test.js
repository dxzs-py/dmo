/**
 * subagentAggregation 单元测试（聚合层顺序关联 Task 3）
 *
 * 覆盖：task 回退链（meta.task → spawn input.task（对象/JSON 字符串/threadId
 * 精确匹配/唯一 spawn 兜底/多 spawn 保守空串））、spawnToolCallId 仅来自 meta、
 * mapSubagentsBySpawnToolCall 分组与孤儿收集、formatSubagentTitle 截断与回退。
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect } from 'vitest'
import {
  buildSubagentsFromMessage,
  mapSubagentsBySpawnToolCall,
  formatSubagentTitle,
} from '../subagentAggregation.js'

/** 构造 spawn_sub_agent 主 agent 工具调用 */
const spawnTool = (id, input, { subagentThreadId = '' } = {}) => ({
  id,
  toolCallId: id,
  name: 'spawn_sub_agent',
  input,
  subagentThreadId,
})

/** 构造普通子代理工具调用 */
const subTool = (id, threadId) => ({
  id,
  toolCallId: id,
  name: 'read_file',
  subagentThreadId: threadId,
})

describe('subagentAggregation', () => {
  // ===== buildSubagentsFromMessage：task 来源 =====

  describe('buildSubagentsFromMessage', () => {
    it('task 来源：meta.task 优先（spawn input 仅作回退）', () => {
      const message = {
        toolCalls: [spawnTool('spawn-1', { task: '回退任务', threadId: 't1' })],
        subagentContents: {},
      }
      const [sa] = buildSubagentsFromMessage(message, [
        { threadId: 't1', task: '元数据任务' },
      ])
      expect(sa.task).toBe('元数据任务')
    })

    it('task 回退：meta.task 缺失时取 spawn input.task（对象 + threadId 精确匹配）', () => {
      const message = {
        toolCalls: [
          spawnTool('spawn-1', { task: '搜索资料', threadId: 't1' }),
          spawnTool('spawn-2', { task: '整理文档', threadId: 't2' }),
          subTool('sub-1', 't1'),
        ],
        subagentContents: {},
      }
      const subagents = buildSubagentsFromMessage(message, [
        { threadId: 't1' },
        { threadId: 't2' },
      ])
      const byThread = Object.fromEntries(subagents.map(sa => [sa.threadId, sa]))
      expect(byThread.t1.task).toBe('搜索资料')
      expect(byThread.t2.task).toBe('整理文档')
    })

    it('task 回退：input 为 JSON 字符串时正确解析', () => {
      const message = {
        toolCalls: [
          spawnTool('spawn-1', JSON.stringify({ task: '字符串任务', threadId: 't1' })),
          subTool('sub-1', 't1'),
        ],
        subagentContents: {},
      }
      const [sa] = buildSubagentsFromMessage(message, [{ threadId: 't1' }])
      expect(sa.task).toBe('字符串任务')
    })

    it('task 回退：spawn 无 threadId 关联时保守返回空串（不错配）', () => {
      // 根因修复：原「唯一 spawn 兜底」在存在嵌套子代理时会把父级 spawn 的 task
      // 错配给子代理（collectSpawnEntries 仅统计主 agent 工具）。权威来源是
      // meta.task（递归拉取已保证嵌套层级齐全），spawn 回退仅限精确 threadId 关联。
      const message = {
        toolCalls: [spawnTool('spawn-1', { task: '唯一任务' }), subTool('sub-1', 't1')],
        subagentContents: {},
      }
      const [sa] = buildSubagentsFromMessage(message, [{ threadId: 't1' }])
      expect(sa.task).toBe('')
    })

    it('task 回退：多 spawn 无 thread 关联时保守返回空串（不错配）', () => {
      const message = {
        toolCalls: [
          spawnTool('spawn-1', { task: '任务A' }),
          spawnTool('spawn-2', { task: '任务B' }),
          subTool('sub-1', 't1'),
        ],
        subagentContents: {},
      }
      const [sa] = buildSubagentsFromMessage(message, [{ threadId: 't1' }])
      expect(sa.task).toBe('')
    })

    it('task 回退：spawn input 非法 JSON / 缺 task 字段 → 空串', () => {
      const message = {
        toolCalls: [
          spawnTool('spawn-1', '{not valid json'),
          spawnTool('spawn-2', { description: '无 task 字段' }),
          subTool('sub-1', 't1'),
        ],
        subagentContents: {},
      }
      const [sa] = buildSubagentsFromMessage(message, [{ threadId: 't1' }])
      expect(sa.task).toBe('')
    })

    it('task 回退：子代理内部的 spawn 工具不作为主 agent 回退源', () => {
      const message = {
        toolCalls: [
          // 一层子代理 t1 内部调用 spawn（subagentThreadId 非空，属嵌套层）
          spawnTool('spawn-nested', { task: '嵌套任务' }, { subagentThreadId: 't1' }),
          subTool('sub-1', 't1'),
        ],
        subagentContents: {},
      }
      const [sa] = buildSubagentsFromMessage(message, [{ threadId: 't1' }])
      expect(sa.task).toBe('')
    })

    it('spawnToolCallId 仅来自 meta.spawnToolCallId（无 meta 时空串）', () => {
      const message = {
        toolCalls: [spawnTool('spawn-1', { task: 'x' }), subTool('sub-1', 't1')],
        subagentContents: {},
      }
      const withMeta = buildSubagentsFromMessage(message, [
        { threadId: 't1', spawnToolCallId: 'spawn-1' },
      ])
      expect(withMeta[0].spawnToolCallId).toBe('spawn-1')

      const withoutMeta = buildSubagentsFromMessage(message)
      expect(withoutMeta[0].spawnToolCallId).toBe('')
    })

    it('depth 采用后端序列化字段（meta.depth），缺失时兜底 1', () => {
      const message = {
        toolCalls: [subTool('sub-1', 't1'), subTool('sub-2', 't2')],
        subagentContents: {},
      }
      const list = buildSubagentsFromMessage(message, [
        { threadId: 't1', depth: 2 }, // 嵌套层（孙代理）
        { threadId: 't2' }, // 历史实例无 depth 字段
      ])
      expect(list[0].depth).toBe(2)
      expect(list[1].depth).toBe(1)
      // 全局序号与 depth 独立（顺序号仍从 1 递增）
      expect(list[0].order).toBe(1)
      expect(list[1].order).toBe(2)
    })
  })

  // ===== mapSubagentsBySpawnToolCall =====

  describe('mapSubagentsBySpawnToolCall', () => {
    it('按 spawnToolCallId 分组 + 孤儿收集', () => {
      const sa1 = { threadId: 't1', spawnToolCallId: 'spawn-1' }
      const sa2 = { threadId: 't2', spawnToolCallId: 'spawn-2' }
      const orphan = { threadId: 't3', spawnToolCallId: '' }
      const { bySpawnToolCallId, orphans } = mapSubagentsBySpawnToolCall([sa1, sa2, orphan])
      expect(bySpawnToolCallId.size).toBe(2)
      expect(bySpawnToolCallId.get('spawn-1')).toBe(sa1)
      expect(bySpawnToolCallId.get('spawn-2')).toBe(sa2)
      expect(orphans).toEqual([orphan])
    })

    it('全部孤儿 / 空输入安全', () => {
      const { bySpawnToolCallId, orphans } = mapSubagentsBySpawnToolCall([
        { threadId: 't1' },
        { threadId: 't2', spawnToolCallId: undefined },
      ])
      expect(bySpawnToolCallId.size).toBe(0)
      expect(orphans.length).toBe(2)

      const empty = mapSubagentsBySpawnToolCall([])
      expect(empty.bySpawnToolCallId.size).toBe(0)
      expect(empty.orphans.length).toBe(0)

      const nullInput = mapSubagentsBySpawnToolCall(null)
      expect(nullInput.bySpawnToolCallId.size).toBe(0)
      expect(nullInput.orphans.length).toBe(0)
    })
  })

  // ===== formatSubagentTitle =====

  describe('formatSubagentTitle', () => {
    it('task 非空且不超长 → 原样返回（含首尾空白修剪）', () => {
      expect(formatSubagentTitle('搜索最新论文', 'researcher')).toBe('搜索最新论文')
      expect(formatSubagentTitle('  带空白  ', 'researcher')).toBe('带空白')
    })

    it('task 超过 maxLength → 截断加省略号', () => {
      const longTask = 'a'.repeat(31)
      expect(formatSubagentTitle(longTask, 'name')).toBe(`${'a'.repeat(30)}…`)
      // 恰好等于 maxLength 不截断
      expect(formatSubagentTitle('b'.repeat(30), 'name')).toBe('b'.repeat(30))
    })

    it('自定义 maxLength', () => {
      expect(formatSubagentTitle('abcdef', 'name', 3)).toBe('abc…')
      expect(formatSubagentTitle('abc', 'name', 3)).toBe('abc')
    })

    it('task 空 → agentName → 子代理 回退链', () => {
      expect(formatSubagentTitle('', 'web-researcher')).toBe('web-researcher')
      expect(formatSubagentTitle(undefined, 'web-researcher')).toBe('web-researcher')
      expect(formatSubagentTitle('', '')).toBe('子代理')
      expect(formatSubagentTitle(null, undefined)).toBe('子代理')
      // task 为纯空白视为空
      expect(formatSubagentTitle('   ', 'name')).toBe('name')
    })
  })
})
