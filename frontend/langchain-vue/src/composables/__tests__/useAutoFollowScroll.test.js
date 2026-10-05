import { describe, it, expect } from 'vitest'
import {
  AUTO_FOLLOW_THRESHOLD,
  getDistanceFromBottom,
  resolveFollowTransition,
  resolveNewContentBadgeText,
  useAutoFollowScroll,
} from '../useAutoFollowScroll'

describe('getDistanceFromBottom - 距底距离计算（纯函数）', () => {
  it('按 scrollHeight - clientHeight - scrollTop 计算', () => {
    expect(getDistanceFromBottom({ scrollTop: 100, scrollHeight: 1000, clientHeight: 400 })).toBe(500)
  })

  it('贴底时距离为 0', () => {
    expect(getDistanceFromBottom({ scrollTop: 600, scrollHeight: 1000, clientHeight: 400 })).toBe(0)
  })

  it('scrollTop 超界时钳制为 0（不允许负数）', () => {
    expect(getDistanceFromBottom({ scrollTop: 999, scrollHeight: 1000, clientHeight: 400 })).toBe(0)
  })

  it('容器缺失或含非有限数值时返回 0', () => {
    expect(getDistanceFromBottom(null)).toBe(0)
    expect(getDistanceFromBottom(undefined)).toBe(0)
    expect(getDistanceFromBottom({ scrollTop: NaN, scrollHeight: 1000, clientHeight: 400 })).toBe(0)
    expect(getDistanceFromBottom({ scrollTop: 0, scrollHeight: undefined, clientHeight: 400 })).toBe(0)
  })
})

describe('resolveFollowTransition - 跟随状态转移判定（纯函数）', () => {
  it('距底 0 恢复跟随', () => {
    expect(resolveFollowTransition(0)).toBe('resume')
  })

  it('距底等于阈值（≤）恢复跟随', () => {
    expect(resolveFollowTransition(AUTO_FOLLOW_THRESHOLD)).toBe('resume')
  })

  it('距底超出阈值暂停跟随', () => {
    expect(resolveFollowTransition(AUTO_FOLLOW_THRESHOLD + 1)).toBe('pause')
  })

  it('支持自定义阈值', () => {
    expect(resolveFollowTransition(200, 300)).toBe('resume')
    expect(resolveFollowTransition(200, 150)).toBe('pause')
  })
})

describe('resolveNewContentBadgeText - 浮标文案（纯函数）', () => {
  it('无可靠计数时显示"有新内容"', () => {
    expect(resolveNewContentBadgeText(0)).toBe('有新内容')
  })

  it('有可靠计数时显示"N 条新消息"', () => {
    expect(resolveNewContentBadgeText(3)).toBe('3 条新消息')
  })
})

describe('useAutoFollowScroll - 跟随滚动状态机', () => {
  it('初始状态：跟随中且浮标隐藏', () => {
    const state = useAutoFollowScroll()
    expect(state.isFollowing.value).toBe(true)
    expect(state.showNewContentBadge.value).toBe(false)
  })

  it('跟随中收到新消息/流式增长：应当立即滚动（返回 true），无未读累计', () => {
    const state = useAutoFollowScroll()
    expect(state.handleNewMessages(1)).toBe(true)
    expect(state.handleStreamContentGrowth()).toBe(true)
    expect(state.pendingMessageCount.value).toBe(0)
    expect(state.hasPendingStreamContent.value).toBe(false)
    expect(state.showNewContentBadge.value).toBe(false)
  })

  it('上翻超出阈值暂停：后续新消息不滚动并累计未读计数（浮标显示条数）', () => {
    const state = useAutoFollowScroll()
    expect(state.applyScrollPosition(400)).toBe(false)
    expect(state.isFollowing.value).toBe(false)

    expect(state.handleNewMessages(2)).toBe(false)
    expect(state.pendingMessageCount.value).toBe(2)
    expect(state.showNewContentBadge.value).toBe(true)
    expect(state.newContentBadgeText.value).toBe('2 条新消息')
  })

  it('暂停期间流式增长：不滚动仅标记"有新内容"（无计数时浮标显示有新内容）', () => {
    const state = useAutoFollowScroll()
    state.applyScrollPosition(400)
    expect(state.handleStreamContentGrowth()).toBe(false)
    expect(state.hasPendingStreamContent.value).toBe(true)
    expect(state.showNewContentBadge.value).toBe(true)
    expect(state.newContentBadgeText.value).toBe('有新内容')
  })

  it('计数与流式标记并存时浮标优先显示可靠条数', () => {
    const state = useAutoFollowScroll()
    state.applyScrollPosition(400)
    state.handleNewMessages(1)
    state.handleStreamContentGrowth()
    expect(state.newContentBadgeText.value).toBe('1 条新消息')
  })

  it('手动滚回阈值内自动恢复跟随：清空未读标记，浮标隐藏', () => {
    const state = useAutoFollowScroll()
    state.applyScrollPosition(400)
    state.handleNewMessages(3)
    state.handleStreamContentGrowth()
    expect(state.showNewContentBadge.value).toBe(true)

    expect(state.applyScrollPosition(AUTO_FOLLOW_THRESHOLD - 10)).toBe(true)
    expect(state.isFollowing.value).toBe(true)
    expect(state.pendingMessageCount.value).toBe(0)
    expect(state.hasPendingStreamContent.value).toBe(false)
    expect(state.showNewContentBadge.value).toBe(false)
  })

  it('暂停中在阈值外继续滚动保持暂停（不误恢复）', () => {
    const state = useAutoFollowScroll()
    state.applyScrollPosition(400)
    expect(state.applyScrollPosition(200)).toBe(false)
    expect(state.isFollowing.value).toBe(false)
  })

  it('forceFollow（用户发送消息/点击浮标）：无条件恢复跟随并清空未读', () => {
    const state = useAutoFollowScroll()
    state.applyScrollPosition(400)
    state.handleNewMessages(5)
    state.handleStreamContentGrowth()

    state.forceFollow()
    expect(state.isFollowing.value).toBe(true)
    expect(state.pendingMessageCount.value).toBe(0)
    expect(state.hasPendingStreamContent.value).toBe(false)
    expect(state.showNewContentBadge.value).toBe(false)
    // 恢复后新消息恢复立即滚动
    expect(state.handleNewMessages(1)).toBe(true)
  })

  it('handleMessagesRemoved（删除/回滚）：清零未读标记但保持当前暂停状态', () => {
    const state = useAutoFollowScroll()
    state.applyScrollPosition(400)
    state.handleNewMessages(2)
    state.handleStreamContentGrowth()

    state.handleMessagesRemoved()
    expect(state.isFollowing.value).toBe(false)
    expect(state.pendingMessageCount.value).toBe(0)
    expect(state.hasPendingStreamContent.value).toBe(false)
    expect(state.showNewContentBadge.value).toBe(false)
  })

  it('支持自定义阈值注入', () => {
    const state = useAutoFollowScroll({ threshold: 300 })
    expect(state.applyScrollPosition(200)).toBe(true)
    expect(state.applyScrollPosition(400)).toBe(false)
  })
})
