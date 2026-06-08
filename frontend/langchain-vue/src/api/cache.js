import { apiClient } from './axios'

export const cacheAPI = {
  getCacheHealth() { return apiClient.get('/cache/health/') },
  getCacheStats() { return apiClient.get('/cache/stats/') },
  clearCache(data = {}) { return apiClient.post('/cache/clear/', data) },
  getDatabaseOverview() { return apiClient.get('/core/database/overview/') },
  getPostgreSQLStatus() { return apiClient.get('/core/database/postgresql/status/') },
  getVectorStoreStatus() { return apiClient.get('/core/database/vector-store/status/') },
}
