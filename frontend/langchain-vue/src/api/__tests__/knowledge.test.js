/**
 * api/knowledge.js W7 整改单元测试
 *
 * 覆盖（R-UX-02 / R-UX-06 / R-UX-07 的 API 层接线）：
 * - uploadDocuments：第三参 options 透传（onUploadProgress 进 axios config），
 *   multipart Content-Type 头与 options 合并不互斥
 * - 分页参数透传：page/page_size 以 URL query 协议键名（snake_case）传给 axios params
 * - rebuildIndex：POST 重建路由（R-UX-07 用户侧入口）
 *
 * mock 说明：'./axios' 桩掉 apiClient（阻断拦截器/toCamelCase 重依赖链），
 * 断言 apiClient 收到的调用参数形状。
 *
 * 运行方式（vitest）：
 *   npm run test
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const apiClient = vi.hoisted(() => ({
  get: vi.fn(() => Promise.resolve({ data: {} })),
  post: vi.fn(() => Promise.resolve({ data: {} })),
  patch: vi.fn(() => Promise.resolve({ data: {} })),
  delete: vi.fn(() => Promise.resolve({ data: {} })),
}))

vi.mock('../axios', () => ({ apiClient }))
// fetchSSE 依赖 user store/router（node 环境无 window），本测试不覆盖 SSE 封装
vi.mock('@/utils/sse', () => ({ fetchSSE: vi.fn() }))

import { knowledgeAPI, ragAPI } from '../knowledge.js'

describe('knowledgeAPI.uploadDocuments options 透传（R-UX-06 onUploadProgress）', () => {
  beforeEach(() => {
    apiClient.post.mockClear()
  })

  it('onUploadProgress 进入 axios config，multipart 头保留', async () => {
    const onUploadProgress = () => {}
    const formData = new FormData()
    await knowledgeAPI.uploadDocuments('kb1', formData, { onUploadProgress })

    expect(apiClient.post).toHaveBeenCalledTimes(1)
    const [url, body, config] = apiClient.post.mock.calls[0]
    expect(url).toBe('/knowledge/knowledge-bases/kb1/documents/')
    expect(body).toBe(formData)
    expect(config.onUploadProgress).toBe(onUploadProgress)
    expect(config.headers['Content-Type']).toBe('multipart/form-data')
  })

  it('不传 options 时保持既有形态（仅 headers，无 onUploadProgress）', async () => {
    await knowledgeAPI.uploadDocuments('kb1', new FormData())

    const config = apiClient.post.mock.calls[0][2]
    expect(config.headers['Content-Type']).toBe('multipart/form-data')
    expect(config).not.toHaveProperty('onUploadProgress')
  })
})

describe('ragAPI.uploadDocuments options 透传（R-UX-06 onUploadProgress）', () => {
  beforeEach(() => {
    apiClient.post.mockClear()
  })

  it('onUploadProgress 进入 axios config（索引名路径）', async () => {
    const onUploadProgress = () => {}
    await ragAPI.uploadDocuments('idx1', new FormData(), { onUploadProgress })

    const [url, , config] = apiClient.post.mock.calls[0]
    expect(url).toBe('/knowledge/knowledge-bases/idx1/documents/')
    expect(config.onUploadProgress).toBe(onUploadProgress)
  })
})

describe('分页参数透传（R-UX-02，query 协议键名 snake_case）', () => {
  beforeEach(() => {
    apiClient.get.mockClear()
  })

  it('getKnowledgeBases 透传 page_size', async () => {
    await knowledgeAPI.getKnowledgeBases({ page_size: 100 })

    const [url, config] = apiClient.get.mock.calls[0]
    expect(url).toBe('/knowledge/knowledge-bases/')
    expect(config.params).toStrictEqual({ page_size: 100 })
  })

  it('getDocuments 透传 page/page_size（snake_case）', async () => {
    await knowledgeAPI.getDocuments('kb1', { page: 2, page_size: 20 })

    const [url, config] = apiClient.get.mock.calls[0]
    expect(url).toBe('/knowledge/knowledge-bases/kb1/documents/')
    expect(config.params).toStrictEqual({ page: 2, page_size: 20 })
  })

  it('ragAPI.getIndices 透传 page_size', async () => {
    await ragAPI.getIndices({ page_size: 100 })

    const [url, config] = apiClient.get.mock.calls[0]
    expect(url).toBe('/knowledge/knowledge-bases/')
    expect(config.params).toStrictEqual({ page_size: 100 })
  })
})

describe('rebuildIndex 路由（R-UX-07）', () => {
  beforeEach(() => {
    apiClient.post.mockClear()
  })

  it('POST 重建端点', async () => {
    await knowledgeAPI.rebuildIndex('kb9')

    const [url] = apiClient.post.mock.calls[0]
    expect(url).toBe('/knowledge/knowledge-bases/kb9/rebuild/')
  })
})
