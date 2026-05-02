import { apiClient } from './axios'
import settings from '../config/settings'
import { useUserStore } from '@/stores/user'

export const knowledgeAPI = {
  getKnowledgeBases() { return apiClient.get('/knowledge/knowledge-bases/') },
  createKnowledgeBase(data) { return apiClient.post('/knowledge/knowledge-bases/', data) },
  updateKnowledgeBase(id, data) { return apiClient.put(`/knowledge/knowledge-bases/${id}/`, data) },
  deleteKnowledgeBase(id) { return apiClient.delete(`/knowledge/knowledge-bases/${id}/`) },
  getKnowledgeBaseDetail(id) { return apiClient.get(`/knowledge/knowledge-bases/${id}/`) },
  uploadDocuments(kbId, formData) {
    return apiClient.post(`/knowledge/knowledge-bases/${kbId}/upload/`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  getDocuments(kbId) { return apiClient.get(`/knowledge/knowledge-bases/${kbId}/documents/`) },
  deleteDocument(kbId, filename) { return apiClient.delete(`/knowledge/knowledge-bases/${kbId}/documents/${filename}/`) },
  testSearch(kbId, data) { return apiClient.post(`/knowledge/knowledge-bases/${kbId}/search/`, data) },
  getCacheHealth() { return apiClient.get('/knowledge/cache/health/') },
  getCacheStats() { return apiClient.get('/knowledge/cache/stats/') },
  clearCache(data = {}) { return apiClient.post('/knowledge/cache/clear/', data) },
  getDatabaseOverview() { return apiClient.get('/knowledge/database/overview/') },
  getMySQLStatus() { return apiClient.get('/knowledge/database/mysql/status/') },
  getVectorStoreStatus() { return apiClient.get('/knowledge/database/vector-store/status/') },
}

export const ragAPI = {
  query(data) { return apiClient.post('/knowledge/query/', data) },
  async streamQuery(data, options = {}) {
    const userStore = useUserStore()
    const buildHeaders = () => {
      const headers = { 'Content-Type': 'application/json' }
      if (userStore.token) headers['Authorization'] = `Bearer ${userStore.token}`
      return headers
    }

    let response = await fetch(`${settings.API_BASE_URL}/knowledge/query/stream/`, {
      method: 'POST',
      headers: buildHeaders(),
      body: JSON.stringify(data),
      signal: options.signal,
    })

    if (response.status === 401 && userStore.refreshToken) {
      const refreshed = await userStore.refreshAccessToken()
      if (refreshed) {
        response = await fetch(`${settings.API_BASE_URL}/knowledge/query/stream/`, {
          method: 'POST',
          headers: buildHeaders(),
          body: JSON.stringify(data),
          signal: options.signal,
        })
      }
    }

    return response
  },
  getIndices() { return apiClient.get('/knowledge/indices/') },
  createIndex(data) { return apiClient.post('/knowledge/indices/create/', data) },
  createEmptyIndex(data) { return apiClient.post('/knowledge/indices/create-empty/', data) },
  getIndexDetail(name) { return apiClient.get(`/knowledge/indices/${name}/`) },
  deleteIndex(name) { return apiClient.delete(`/knowledge/indices/${name}/delete/`) },
  getIndexStats(name) { return apiClient.get(`/knowledge/indices/${name}/stats/`) },
  uploadDocuments(name, formData) {
    return apiClient.post(`/knowledge/indices/${name}/upload/`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  getDocuments(name) { return apiClient.get(`/knowledge/indices/${name}/documents/`) },
  deleteDocument(name, filename) { return apiClient.delete(`/knowledge/indices/${name}/documents/${filename}/`) },
  addDirectory(name, data) { return apiClient.post(`/knowledge/indices/${name}/add-directory/`, data) },
  searchDocuments(data) { return apiClient.post('/knowledge/search/', data) },
}
