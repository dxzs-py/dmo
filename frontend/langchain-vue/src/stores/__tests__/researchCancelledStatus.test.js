/**
 * 深度研究取消终态（C1/P2-1）前端同步与展示测试
 *
 * 被测：
 * - stores/statusMap.js createTerminalProtectedStatusMap：cancelled 作为终态
 *   受保护——实时通道滞后事件（running/pending 快照）不得覆盖已取消状态
 * - utils/researchTaskStatus.js：cancelled 的文案/标签色映射
 *
 * 场景对齐：后端执行服务经 status_change 实时事件广播 CANCELLED 后，其他浏览器
 * 可能仍有在途的 running 快照事件，终态保护保证状态标签不回跳。
 *
 * 运行方式（vitest）：npm run test
 */
import { describe, it, expect } from 'vitest'
import { createTerminalProtectedStatusMap } from '../statusMap'
import { getTaskStatusText, getTaskStatusTagType } from '@/utils/researchTaskStatus'
import { ResearchTaskStatus } from '@/types'

describe('createTerminalProtectedStatusMap cancelled 终态保护（C1）', () => {
  it('已取消状态不被滞后非终态事件覆盖', () => {
    const { set, get } = createTerminalProtectedStatusMap({ logTag: 'Research', actionName: 'setTaskStatus' })
    set('task-1', { status: ResearchTaskStatus.CANCELLED, currentStep: 'cancelled' })
    // 滞后的 running 快照（轮询/在途事件）到达
    set('task-1', { status: ResearchTaskStatus.RUNNING, currentStep: 'research_started' })
    const state = get('task-1')
    expect(state.status).toBe(ResearchTaskStatus.CANCELLED)
  })

  it('取消终态是权威：可覆盖非终态运行状态', () => {
    const { set, get } = createTerminalProtectedStatusMap({ logTag: 'Research', actionName: 'setTaskStatus' })
    set('task-2', { status: ResearchTaskStatus.RUNNING })
    set('task-2', { status: ResearchTaskStatus.CANCELLED, error: '用户已取消该研究任务' })
    const state = get('task-2')
    expect(state.status).toBe(ResearchTaskStatus.CANCELLED)
    expect(state.error).toBe('用户已取消该研究任务')
  })

  it('已取消后其他字段仍可合并更新（部分成果字段照常同步）', () => {
    const { set, get } = createTerminalProtectedStatusMap({ logTag: 'Research', actionName: 'setTaskStatus' })
    set('task-3', { status: ResearchTaskStatus.CANCELLED })
    set('task-3', { content: '部分成果正文' })
    const state = get('task-3')
    expect(state.status).toBe(ResearchTaskStatus.CANCELLED)
    expect(state.content).toBe('部分成果正文')
  })
})

describe('researchTaskStatus cancelled 映射（C1）', () => {
  it('cancelled 文案为「已取消」', () => {
    expect(getTaskStatusText(ResearchTaskStatus.CANCELLED)).toBe('已取消')
  })

  it('cancelled 标签色为 info（中性终态，区别于 failed 的 danger）', () => {
    expect(getTaskStatusTagType(ResearchTaskStatus.CANCELLED)).toBe('info')
  })
})
