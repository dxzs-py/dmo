import { apiClient } from './axios'

export const attachmentAPI = {
  getStats() {
    return apiClient.get('/chat/admin/attachments/stats/')
  },

  getList(params = {}) {
    return apiClient.get('/chat/admin/attachments/', { params })
  },

  getDetail(attachmentId) {
    return apiClient.get(`/chat/admin/attachments/${attachmentId}/`)
  },

  deleteAttachment(attachmentId) {
    return apiClient.delete(`/chat/admin/attachments/${attachmentId}/`)
  },

  actionAttachment(attachmentId, action, data = {}) {
    return apiClient.post(`/chat/admin/attachments/${attachmentId}/action/`, {
      action,
      ...data,
    })
  },

  runCleanup(action = 'cleanup', dryRun = false) {
    return apiClient.post('/chat/admin/attachments/cleanup/', {
      action,
      dry_run: dryRun,
    })
  },

  getStorageAlerts(params = {}) {
    return apiClient.get('/chat/admin/storage-alerts/', { params })
  },

  handleStorageAlert(alertId, action) {
    return apiClient.post(`/chat/admin/storage-alerts/${alertId}/`, { action })
  },
}
