import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * F4/UX-02 深研进度条守卫测试（无百分比断言）
 *
 * 项目 vitest 为 node 环境且未引入组件挂载依赖（@vue/test-utils），
 * 与批E test_deep_builder_coupling.py 同理采用源码扫描守卫：
 * 锁定"时间估算伪进度百分比已移除、不再回潮"的整改成果。
 *
 * 背景（payload 实测结论，2026-10-05）：
 * - WS task 频道 status_change（common/realtime_sync.py publish_task_status）
 *   仅携带 current_step，且 RUNNING 内部只有 research_started/waiting_subagent
 *   两个阶段标记（session_executor.py 全部 _publish_status_change 调用点枚举），
 *   waiting_subagent 在子代理 fan-out 挂起时发布、可多波次出现，非线性推进；
 * - SSE 主通道（apps/research/views_stream.py）status_change 仅在状态跃变时
 *   发布 {status, message}，message 为状态级粗粒度文案，无阶段字段。
 * → 阶段信息不足以支撑可靠分档，按审计认可第二案：去掉百分比数字，
 *   保留 elapsed 计时 + progressMessage 阶段文本（头部标签）。
 */

const here = dirname(fileURLToPath(import.meta.url))

const readSource = (relPath) =>
  readFileSync(join(here, relPath), 'utf-8')

describe('F4/UX-02：深研进度条去除时间估算伪百分比', () => {
  const viewSrc = readSource('../../../views/DeepResearchView.vue')
  const detailSrc = readSource('../ResearchTaskDetail.vue')

  it('DeepResearchView 不再计算时间估算/硬编码伪进度，也不再向详情传百分比', () => {
    // 原 RUNNING 态时间估算：10 + (elapsed/600)*80 封顶 90%
    expect(viewSrc).not.toMatch(/maxSeconds/)
    expect(viewSrc).not.toMatch(/elapsedSeconds\.value\s*\/\s*600/)
    // 原 AWAITING_APPROVAL 硬编码 50%
    expect(viewSrc).not.toMatch(/AWAITING_APPROVAL\)\s*return\s*50/)
    // progressPercentage computed 与 prop 透传整体移除
    expect(viewSrc).not.toMatch(/progressPercentage|progress-percentage/)
    // 改为透传真实用时计时
    expect(viewSrc).toMatch(/:elapsed-seconds="elapsedSeconds"/)
  })

  it('ResearchTaskDetail 进度条为无数字的不确定态，且展示用时', () => {
    // progressPercentage prop 已移除
    expect(detailSrc).not.toMatch(/progressPercentage|progress-percentage/)
    // 不确定态动画条 + 隐藏百分比数字（无百分比断言核心）
    expect(detailSrc).toMatch(/:indeterminate="true"/)
    expect(detailSrc).toMatch(/:show-text="false"/)
    // 用时计时展示（formatDuration(elapsedSeconds)）
    expect(detailSrc).toMatch(/formatDuration\(elapsedSeconds\)/)
  })
})
