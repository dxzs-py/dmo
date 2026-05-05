import { apiClient } from './axios'

export const attachmentAPI = {
  getStats() {
    return apiClient.get('/chat/admin/attachments/stats/')
  },

  getList(params = {}) {
    return apiClient.get('/chat/admin/attachments/', { params })
  },

  getTrashedList(params = {}) {
    return apiClient.get('/chat/admin/attachments/', { params: { ...params, trashed: true } })
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

  indexAttachment(attachmentId) {
    return apiClient.post(`/chat/admin/attachments/${attachmentId}/action/`, {
      action: 'index',
    })
  },

  unindexAttachment(attachmentId) {
    return apiClient.post(`/chat/admin/attachments/${attachmentId}/action/`, {
      action: 'unindex',
    })
  },

  permanentDelete(attachmentId) {
    return apiClient.post(`/chat/admin/attachments/${attachmentId}/action/`, {
      action: 'permanent_delete',
    })
  },

  restoreFromTrash(attachmentId) {
    return apiClient.post(`/chat/admin/attachments/${attachmentId}/action/`, {
      action: 'restore_from_trash',
    })
  },

  batchAction(action, attachmentIds) {
    return apiClient.post('/chat/admin/attachments/batch/', {
      action,
      attachment_ids: attachmentIds,
    })
  },

  batchIndex(attachmentIds) {
    return this.batchAction('index', attachmentIds)
  },

  batchUnindex(attachmentIds) {
    return this.batchAction('unindex', attachmentIds)
  },

  batchDelete(attachmentIds) {
    return this.batchAction('delete', attachmentIds)
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
