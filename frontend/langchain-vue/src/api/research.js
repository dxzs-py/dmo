import { apiClient } from './axios'
import settings from '../config/settings'
import { useUserStore } from '@/stores/user'

export const deepResearchAPI = {
  start(data) { return apiClient.post('/research/start/', data) },
  getStatus(taskId) { return apiClient.get(`/research/status/${taskId}/`) },
  getResults(taskId) { return apiClient.get(`/research/results/${taskId}/`) },
  getTasks(params = {}) { return apiClient.get('/research/tasks/', { params }) },
  deleteTask(taskId) { return apiClient.delete(`/research/task/${taskId}/`) },
  getFiles(taskId) { return apiClient.get(`/research/${taskId}/files/`) },
  downloadFile(taskId, filename) { return `${settings.API_BASE_URL}/research/${taskId}/file/download/${filename}` },
  getFileContent(taskId, filename) { return apiClient.get(`/research/${taskId}/file/content/${filename}/`) },
  searchFiles(query) { return apiClient.get('/research/search/', { params: { keyword: query } }) },
  async streamFetch(taskId, options = {}) {
    const userStore = useUserStore()
    const buildHeaders = () => {
      const headers = { 'Accept': 'text/event-stream' }
      if (userStore.token) headers['Authorization'] = `Bearer ${userStore.token}`
      return headers
    }
    const buildUrl = () => {
      const base = `${settings.API_BASE_URL}/research/stream/${taskId}/`
      if (userStore.token) return `${base}?token=${encodeURIComponent(userStore.token)}`
      return base
    }

    let response = await fetch(buildUrl(), {
      method: 'GET',
      headers: buildHeaders(),
      signal: options.signal,
    })

    if (response.status === 401 && userStore.refreshToken) {
      const refreshed = await userStore.refreshAccessToken()
      if (refreshed) {
        response = await fetch(buildUrl(), {
          method: 'GET',
          headers: buildHeaders(),
          signal: options.signal,
        })
      }
    }

    return response
  },
}
