import { apiClient } from './axios'

export const attachmentAPI = {
  getStats() {
    return apiClient.get('/attachments/admin/stats/')
  },

  getList(params = {}) {
    return apiClient.get('/attachments/admin/', { params })
  },

  getTrashedList(params = {}) {
    return apiClient.get('/attachments/admin/', { params: { ...params, trashed: true } })
  },

  getDetail(attachmentId) {
    return apiClient.get(`/attachments/admin/${attachmentId}/`)
  },

  deleteAttachment(attachmentId) {
    return apiClient.delete(`/attachments/admin/${attachmentId}/`)
  },

  actionAttachment(attachmentId, action, data = {}) {
    return apiClient.post(`/attachments/admin/${attachmentId}/action/`, {
      action,
      ...data,
    })
  },

  indexAttachment(attachmentId) {
    return apiClient.post(`/attachments/admin/${attachmentId}/action/`, {
      action: 'index',
    })
  },

  unindexAttachment(attachmentId) {
    return apiClient.post(`/attachments/admin/${attachmentId}/action/`, {
      action: 'unindex',
    })
  },

  permanentDelete(attachmentId) {
    return apiClient.post(`/attachments/admin/${attachmentId}/action/`, {
      action: 'permanent_delete',
    })
  },

  restoreFromTrash(attachmentId) {
    return apiClient.post(`/attachments/admin/${attachmentId}/action/`, {
      action: 'restore_from_trash',
    })
  },

  batchAction(action, attachmentIds) {
    return apiClient.post('/attachments/admin/batch/', {
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
    return apiClient.post('/attachments/admin/cleanup/', {
      action,
      dry_run: dryRun,
    })
  },

  getStorageAlerts(params = {}) {
    return apiClient.get('/attachments/admin/storage-alerts/', { params })
  },

  handleStorageAlert(alertId, action) {
    return apiClient.post(`/attachments/admin/storage-alerts/${alertId}/`, { action })
  },
}
