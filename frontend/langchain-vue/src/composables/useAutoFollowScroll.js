import { ref, computed } from 'vue'

/**
 * 自动滚底判定阈值（px）：距底部不超过该值视为"在底部附近"，
 * 维持自动跟随滚动；超出则暂停自动滚动（用户上翻阅读场景）。
 */
export const AUTO_FOLLOW_THRESHOLD = 120

/**
 * 计算滚动容器距底部的剩余距离（纯函数）
 *
 * @param {{ scrollTop: number, scrollHeight: number, clientHeight: number }|null} container
 *        任意暴露 scrollTop/scrollHeight/clientHeight 的容器（DOM 元素可直接传入）；
 *        虚拟滚动（DynamicScroller）下取其根元素，scrollHeight 即虚拟总高度
 * @returns {number} 距底部距离（px），入参缺失或含非有限数值时返回 0
 */
export function getDistanceFromBottom(container) {
  if (!container) return 0
  const { scrollTop, scrollHeight, clientHeight } = container
  if (![scrollTop, scrollHeight, clientHeight].every(Number.isFinite)) return 0
  return Math.max(0, scrollHeight - clientHeight - scrollTop)
}

/**
 * 依据距底距离判定跟随状态转移（纯函数）
 *
 * @param {number} distance 距底部距离（px）
 * @param {number} [threshold] 跟随阈值（默认 AUTO_FOLLOW_THRESHOLD）
 * @returns {'resume'|'pause'} resume=恢复自动跟随；pause=暂停自动滚动
 */
export function resolveFollowTransition(distance, threshold = AUTO_FOLLOW_THRESHOLD) {
  return distance <= threshold ? 'resume' : 'pause'
}

/**
 * 新内容浮标文案（纯函数）：暂停期间新增消息条数可靠时显示"N 条新消息"；
 * 流式正文增长等无法计数的场景显示"有新内容"。
 * @param {number} pendingMessageCount 暂停期间新增消息条数
 * @returns {string} 浮标文案
 */
export function resolveNewContentBadgeText(pendingMessageCount) {
  return pendingMessageCount > 0 ? `${pendingMessageCount} 条新消息` : '有新内容'
}

/**
 * 聊天消息区自动跟随滚动的状态机（ChatMessages.vue 专用）
 *
 * 设计目标（audit UX-05）：
 * - 距底阈值内才自动滚底；用户上翻超出阈值后暂停自动滚动（不再强拉视口）；
 * - 暂停期间有新内容时以"回到底部"浮标提示（新增消息计数 / 流式增长标记），
 *   点击滚底并恢复跟随；
 * - 用户手动滚回底部附近时自动恢复跟随；
 * - 用户主动发送消息 / 重新生成等主动锚底场景强制恢复跟随（用户预期）。
 *
 * 状态转移依据：
 * - 滚动事件（仅真实滚动会触发，scrollHeight 变化不触发）按距底距离转移；
 * - 新消息到达：跟随中→由调用方滚动；暂停中→累计未读计数；
 * - 流式产出增长：跟随中→由调用方滚动；暂停中→标记"有新内容"（不可计数）。
 */
export function useAutoFollowScroll({ threshold = AUTO_FOLLOW_THRESHOLD } = {}) {
  /** 是否处于自动跟随滚动状态 */
  const isFollowing = ref(true)
  /** 暂停期间新增的消息条数（可靠计数来源：messages 数组长度增量） */
  const pendingMessageCount = ref(0)
  /** 暂停期间流式产出有增长（无法计数，仅标记） */
  const hasPendingStreamContent = ref(false)

  /** 是否显示"回到底部"浮标：仅在暂停且确有未消费新内容时出现 */
  const showNewContentBadge = computed(
    () => !isFollowing.value && (pendingMessageCount.value > 0 || hasPendingStreamContent.value)
  )
  /** 浮标文案：优先展示可靠的新增消息条数 */
  const newContentBadgeText = computed(() => resolveNewContentBadgeText(pendingMessageCount.value))

  /** 恢复跟随并清空未消费新内容标记 */
  const resumeFollowing = () => {
    isFollowing.value = true
    pendingMessageCount.value = 0
    hasPendingStreamContent.value = false
  }

  /**
   * 滚动事件驱动：按距底距离转移跟随状态。
   * 恢复跟随时一并清空未消费新内容标记（浮标隐藏）。
   * @param {number} distance 距底部距离（px）
   * @returns {boolean} 转移后的跟随状态
   */
  const applyScrollPosition = (distance) => {
    if (resolveFollowTransition(distance, threshold) === 'resume') {
      resumeFollowing()
    } else {
      isFollowing.value = false
    }
    return isFollowing.value
  }

  /**
   * 新消息到达：跟随中→由调用方执行滚动（返回 true）；暂停中→累计未读计数（返回 false）。
   * @param {number} [count] 本次新增条数
   * @returns {boolean} 是否应当立即滚动到底部
   */
  const handleNewMessages = (count = 1) => {
    if (isFollowing.value) return true
    pendingMessageCount.value += count
    return false
  }

  /**
   * 流式产出增长（正文/推理/工具调用/子代理卡等）：
   * 跟随中→由调用方执行滚动（返回 true）；暂停中→仅标记有新内容（返回 false）。
   * @returns {boolean} 是否应当立即滚动到底部
   */
  const handleStreamContentGrowth = () => {
    if (isFollowing.value) return true
    hasPendingStreamContent.value = true
    return false
  }

  /** 消息数量减少（删除/回滚）：未读计数失效清零（跟随/暂停状态本身不变） */
  const handleMessagesRemoved = () => {
    pendingMessageCount.value = 0
    hasPendingStreamContent.value = false
  }

  return {
    isFollowing,
    pendingMessageCount,
    hasPendingStreamContent,
    showNewContentBadge,
    newContentBadgeText,
    applyScrollPosition,
    forceFollow: resumeFollowing,
    handleNewMessages,
    handleStreamContentGrowth,
    handleMessagesRemoved,
  }
}
