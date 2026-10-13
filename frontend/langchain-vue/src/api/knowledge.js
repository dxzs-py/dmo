import { apiClient } from './axios'
import { fetchSSE } from '@/utils/sse'

export const knowledgeAPI = {
  // 分页参数（page/page_size）为 URL query 协议标识符，保持 snake_case
  getKnowledgeBases(params = {}) { return apiClient.get('/knowledge/knowledge-bases/', { params }) },
  createKnowledgeBase(data) { return apiClient.post('/knowledge/knowledge-bases/', data) },
  updateKnowledgeBase(id, data) { return apiClient.patch(`/knowledge/knowledge-bases/${id}/`, data) },
  deleteKnowledgeBase(id) { return apiClient.delete(`/knowledge/knowledge-bases/${id}/`) },
  getKnowledgeBaseDetail(id) { return apiClient.get(`/knowledge/knowledge-bases/${id}/`) },
  uploadDocuments(kbId, formData, options = {}) {
    return apiClient.post(`/knowledge/knowledge-bases/${kbId}/documents/`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      ...options,
    })
  },
  getDocuments(kbId, params = {}) { return apiClient.get(`/knowledge/knowledge-bases/${kbId}/documents/`, { params }) },
  deleteDocument(kbId, filename) { return apiClient.delete(`/knowledge/knowledge-bases/${kbId}/documents/${filename}/`) },
  testSearch(kbId, data) { return apiClient.post(`/knowledge/knowledge-bases/${kbId}/search/`, data) },
  /** 跨库聚合搜索：后端单次 embedding + 并发检索 + RRF 融合，返回统一 top-k */
  searchAll(data) { return apiClient.post('/knowledge/search-all/', data) },
  /** 重建索引：从原始上传文件重建向量索引（后端复用 rebuild_index_from_source_files，同步重操作） */
  rebuildIndex(kbId) { return apiClient.post(`/knowledge/knowledge-bases/${kbId}/rebuild/`) },
}

export const ragAPI = {
  query(data) { return apiClient.post('/knowledge/query/', data) },
  streamQuery(data, options = {}) {
    return fetchSSE('/knowledge/query/stream/', {
      method: 'POST',
      body: JSON.stringify(data),
      ...options,
    })
  },
  // 与 knowledgeAPI 同端点（知识库资源化路由唯一），分页参数透传
  getIndices(params = {}) { return apiClient.get('/knowledge/knowledge-bases/', { params }) },
  createIndex(data) { return apiClient.post('/knowledge/knowledge-bases/', data) },
  deleteIndex(name) { return apiClient.delete(`/knowledge/knowledge-bases/${name}/`) },
  uploadDocuments(name, formData, options = {}) {
    return apiClient.post(`/knowledge/knowledge-bases/${name}/documents/`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      ...options,
    })
  },
  getDocuments(name, params = {}) { return apiClient.get(`/knowledge/knowledge-bases/${name}/documents/`, { params }) },
  deleteDocument(name, filename) { return apiClient.delete(`/knowledge/knowledge-bases/${name}/documents/${filename}/`) },
}
