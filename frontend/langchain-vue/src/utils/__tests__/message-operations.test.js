import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  mergeMessageFromBackend,
  findToolCallInMap,
  setApprovalToToolCallInMap,
  addOrUpdateToolCallInMap,
  updateOrAddToolResultInMap,
  updateApprovalStateInMap,
  updateToolCallStatusInMap,
  isNonEmptyParams,
  getInterruptId,
} from '../message-operations'
import { StreamState, ToolCallStatus, ApprovalState } from '@/types'

// 屏蔽 logger 输出，避免测试日志噪音
vi.mock('@/utils/logger', () => ({
  logger: {
    log: () => {},
    info: () => {},
    warn: () => {},
    error: () => {},
    debug: () => {},
  },
}))

describe('mergeMessageFromBackend', () => {
  let existingMsg

  beforeEach(() => {
    existingMsg = {
      id: 'msg-1',
      role: 'assistant',
      content: 'local content',
      toolCalls: [],
      sources: [],
      reasoning: null,
      suggestions: null,
      context: null,
      tokenCount: 10,
      responseTime: 100,
      model: 'deepseek-v4-pro',
      backendId: 100,
      streamState: StreamState.STREAMING,
      isStreaming: true,
      versions: {
        0: {
          id: 'msg-1',
          content: 'local content',
          toolCalls: [],
          sources: [],
          reasoning: null,
          suggestions: null,
          context: null,
          streamState: StreamState.STREAMING,
          isStreaming: true,
        },
      },
      currentVersion: 0,
    }
  })

  it('应在 STREAMING 状态下保护 content 不被较短后端内容覆盖', () => {
    const backendMsg = { content: '' }
    const merged = mergeMessageFromBackend(existingMsg, backendMsg)
    expect(merged.content).toBe('local content')
    expect(merged.versions[0].content).toBe('local content')
  })

  it('应在 INTERRUPTED 状态下保护 content 不被覆盖', () => {
    existingMsg.streamState = StreamState.INTERRUPTED
    existingMsg.isStreaming = false
    existingMsg.versions[0].streamState = StreamState.INTERRUPTED
    existingMsg.versions[0].isStreaming = false

    const backendMsg = { content: 'shorter' }
    const merged = mergeMessageFromBackend(existingMsg, backendMsg)
    expect(merged.content).toBe('local content')
    expect(merged.versions[0].content).toBe('local content')
  })

  it('应在 FINALIZING 状态下保护 content 不被覆盖', () => {
    existingMsg.streamState = StreamState.FINALIZING
    existingMsg.isStreaming = true
    existingMsg.versions[0].streamState = StreamState.FINALIZING
    existingMsg.versions[0].isStreaming = true

    const backendMsg = { content: 'old snapshot' }
    const merged = mergeMessageFromBackend(existingMsg, backendMsg)
    expect(merged.content).toBe('local content')
    expect(merged.versions[0].content).toBe('local content')
  })

  it('应在 COMPLETED 状态下允许较长后端 content 覆盖', () => {
    existingMsg.streamState = StreamState.COMPLETED
    existingMsg.isStreaming = false
    existingMsg.versions[0].streamState = StreamState.COMPLETED
    existingMsg.versions[0].isStreaming = false

    const backendMsg = { content: 'backend final content which is longer' }
    const merged = mergeMessageFromBackend(existingMsg, backendMsg)
    // COMPLETED 是保护态，但后端 content 更长时允许更新 mapped.content
    expect(merged.content).toBe('backend final content which is longer')
    // 保护态下 versions[0].content 不被更新（delete versionMapped.content）
    expect(merged.versions[0].content).toBe('local content')
  })

  it('流式期间仍应更新非内容字段（tokenCount/responseTime/model/backendId）', () => {
    const backendMsg = {
      content: 'should be ignored',
      tokenCount: 999,
      responseTime: 888,
      model: 'gpt-4',
      backendId: 200,
    }
    const merged = mergeMessageFromBackend(existingMsg, backendMsg)
    // STREAMING 是保护态，但后端 content 更长（17 > 13）时允许覆盖
    expect(merged.content).toBe('should be ignored')
    expect(merged.tokenCount).toBe(999)
    expect(merged.responseTime).toBe(888)
    expect(merged.model).toBe('gpt-4')
    expect(merged.backendId).toBe(200)
  })

  it('流式期间应保护 toolCalls 不被较少的后端 toolCalls 覆盖', () => {
    existingMsg.toolCalls = [{ id: 'tc-1', name: 'shell_exec', status: 'running' }]
    existingMsg.sources = [{ title: 'local source' }]
    // reasoning 保护逻辑检查 reasoning.content 长度，后端更长时允许覆盖
    // 使用更长的本地 reasoning 以触发保护
    existingMsg.reasoning = { content: 'local reasoning is longer than backend' }
    existingMsg.suggestions = ['local suggestion']
    existingMsg.context = { key: 'local' }
    existingMsg.versions[0].toolCalls = existingMsg.toolCalls
    existingMsg.versions[0].sources = existingMsg.sources
    existingMsg.versions[0].reasoning = existingMsg.reasoning
    existingMsg.versions[0].suggestions = existingMsg.suggestions
    existingMsg.versions[0].context = existingMsg.context

    // 后端 toolCalls 为空数组（数量 < 本地 1 个），不应覆盖
    const backendMsg = {
      toolCalls: [],
      sources: [{ title: 'backend source' }],
      reasoning: { content: 'backend reasoning' },
      suggestions: ['backend suggestion'],
      context: { key: 'backend' },
    }
    const merged = mergeMessageFromBackend(existingMsg, backendMsg)
    expect(merged.toolCalls).toEqual([{ id: 'tc-1', name: 'shell_exec', status: 'running' }])
    expect(merged.sources).toEqual([{ title: 'local source' }])
    // 本地 reasoning content 更长，后端不更长时保护
    expect(merged.reasoning).toEqual({ content: 'local reasoning is longer than backend' })
    expect(merged.suggestions).toEqual(['local suggestion'])
    expect(merged.context).toEqual({ key: 'local' })
  })

  it('合并 toolCalls 时应保留本地更完整的 result/status', () => {
    existingMsg.streamState = StreamState.COMPLETED
    existingMsg.isStreaming = false
    existingMsg.versions[0].streamState = StreamState.COMPLETED
    existingMsg.versions[0].isStreaming = false
    existingMsg.toolCalls = [
      { id: 'tc-1', name: 'shell_exec', status: 'completed', result: 'done', tool_call_id: 'call-1' },
    ]
    existingMsg.versions[0].toolCalls = existingMsg.toolCalls

    const backendMsg = {
      toolCalls: [
        { id: 'tc-1', name: 'shell_exec', status: 'running', result: null, tool_call_id: 'call-1' },
      ],
    }
    const merged = mergeMessageFromBackend(existingMsg, backendMsg)
    expect(merged.toolCalls[0].status).toBe('completed')
    expect(merged.toolCalls[0].result).toBe('done')
  })

  it('当本地 content 为空而后端有内容时，即使流式中也应允许覆盖空内容', () => {
    existingMsg.content = ''
    existingMsg.versions[0].content = ''

    const backendMsg = { content: 'backend content' }
    const merged = mergeMessageFromBackend(existingMsg, backendMsg)
    // 保护态下 localContentEmpty=true 时允许后端覆盖
    expect(merged.content).toBe('backend content')
  })

  it('FINALIZING 状态下后端 toolCalls 为空数组时不应覆盖本地 toolCalls', () => {
    existingMsg.streamState = StreamState.FINALIZING
    existingMsg.isStreaming = true
    existingMsg.versions[0].streamState = StreamState.FINALIZING
    existingMsg.versions[0].isStreaming = true
    existingMsg.toolCalls = [
      { id: 'tc-1', name: 'shell_exec', status: 'completed', result: 'done', tool_call_id: 'call-1' },
    ]
    existingMsg.versions[0].toolCalls = existingMsg.toolCalls

    const backendMsg = { toolCalls: [] }
    const merged = mergeMessageFromBackend(existingMsg, backendMsg)
    expect(merged.toolCalls).toEqual([
      { id: 'tc-1', name: 'shell_exec', status: 'completed', result: 'done', tool_call_id: 'call-1' },
    ])
  })

  it('FINALIZING 状态下后端 toolCalls 更完整时应允许合并', () => {
    existingMsg.streamState = StreamState.FINALIZING
    existingMsg.isStreaming = true
    existingMsg.versions[0].streamState = StreamState.FINALIZING
    existingMsg.versions[0].isStreaming = true
    existingMsg.toolCalls = [
      { id: 'tc-1', name: 'shell_exec', status: 'running', result: null, tool_call_id: 'call-1' },
    ]
    existingMsg.versions[0].toolCalls = existingMsg.toolCalls

    const backendMsg = {
      toolCalls: [
        { id: 'tc-1', name: 'shell_exec', status: 'completed', result: 'done', tool_call_id: 'call-1' },
        { id: 'tc-2', name: 'web_search', status: 'completed', result: 'found', tool_call_id: 'call-2' },
      ],
    }
    const merged = mergeMessageFromBackend(existingMsg, backendMsg)
    expect(merged.toolCalls.length).toBe(2)
    expect(merged.toolCalls[0].status).toBe('completed')
    expect(merged.toolCalls[0].result).toBe('done')
    expect(merged.toolCalls[1].name).toBe('web_search')
  })

  it('INTERRUPTED 状态下后端 toolCalls 较少时不应覆盖本地 toolCalls', () => {
    existingMsg.streamState = StreamState.INTERRUPTED
    existingMsg.isStreaming = false
    existingMsg.versions[0].streamState = StreamState.INTERRUPTED
    existingMsg.versions[0].isStreaming = false
    existingMsg.toolCalls = [
      { id: 'tc-1', name: 'shell_exec', status: 'completed', result: 'done', tool_call_id: 'call-1' },
      { id: 'tc-2', name: 'web_search', status: 'running', result: null, tool_call_id: 'call-2' },
    ]
    existingMsg.versions[0].toolCalls = existingMsg.toolCalls

    // 后端快照只有 1 个 toolCall（旧快照），不应覆盖本地 2 个
    const backendMsg = {
      toolCalls: [
        { id: 'tc-1', name: 'shell_exec', status: 'running', result: null, tool_call_id: 'call-1' },
      ],
    }
    const merged = mergeMessageFromBackend(existingMsg, backendMsg)
    expect(merged.toolCalls.length).toBe(2)
    expect(merged.toolCalls[0].status).toBe('completed')
    expect(merged.toolCalls[1].name).toBe('web_search')
  })
})

// ==================== SubTask 9.6: findToolCallInMap ====================

describe('SubTask 9.6 findToolCallInMap', () => {
  let toolCallMap

  beforeEach(() => {
    toolCallMap = new Map()
  })

  it('直接查找 → toolCallMap.has(toolCallId) 命中', () => {
    const tc = { id: 'tc-1', name: 'execute', status: ToolCallStatus.RUNNING }
    toolCallMap.set('tc-1', tc)

    const { toolCall, key } = findToolCallInMap(toolCallMap, 'tc-1')
    expect(toolCall).toBe(tc)
    expect(key).toBe('tc-1')
  })

  it('altId 查找 → data.tool_call_id 匹配', () => {
    const tc = { id: 'tc-real', name: 'execute', status: ToolCallStatus.RUNNING }
    toolCallMap.set('tc-real', tc)

    // toolCallId 是 interrupt_id，与 Map key 不一致
    // 但 data.tool_call_id 指向 Map key
    const { toolCall, key } = findToolCallInMap(toolCallMap, 'interrupt-x', {
      tool_call_id: 'tc-real',
    })
    expect(toolCall).toBe(tc)
    expect(key).toBe('tc-real')
  })

  it('altId 查找 → data.tool_call_id 匹配', () => {
    const tc = { id: 'tc-real', name: 'execute', status: ToolCallStatus.RUNNING }
    toolCallMap.set('tc-real', tc)

    const { toolCall, key } = findToolCallInMap(toolCallMap, 'interrupt-y', {
      tool_call_id: 'tc-real',
    })
    expect(toolCall).toBe(tc)
    expect(key).toBe('tc-real')
  })

  it('遍历兜底查找 → tc.id 匹配 toolCallId', () => {
    // Map key 与 tc.id 不一致（罕见场景）
    const tc = { id: 'tc-target', name: 'execute', status: ToolCallStatus.RUNNING }
    toolCallMap.set('wrong-key', tc)

    const { toolCall } = findToolCallInMap(toolCallMap, 'tc-target')
    expect(toolCall).toBe(tc)
  })

  it('遍历兜底查找 → tc.tool_call_id 匹配 toolCallId', () => {
    const tc = {
      id: 'tc-2',
      tool_call_id: 'call-llm-2',
      name: 'execute',
      status: ToolCallStatus.RUNNING,
    }
    toolCallMap.set('tc-2', tc)

    const { toolCall } = findToolCallInMap(toolCallMap, 'call-llm-2')
    expect(toolCall).toBe(tc)
  })

  it('遍历兜底查找 → tc.tool_call_id 匹配 data.tool_call_id', () => {
    const tc = {
      id: 'tc-3',
      tool_call_id: 'call-llm-3',
      name: 'execute',
      status: ToolCallStatus.RUNNING,
    }
    toolCallMap.set('tc-3', tc)

    const { toolCall } = findToolCallInMap(toolCallMap, 'interrupt-z', {
      tool_call_id: 'call-llm-3',
    })
    expect(toolCall).toBe(tc)
  })

  it('不存在 → 返回 { toolCall: null, key: null }', () => {
    const result = findToolCallInMap(toolCallMap, 'nonexistent')
    expect(result.toolCall).toBeNull()
    expect(result.key).toBeNull()
  })

  it('空 toolCallMap → 返回 null', () => {
    const result = findToolCallInMap(new Map(), 'tc-1')
    expect(result.toolCall).toBeNull()
  })

  it('空 toolCallId → 返回 null', () => {
    toolCallMap.set('tc-1', { id: 'tc-1' })
    const result = findToolCallInMap(toolCallMap, '')
    expect(result.toolCall).toBeNull()
  })

  it('null toolCallMap → 返回 null', () => {
    const result = findToolCallInMap(null, 'tc-1')
    expect(result.toolCall).toBeNull()
  })
})

// ==================== SubTask 9.7: setApprovalToToolCallInMap（_synthetic 占位） ====================

describe('SubTask 9.7 setApprovalToToolCallInMap - _synthetic 占位机制', () => {
  let toolCallMap

  beforeEach(() => {
    toolCallMap = new Map()
  })

  it('toolCall 存在 → 附加 approval，返回 false（非占位）', () => {
    const tc = {
      id: 'tc-exist',
      name: 'execute',
      status: ToolCallStatus.RUNNING,
      parameters: { command: 'ls' },
    }
    toolCallMap.set('tc-exist', tc)

    const isSynthetic = setApprovalToToolCallInMap(toolCallMap, 'tc-exist', {
      interrupt_id: 'tc-exist',
      tool_name: 'execute',
      operation: 'ls',
      state: ApprovalState.PENDING,
    })

    expect(isSynthetic).toBe(false)
    expect(tc.approval).toBeTruthy()
    expect(tc.approval.state).toBe(ApprovalState.PENDING)
    expect(tc.approval.tool_name).toBe('execute')
    // _synthetic 标记不应存在
    expect(tc._synthetic).toBeUndefined()
  })

  it('toolCall 不存在 → 创建 _synthetic 占位，返回 true', () => {
    const toolCallId = 'interrupt-synthetic'
    const isSynthetic = setApprovalToToolCallInMap(toolCallMap, toolCallId, {
      interrupt_id: toolCallId,
      tool_name: 'execute',
      operation: 'rm -rf /tmp',
      state: ApprovalState.PENDING,
    })

    expect(isSynthetic).toBe(true)
    const synthetic = toolCallMap.get(toolCallId)
    expect(synthetic).toBeTruthy()
    expect(synthetic._synthetic).toBe(true)
    expect(synthetic.name).toBe('execute')
    expect(synthetic.status).toBe(ToolCallStatus.RUNNING)
    expect(synthetic.approval).toBeTruthy()
    expect(synthetic.approval.state).toBe(ApprovalState.PENDING)
  })

  it('占位条目 → operation 提取为 parameters.command', () => {
    const toolCallId = 'interrupt-op'
    setApprovalToToolCallInMap(toolCallMap, toolCallId, {
      interrupt_id: toolCallId,
      tool_name: 'execute',
      operation: 'ls -la',
      state: ApprovalState.PENDING,
    })

    const synthetic = toolCallMap.get(toolCallId)
    expect(synthetic.parameters).toEqual({ command: 'ls -la' })
    expect(synthetic.args).toEqual({ command: 'ls -la' })
  })

  it('占位条目 → command 字段提取为 parameters.command', () => {
    const toolCallId = 'interrupt-cmd'
    setApprovalToToolCallInMap(toolCallMap, toolCallId, {
      interrupt_id: toolCallId,
      tool_name: 'execute',
      command: 'pwd',
      state: ApprovalState.PENDING,
    })

    const synthetic = toolCallMap.get(toolCallId)
    expect(synthetic.parameters).toEqual({ command: 'pwd' })
  })

  it('占位条目 → 携带 tool_call_id', () => {
    const toolCallId = 'interrupt-llm'
    const llmId = 'call-llm-xyz'
    setApprovalToToolCallInMap(toolCallMap, toolCallId, {
      interrupt_id: toolCallId,
      tool_call_id: llmId,
      tool_name: 'execute',
      operation: 'ls',
      state: ApprovalState.PENDING,
    })

    const synthetic = toolCallMap.get(toolCallId)
    // 占位条目顶层 tool_call_id = toolCallId（interrupt_id），
    // approvalData.tool_call_id 保存在 approval.tool_call_id 中
    expect(synthetic.tool_call_id).toBe(toolCallId)
    expect(synthetic.approval.tool_call_id).toBe(llmId)
    expect(synthetic.interrupt_id).toBe(toolCallId)
  })

  it('占位条目 → 通过 addOrUpdateToolCallInMap 合并后清除 _synthetic 标记', () => {
    const toolCallId = 'interrupt-merge'
    // 1. 创建占位条目
    setApprovalToToolCallInMap(toolCallMap, toolCallId, {
      interrupt_id: toolCallId,
      tool_name: 'execute',
      operation: 'ls',
      state: ApprovalState.PENDING,
    })

    const synthetic = toolCallMap.get(toolCallId)
    expect(synthetic._synthetic).toBe(true)
    expect(synthetic.approval).toBeTruthy()

    // 2. tool 事件到达，更新占位条目
    addOrUpdateToolCallInMap(toolCallMap, {
      id: toolCallId,
      tool_call_id: toolCallId,
      name: 'execute',
      state: 'input-available',
      status: ToolCallStatus.RUNNING,
      parameters: { command: 'ls' },
    })

    // _synthetic 标记应被清除
    const merged = toolCallMap.get(toolCallId)
    expect(merged._synthetic).toBeUndefined()
    // approval 字段应保留
    expect(merged.approval).toBeTruthy()
    expect(merged.approval.state).toBe(ApprovalState.PENDING)
    // 新字段应更新
    expect(merged.status).toBe(ToolCallStatus.RUNNING)
    expect(merged.parameters).toEqual({ command: 'ls' })
  })

  it('空参数 → setApprovalToToolCallInMap 返回 false', () => {
    expect(setApprovalToToolCallInMap(null, 'tc-1', {})).toBe(false)
    expect(setApprovalToToolCallInMap(toolCallMap, '', {})).toBe(false)
    expect(setApprovalToToolCallInMap(toolCallMap, 'tc-1', null)).toBe(false)
  })
})

// ==================== addOrUpdateToolCallInMap ====================

describe('addOrUpdateToolCallInMap', () => {
  let toolCallMap

  beforeEach(() => {
    toolCallMap = new Map()
  })

  it('新建条目 → 正确存储所有字段', () => {
    const toolCallId = addOrUpdateToolCallInMap(toolCallMap, {
      id: 'tc-new',
      tool_call_id: 'tc-new',
      name: 'execute',
      state: 'input-available',
      status: ToolCallStatus.RUNNING,
      parameters: { command: 'ls' },
    })

    expect(toolCallId).toBe('tc-new')
    const tc = toolCallMap.get('tc-new')
    expect(tc.id).toBe('tc-new')
    expect(tc.name).toBe('execute')
    expect(tc.status).toBe(ToolCallStatus.RUNNING)
    expect(tc.parameters).toEqual({ command: 'ls' })
    expect(tc.args).toEqual({ command: 'ls' })
  })

  it('toolCallId 优先级：tool_call_id > tool_call_id > id', () => {
    addOrUpdateToolCallInMap(toolCallMap, {
      id: 'id-1',
      tool_call_id: 'tcid-1',
      tool_call_id: 'llmid-1',
      name: 'execute',
    })
    // 应使用 tool_call_id 作为 key
    expect(toolCallMap.has('llmid-1')).toBe(true)
    expect(toolCallMap.has('tcid-1')).toBe(false)
    expect(toolCallMap.has('id-1')).toBe(false)
  })

  it('更新非占位条目 → 保留已有 approval', () => {
    const tc = {
      id: 'tc-update',
      name: 'execute',
      status: ToolCallStatus.RUNNING,
      parameters: { command: 'old' },
      approval: { state: ApprovalState.PENDING, tool_name: 'execute' },
    }
    toolCallMap.set('tc-update', tc)

    addOrUpdateToolCallInMap(toolCallMap, {
      id: 'tc-update',
      tool_call_id: 'tc-update',
      name: 'execute',
      status: ToolCallStatus.COMPLETED,
    })

    const updated = toolCallMap.get('tc-update')
    // approval 应保留
    expect(updated.approval).toBeTruthy()
    expect(updated.approval.state).toBe(ApprovalState.PENDING)
  })

  it('parameters 空对象 → 回退到 existing.parameters', () => {
    const tc = {
      id: 'tc-params',
      name: 'execute',
      parameters: { command: 'existing' },
    }
    toolCallMap.set('tc-params', tc)

    addOrUpdateToolCallInMap(toolCallMap, {
      id: 'tc-params',
      tool_call_id: 'tc-params',
      parameters: {}, // 空对象
    })

    const updated = toolCallMap.get('tc-params')
    // 应保留 existing.parameters
    expect(updated.parameters).toEqual({ command: 'existing' })
  })

  it('无 toolCallId → 返回 null', () => {
    const result = addOrUpdateToolCallInMap(toolCallMap, { name: 'execute' })
    expect(result).toBeNull()
  })

  it('null toolCallMap → 返回 null', () => {
    const result = addOrUpdateToolCallInMap(null, { id: 'tc-1' })
    expect(result).toBeNull()
  })
})

// ==================== updateOrAddToolResultInMap ====================

describe('updateOrAddToolResultInMap', () => {
  let toolCallMap

  beforeEach(() => {
    toolCallMap = new Map()
  })

  it('existing 分支 → 更新 result/state/status', () => {
    const tc = {
      id: 'tc-result',
      name: 'execute',
      status: ToolCallStatus.RUNNING,
    }
    toolCallMap.set('tc-result', tc)

    updateOrAddToolResultInMap(toolCallMap, {
      id: 'tc-result',
      tool_call_id: 'tc-result',
      result: 'done',
      state: 'success',
    })

    expect(tc.result).toBe('done')
    expect(tc.state).toBe('success')
    expect(tc.status).toBe(ToolCallStatus.COMPLETED)
    expect(tc.completed_at).toBeTruthy()
  })

  it('existing 分支 → 显式传 status=FAILED', () => {
    const tc = {
      id: 'tc-fail',
      name: 'execute',
      status: ToolCallStatus.RUNNING,
    }
    toolCallMap.set('tc-fail', tc)

    updateOrAddToolResultInMap(toolCallMap, {
      id: 'tc-fail',
      tool_call_id: 'tc-fail',
      result: 'error',
      state: 'output-error',
      status: ToolCallStatus.FAILED,
    })

    expect(tc.status).toBe(ToolCallStatus.FAILED)
  })

  it('容错创建分支 → tool_result 先于 tool 到达', () => {
    updateOrAddToolResultInMap(toolCallMap, {
      id: 'tc-fallback',
      tool_call_id: 'tc-fallback',
      name: 'execute',
      result: 'output',
      state: 'success',
    })

    const tc = toolCallMap.get('tc-fallback')
    expect(tc).toBeTruthy()
    expect(tc.id).toBe('tc-fallback')
    expect(tc.result).toBe('output')
    expect(tc.status).toBe(ToolCallStatus.COMPLETED)
  })

  it('容错创建分支 → 携带 parameters 时同步填充', () => {
    updateOrAddToolResultInMap(toolCallMap, {
      id: 'tc-params',
      tool_call_id: 'tc-params',
      name: 'execute',
      result: 'ok',
      state: 'success',
      parameters: { command: 'pwd' },
    })

    const tc = toolCallMap.get('tc-params')
    expect(tc.parameters).toEqual({ command: 'pwd' })
    expect(tc.args).toEqual({ command: 'pwd' })
  })

  it('无 toolCallId → 返回 null', () => {
    const result = updateOrAddToolResultInMap(toolCallMap, { result: 'x' })
    expect(result).toBeNull()
  })
})

// ==================== updateApprovalStateInMap ====================

describe('updateApprovalStateInMap', () => {
  let toolCallMap

  beforeEach(() => {
    toolCallMap = new Map()
  })

  it('更新 approval.state → 不替换整个 approval 对象', () => {
    const tc = {
      id: 'tc-1',
      name: 'execute',
      approval: {
        state: ApprovalState.PENDING,
        tool_name: 'execute',
        tool_call_id: 'tc-1',
      },
    }
    toolCallMap.set('tc-1', tc)

    const result = updateApprovalStateInMap(toolCallMap, 'tc-1', ApprovalState.PROCESSING)
    expect(result).toBe(true)
    expect(tc.approval.state).toBe(ApprovalState.PROCESSING)
    // 其他字段应保留
    expect(tc.approval.tool_name).toBe('execute')
    expect(tc.approval.tool_call_id).toBe('tc-1')
  })

  it('approval 不存在 → 初始化为空对象后设置 state', () => {
    const tc = { id: 'tc-2', name: 'execute' }
    toolCallMap.set('tc-2', tc)

    const result = updateApprovalStateInMap(toolCallMap, 'tc-2', ApprovalState.APPROVED)
    expect(result).toBe(true)
    expect(tc.approval.state).toBe(ApprovalState.APPROVED)
  })

  it('不存在的 toolCall → 返回 false', () => {
    const result = updateApprovalStateInMap(toolCallMap, 'nonexistent', ApprovalState.APPROVED)
    expect(result).toBe(false)
  })

  it('altId 查找 → 通过 data.tool_call_id 匹配', () => {
    const tc = { id: 'tc-real', name: 'execute', approval: { state: 'pending' } }
    toolCallMap.set('tc-real', tc)

    const result = updateApprovalStateInMap(toolCallMap, 'interrupt-x', ApprovalState.APPROVED, {
      tool_call_id: 'tc-real',
    })
    expect(result).toBe(true)
    expect(tc.approval.state).toBe(ApprovalState.APPROVED)
  })
})

// ==================== updateToolCallStatusInMap ====================

describe('updateToolCallStatusInMap', () => {
  let toolCallMap

  beforeEach(() => {
    toolCallMap = new Map()
  })

  it('更新 status → toolCall.status 改变', () => {
    const tc = { id: 'tc-1', name: 'execute', status: ToolCallStatus.RUNNING }
    toolCallMap.set('tc-1', tc)

    const result = updateToolCallStatusInMap(toolCallMap, 'tc-1', ToolCallStatus.COMPLETED)
    expect(result).toBe(true)
    expect(tc.status).toBe(ToolCallStatus.COMPLETED)
  })

  it('不存在的 toolCall → 返回 false', () => {
    const result = updateToolCallStatusInMap(toolCallMap, 'nonexistent', ToolCallStatus.COMPLETED)
    expect(result).toBe(false)
  })

  it('altId 查找 → 通过 data.tool_call_id 匹配', () => {
    const tc = { id: 'tc-real', name: 'execute', status: 'running' }
    toolCallMap.set('tc-real', tc)

    const result = updateToolCallStatusInMap(toolCallMap, 'interrupt-y', ToolCallStatus.COMPLETED, {
      tool_call_id: 'tc-real',
    })
    expect(result).toBe(true)
    expect(tc.status).toBe(ToolCallStatus.COMPLETED)
  })
})

// ==================== isNonEmptyParams / getInterruptId ====================

describe('isNonEmptyParams', () => {
  it('非空对象 → true', () => {
    expect(isNonEmptyParams({ key: 'value' })).toBe(true)
  })

  it('空对象 → false', () => {
    expect(isNonEmptyParams({})).toBe(false)
  })

  it('null/undefined → false', () => {
    expect(isNonEmptyParams(null)).toBe(false)
    expect(isNonEmptyParams(undefined)).toBe(false)
  })

  it('数组 → false', () => {
    expect(isNonEmptyParams([1, 2, 3])).toBe(false)
    expect(isNonEmptyParams([])).toBe(false)
  })

  it('非对象类型 → false', () => {
    expect(isNonEmptyParams('string')).toBe(false)
    expect(isNonEmptyParams(123)).toBe(false)
    expect(isNonEmptyParams(true)).toBe(false)
  })
})

describe('getInterruptId', () => {
  it('优先返回 interrupt_id', () => {
    expect(getInterruptId({ interrupt_id: 'int-1', tool_call_id: 'tc-1' })).toBe('int-1')
  })

  it('无 interrupt_id 时回退到 tool_call_id', () => {
    expect(getInterruptId({ tool_call_id: 'tc-1' })).toBe('tc-1')
  })

  it('空对象 → 空字符串', () => {
    expect(getInterruptId({})).toBe('')
  })

  it('null → 空字符串', () => {
    expect(getInterruptId(null)).toBe('')
    expect(getInterruptId(undefined)).toBe('')
  })
})
