import { apiClient } from './axios'

export const userAPI = {
  getProfile() { return apiClient.get('/users/profile/') },
  updateProfile(data) { return apiClient.put('/users/profile/', data) },
  uploadAvatar(formData) {
    return apiClient.post('/users/avatar/', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  changePassword(data) { return apiClient.post('/users/change-password/', data) },
  bindPhone(data) { return apiClient.post('/users/bind-phone/', data) },
  getPreferences() { return apiClient.get('/users/preferences/') },
  updatePreferences(data) { return apiClient.put('/users/preferences/', data) },
  getUsageStats() { return apiClient.get('/users/usage-stats/') },
  deleteAccount() { return apiClient.delete('/users/account/') },
}
