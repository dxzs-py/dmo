import { apiClient } from './axios'

export const toolsAPI = {
  getMcpTools(params = {}) { return apiClient.get('/tools/mcp/tools/', { params }) },
  testMcpServer(serverName) { return apiClient.post(`/tools/mcp/servers/${encodeURIComponent(serverName)}/test/`) },
  getMcpServers() { return apiClient.get('/tools/mcp/servers/') },
  addMcpServer(data) { return apiClient.post('/tools/mcp/servers/', data) },
  updateMcpServer(data) {
    const { name, ...rest } = data
    return apiClient.put(`/tools/mcp/servers/${encodeURIComponent(name)}/`, rest)
  },
  deleteMcpServer(name) { return apiClient.delete(`/tools/mcp/servers/${encodeURIComponent(name)}/`) },
  toggleMcpServer(data) {
    return apiClient.patch(`/tools/mcp/servers/${encodeURIComponent(data.name)}/status/`, { status: data.status })
  },
  getToolList(params = {}) { return apiClient.get('/tools/list/', { params }) },
  getToolMeta() { return apiClient.get('/tools/meta/') },
  uploadTool(data) { return apiClient.post('/tools/custom/', data) },
  getCustomTools() { return apiClient.get('/tools/custom/') },
  deleteCustomTool(data) { return apiClient.delete(`/tools/custom/${encodeURIComponent(data.name)}/`) },
  toggleCustomTool(data) {
    return apiClient.patch(`/tools/custom/${encodeURIComponent(data.name)}/status/`, { status: data.status })
  },
  updateCustomTool(data) {
    const { name, ...rest } = data
    return apiClient.put(`/tools/custom/${encodeURIComponent(name)}/`, rest)
  },
  // Skill CRUD
  getSkillList(params = {}) { return apiClient.get('/tools/skills/', { params }) },
  createSkill(data) { return apiClient.post('/tools/skills/', data) },
  updateSkill(data) {
    const { name, ...rest } = data
    return apiClient.put(`/tools/skills/${encodeURIComponent(name)}/`, rest)
  },
  deleteSkill(data) { return apiClient.delete(`/tools/skills/${encodeURIComponent(data.name)}/`) },
  toggleSkill(data) {
    return apiClient.patch(`/tools/skills/${encodeURIComponent(data.name)}/status/`, { status: data.status })
  },
  // Skill 包管理（Agent Skills 规范）
  getSkillPackages() { return apiClient.get('/tools/skills/packages/') },
  uploadSkillPackage(formData) { return apiClient.post('/tools/skills/packages/', formData, { headers: { 'Content-Type': 'multipart/form-data' } }) },
  deleteSkillPackage(data) { return apiClient.delete(`/tools/skills/packages/${encodeURIComponent(data.name)}/`) },
  toggleSkillPackage(data) {
    return apiClient.patch(`/tools/skills/packages/${encodeURIComponent(data.name)}/status/`, { status: data.status })
  },
  getSkillPackageDetail(params) { return apiClient.get(`/tools/skills/packages/${encodeURIComponent(params.name)}/`) },
}
