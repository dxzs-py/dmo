/**
 * updateTaskFromEvent 取消终态映射测试（深研取消→聊天侧同步修复）
 *
 * 被测：stores/research.js updateTaskFromEvent——stream_completed 事件到
 * taskInfo 状态的映射。取消事件携带 stopped=true（与 chat 侧停止语义对齐），
 * 映射 CANCELLED 而非 FAILED，防止取消被详情页/卡片误显"深度研究失败"。
 *
 * 运行方式（vitest）：npm run test
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { ResearchTaskStatus } from '@/types'
import { useResearchStore } from '../research'

describe('updateTaskFromEvent stopped→CANCELLED 映射', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('stream_completed 携带 stopped=true → 状态映射 CANCELLED（非 FAILED）', () => {
    const store = useResearchStore()
    store.updateTaskFromEvent('t1', {
      success: false,
      error: '用户已取消该研究任务',
      stopped: true,
    })
    const state = store.getTaskStatus('t1')
    expect(state.status).toBe(ResearchTaskStatus.CANCELLED)
    expect(state.error).toBe('用户已取消该研究任务')
  })

  it('无 stopped 标记的失败事件仍映射 FAILED（行为回归保护）', () => {
    const store = useResearchStore()
    store.updateTaskFromEvent('t2', { success: false, error: '模型调用失败' })
    expect(store.getTaskStatus('t2').status).toBe(ResearchTaskStatus.FAILED)
  })

  it('成功事件仍映射 COMPLETED（行为回归保护）', () => {
    const store = useResearchStore()
    store.updateTaskFromEvent('t3', { success: true, finalReport: '报告' })
    expect(store.getTaskStatus('t3').status).toBe(ResearchTaskStatus.COMPLETED)
  })
})
