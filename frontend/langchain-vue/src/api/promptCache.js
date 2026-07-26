import { apiClient } from '@/api/axios'

/**
 * 提示缓存 API
 * 提供提示缓存的完整 CRUD + 测试 + 排序接口
 */
export const promptCacheAPI = {
  /** 获取提示缓存列表 */
  list: (params) => apiClient.get('/context/prompt-caches/', { params }),

  /** 创建提示缓存 */
  create: (data) => apiClient.post('/context/prompt-caches/', data),

  /** 获取单个提示缓存详情 */
  get: (id) => apiClient.get(`/context/prompt-caches/${id}/`),

  /** 更新提示缓存（全量） */
  update: (id, data) => apiClient.put(`/context/prompt-caches/${id}/`, data),

  /** 部分更新提示缓存（仅更新传入字段） */
  patch: (id, data) => apiClient.patch(`/context/prompt-caches/${id}/`, data),

  /** 删除提示缓存 */
  delete: (id) => apiClient.delete(`/context/prompt-caches/${id}/`),

  /** 测试/预览模板渲染 */
  test: (id, data) => apiClient.post(`/context/prompt-caches/${id}/test/`, data),

  /** 批量更新排序 */
  reorder: (items) => apiClient.put('/context/prompt-caches/reorder/', { items }),
}
