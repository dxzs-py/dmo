import { apiClient } from './axios'
import { fetchSSE } from '@/utils/sse'

export const knowledgeAPI = {
  getKnowledgeBases() { return apiClient.get('/knowledge/knowledge-bases/') },
  createKnowledgeBase(data) { return apiClient.post('/knowledge/knowledge-bases/', data) },
  updateKnowledgeBase(id, data) { return apiClient.patch(`/knowledge/knowledge-bases/${id}/`, data) },
  deleteKnowledgeBase(id) { return apiClient.delete(`/knowledge/knowledge-bases/${id}/`) },
  getKnowledgeBaseDetail(id) { return apiClient.get(`/knowledge/knowledge-bases/${id}/`) },
  uploadDocuments(kbId, formData) {
    return apiClient.post(`/knowledge/knowledge-bases/${kbId}/documents/`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  getDocuments(kbId) { return apiClient.get(`/knowledge/knowledge-bases/${kbId}/documents/`) },
  deleteDocument(kbId, filename) { return apiClient.delete(`/knowledge/knowledge-bases/${kbId}/documents/${filename}/`) },
  testSearch(kbId, data) { return apiClient.post(`/knowledge/knowledge-bases/${kbId}/search/`, data) },
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
  getIndices() { return apiClient.get('/knowledge/knowledge-bases/') },
  createIndex(data) { return apiClient.post('/knowledge/knowledge-bases/', data) },
  deleteIndex(name) { return apiClient.delete(`/knowledge/knowledge-bases/${name}/`) },
  uploadDocuments(name, formData) {
    return apiClient.post(`/knowledge/knowledge-bases/${name}/documents/`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  getDocuments(name) { return apiClient.get(`/knowledge/knowledge-bases/${name}/documents/`) },
  deleteDocument(name, filename) { return apiClient.delete(`/knowledge/knowledge-bases/${name}/documents/${filename}/`) },
  searchDocuments(data) { return apiClient.post('/knowledge/search/', data) },
}
