import { apiClient } from './axios'
import settings from '../config/settings'
import { useUserStore } from '@/stores/user'

export const workflowAPI = {
  start(data) { return apiClient.post('/learning/start/', data) },
  startStreamUrl() { return `${settings.API_BASE_URL}/learning/start/stream/` },
  streamUrl(threadId) { return `${settings.API_BASE_URL}/learning/stream/${threadId}/` },
  getState(threadId) { return apiClient.get(`/learning/status/${threadId}/`) },
  submitAnswers(threadId, data) { return apiClient.post('/learning/submit/', { thread_id: threadId, answers: data }) },
  getTasks(params = {}) { return apiClient.get('/learning/tasks/', { params }) },
  getFiles(threadId) { return apiClient.get(`/learning/${threadId}/files/`) },
  downloadFile(threadId, filename) { return `${settings.API_BASE_URL}/learning/${threadId}/file/download/${filename}` },
  getFileContent(threadId, filename) { return apiClient.get(`/learning/${threadId}/file/content/${filename}/`) },
  deleteTask(threadId) { return apiClient.delete(`/learning/task/${threadId}/`) },
  async streamFetch(threadId, options = {}) {
    const userStore = useUserStore()
    const buildHeaders = () => {
      const headers = { 'Accept': 'text/event-stream' }
      if (userStore.token) headers['Authorization'] = `Bearer ${userStore.token}`
      return headers
    }
    const buildUrl = () => {
      const base = `${settings.API_BASE_URL}/learning/stream/${threadId}/`
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
