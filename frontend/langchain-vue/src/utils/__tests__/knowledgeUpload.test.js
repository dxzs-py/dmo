/**
 * utils/knowledgeUpload.js mapUploadProgress 单元测试（R-UX-06）
 *
 * 上传两段盲窗整改：HTTP 上传段（axios onUploadProgress）0→100 的原始百分比
 * 映射到 0→29 段，与后端 Celery 任务离散进度点（30/60/75/100）无缝衔接。
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect } from 'vitest'
import { mapUploadProgress } from '../knowledgeUpload'

describe('mapUploadProgress（R-UX-06 HTTP 上传段映射）', () => {
  it('0→100 原始进度映射到 0→29 段', () => {
    expect(mapUploadProgress(0, 1000)).toBe(0)
    expect(mapUploadProgress(250, 1000)).toBe(8)
    expect(mapUploadProgress(500, 1000)).toBe(15)
    expect(mapUploadProgress(966, 1000)).toBe(29)
  })

  it('完成瞬间（100%）封顶 29，不越入任务进度段（30 起）', () => {
    expect(mapUploadProgress(1000, 1000)).toBe(29)
    expect(mapUploadProgress(2000, 1000)).toBe(29)
  })

  it('total 无效（0/负数/缺失）返回 0，不产生 NaN', () => {
    expect(mapUploadProgress(500, 0)).toBe(0)
    expect(mapUploadProgress(500, -1)).toBe(0)
  })
})
