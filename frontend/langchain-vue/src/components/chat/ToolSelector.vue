<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  SetUp, Upload, Refresh, Delete, Edit, Check, Warning,
  Connection, VideoPlay, MagicStick, View, InfoFilled,
} from '@element-plus/icons-vue'
import { toolsAPI } from '@/api/tools'
import { useToolsStore } from '../../stores/tools'
import ToolUploadDialog from './ToolUploadDialog.vue'
import McpUploadDialog from './McpUploadDialog.vue'
import SkillUploadDialog from './SkillUploadDialog.vue'
import MarkdownRenderer from '../common/MarkdownRenderer.vue'

const toolsStore = useToolsStore()

const props = defineProps({
  modelValue: {
    type: Array,
    default: () => [],
  },
})

const emit = defineEmits({
  'update:modelValue': (val) => Array.isArray(val),
  'update:selectedMcpServers': (val) => Array.isArray(val),
  'update:useMcp': (val) => typeof val === 'boolean',
  change: () => true,
})

// ── Tab 状态 ──
const activeTab = ref('langchain')

// ── 各 Tab 数据（从 store 读取） ──
const langchainTools = computed(() => toolsStore.langchainTools)
const mcpServers = computed(() => toolsStore.mcpServers)
const skills = computed(() => toolsStore.skills)
const skillPackages = computed(() => toolsStore.skillPackages)

const langchainLoading = computed(() => toolsStore.langchainLoading)
const mcpLoading = computed(() => toolsStore.mcpLoading)
const skillLoading = computed(() => toolsStore.skillLoading)

// ── 各 Tab 独立选择状态 ──
const selectedLangChainTools = ref([])
const selectedMcpServers = ref([])
const selectedSkills = ref([])

// ── 对话框 ──
const showToolUploadDialog = ref(false)
const showMcpUploadDialog = ref(false)
const showSkillUploadDialog = ref(false)
const editingMcpServer = ref(null)
const editingCustomTool = ref(null)
const editingSkill = ref(null)

// ── Skill 预览 ──
const skillPreviewVisible = ref(false)
const skillPreviewTitle = ref('')
const skillPreviewContent = ref('')

// ── 合并后的选中工具名（对外接口） ──
const mergedSelected = computed(() => [
  ...selectedLangChainTools.value,
  ...selectedMcpServers.value,
  ...selectedSkills.value,
])

// 可选工具总数（用于统计展示）
const totalSelectable = computed(() =>
  selectableLangchainTools.value.length + mcpServers.value.length + skills.value.length + skillPackages.value.length,
)

function splitModelValue(val) {
  const safeVal = Array.isArray(val) ? val : []
  const langchainNames = new Set(langchainTools.value.map(t => t.name))
  const mcpNames = new Set(mcpServers.value.map(s => s.name))
  const skillNames = new Set([
    ...skills.value.map(s => s.name),
    ...skillPackages.value.map(p => `skill_${p.name}`),
  ])

  const langchain = []
  const mcp = []
  const skill = []

  for (const name of safeVal) {
    if (name.startsWith('skill_') || skillNames.has(name)) {
      skill.push(name)
    } else if (mcpNames.has(name)) {
      mcp.push(name)
    } else if (langchainNames.has(name)) {
      langchain.push(name)
    } else {
      langchain.push(name)
    }
  }
  return { langchain, mcp, skill }
}

watch(() => props.modelValue, (val) => {
  const { langchain, mcp, skill } = splitModelValue(val)
  selectedLangChainTools.value = langchain
  selectedMcpServers.value = mcp
  selectedSkills.value = skill
}, { immediate: true })

watch([langchainTools, mcpServers, skills, skillPackages], () => {
  const { langchain, mcp, skill } = splitModelValue(props.modelValue)
  const lc = JSON.stringify(selectedLangChainTools.value)
  const mc = JSON.stringify(selectedMcpServers.value)
  const sc = JSON.stringify(selectedSkills.value)
  if (JSON.stringify(langchain) !== lc || JSON.stringify(mcp) !== mc || JSON.stringify(skill) !== sc) {
    selectedLangChainTools.value = langchain
    selectedMcpServers.value = mcp
    selectedSkills.value = skill
  }
})

// 任何 Tab 选择变化时合并后 emit，同时 emit MCP 相关事件
watch(mergedSelected, (val) => {
  // 防止循环更新：值未实际变化时跳过 emit
  const current = props.modelValue
  if (val.length === current.length && val.every(v => current.includes(v))) return
  emit('update:modelValue', val)
  emit('update:selectedMcpServers', selectedMcpServers.value)
  emit('update:useMcp', selectedMcpServers.value.length > 0)
  emit('change')
}, { deep: true })

// ── 数据获取（委托给 store，store 内部有缓存判断） ──
const fetchLangchainTools = (force = false) => toolsStore.loadLangchainTools(force)
const fetchMcpServers = (force = false) => toolsStore.loadMcpServers(force)
const fetchSkills = (force = false) => toolsStore.loadSkills(force)
const fetchSkillPackages = (force = false) => toolsStore.loadSkillPackages(force)

// Tab 切换时按需加载数据
watch(activeTab, (tab) => {
  if (tab === 'langchain') fetchLangchainTools()
  else if (tab === 'mcp') fetchMcpServers()
  else if (tab === 'skill') { fetchSkills(); fetchSkillPackages() }
})

// ── LangChain Tab 操作 ──
const systemLangchainTools = computed(() =>
  toolsStore.systemLangchainTools.filter(t => t.visibility === 'selectable'),
)
const userLangchainTools = computed(() =>
  toolsStore.userLangchainTools.filter(t => t.visibility === 'selectable'),
)
const selectableLangchainTools = computed(() =>
  langchainTools.value.filter(t => t.visibility === 'selectable'),
)
const coreLangchainTools = computed(() =>
  toolsStore.langchainTools.filter(t => t.visibility === 'core'),
)
const switchLangchainTools = computed(() =>
  toolsStore.langchainTools.filter(t => t.visibility === 'switch'),
)
const hasAutoInfoTools = computed(() =>
  coreLangchainTools.value.length > 0 || switchLangchainTools.value.length > 0
)

const isLangchainSelected = (name) => selectedLangChainTools.value.includes(name)

const toggleLangchainTool = (name) => {
  const idx = selectedLangChainTools.value.indexOf(name)
  if (idx >= 0) {
    selectedLangChainTools.value = selectedLangChainTools.value.filter(n => n !== name)
  } else {
    selectedLangChainTools.value = [...selectedLangChainTools.value, name]
  }
}

const selectAllLangchain = () => {
  selectedLangChainTools.value = selectableLangchainTools.value.map(t => t.name)
}

const clearLangchain = () => {
  selectedLangChainTools.value = []
}

const handleDeleteCustomTool = (tool) => withPopoverLock(async () => {
  try {
    await ElMessageBox.confirm(
      `确定删除自定义工具 "${tool.name}" 吗？`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  try {
    await toolsAPI.deleteCustomTool({ name: tool.name })
    ElMessage.success(`工具 "${tool.name}" 已删除`)
    selectedLangChainTools.value = selectedLangChainTools.value.filter(n => n !== tool.name)
    toolsStore.invalidateLangchainTools()
    fetchLangchainTools()
  } catch (e) {
    ElMessage.error('删除失败: ' + (e.response?.data?.message || e.message))
  }
})

const handleToggleCustomTool = (tool) => withPopoverLock(async () => {
  const newStatus = tool.status === 'active' ? 'disabled' : 'active'
  const label = newStatus === 'active' ? '启用' : '禁用'
  try {
    await toolsAPI.toggleCustomTool({ name: tool.name, status: newStatus })
    ElMessage.success(`工具 "${tool.name}" 已${label}`)
    if (newStatus === 'disabled') {
      selectedLangChainTools.value = selectedLangChainTools.value.filter(n => n !== tool.name)
    }
    toolsStore.invalidateLangchainTools()
    fetchLangchainTools()
  } catch (e) {
    ElMessage.error(`${label}失败: ` + (e.response?.data?.message || e.message))
  }
})

// ── MCP Tab 操作 ──
const systemMcpServers = computed(() => toolsStore.systemMcpServers)
const userMcpServers = computed(() => toolsStore.userMcpServers)

const isMcpSelected = (name) => selectedMcpServers.value.includes(name)

const toggleMcpServer = (name) => {
  const idx = selectedMcpServers.value.indexOf(name)
  if (idx >= 0) {
    selectedMcpServers.value = selectedMcpServers.value.filter(n => n !== name)
  } else {
    selectedMcpServers.value = [...selectedMcpServers.value, name]
  }
}

const selectAllMcp = () => {
  selectedMcpServers.value = mcpServers.value.map(s => s.name)
}

const clearMcp = () => {
  selectedMcpServers.value = []
}

const handleDeleteMcpServer = (name) => withPopoverLock(async () => {
  try {
    await ElMessageBox.confirm(
      `确定删除 MCP Server "${name}" 吗？`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  try {
    await toolsAPI.deleteMcpServer(name)
    ElMessage.success(`MCP Server "${name}" 已删除`)
    selectedMcpServers.value = selectedMcpServers.value.filter(n => n !== name)
    toolsStore.invalidateMcpServers()
    fetchMcpServers()
  } catch (e) {
    ElMessage.error('删除失败: ' + (e.response?.data?.message || e.message))
  }
})

const mcpTestingName = ref(null)
const mcpTestResult = ref(null)

const handleTestMcpServer = (name) => withPopoverLock(async () => {
  mcpTestingName.value = name
  mcpTestResult.value = null
  try {
    const res = await toolsAPI.testMcpServer(name)
    const data = res.data?.data || res.data
    if (data.connected) {
      mcpTestResult.value = { name, success: true, message: `连接成功，共 ${data.toolCount} 个工具` }
    } else {
      mcpTestResult.value = { name, success: false, message: `连接失败${data.error ? '：' + data.error : ''}` }
    }
  } catch (e) {
    mcpTestResult.value = { name, success: false, message: '连接测试失败: ' + (e.response?.data?.message || e.message) }
  } finally {
    mcpTestingName.value = null
  }
})

const handleEditMcpServer = (srv) => {
  editingMcpServer.value = { ...srv }
  showMcpUploadDialog.value = true
}

const handleEditCustomTool = (tool) => {
  editingCustomTool.value = { ...tool }
  showToolUploadDialog.value = true
}

const handleEditSkill = (skill) => {
  editingSkill.value = { ...skill }
  showSkillUploadDialog.value = true
}

const handleToggleMcpServer = (srv) => withPopoverLock(async () => {
  const newStatus = srv.status === 'active' ? 'disabled' : 'active'
  const label = newStatus === 'active' ? '启用' : '禁用'
  try {
    await toolsAPI.toggleMcpServer({ name: srv.name, status: newStatus })
    ElMessage.success(`MCP Server "${srv.name}" 已${label}`)
    if (newStatus === 'disabled') {
      selectedMcpServers.value = selectedMcpServers.value.filter(n => n !== srv.name)
    }
    toolsStore.invalidateMcpServers()
    fetchMcpServers()
  } catch (e) {
    ElMessage.error(`${label}失败: ` + (e.response?.data?.message || e.message))
  }
})

// ── Skill Tab 操作 ──
const systemSkills = computed(() => toolsStore.systemSkills)
const userSkills = computed(() => toolsStore.userSkills)

const isSkillSelected = (name) => selectedSkills.value.includes(name)

const toggleSkill = (name) => {
  const idx = selectedSkills.value.indexOf(name)
  if (idx >= 0) {
    selectedSkills.value = selectedSkills.value.filter(n => n !== name)
  } else {
    selectedSkills.value = [...selectedSkills.value, name]
  }
}

const selectAllSkills = () => {
  selectedSkills.value = [
    ...skills.value.map(s => s.name),
    ...skillPackages.value.map(p => `skill_${p.name}`),
  ]
}

const clearSkills = () => {
  selectedSkills.value = []
}

const handleDeleteSkill = (skill) => withPopoverLock(async () => {
  try {
    await ElMessageBox.confirm(
      `确定删除技能 "${skill.name}" 吗？`,
      '删除确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  try {
    await toolsAPI.deleteSkill({ name: skill.name })
    ElMessage.success(`技能 "${skill.name}" 已删除`)
    selectedSkills.value = selectedSkills.value.filter(n => n !== skill.name)
    toolsStore.invalidateSkills()
    fetchSkills()
  } catch (e) {
    ElMessage.error('删除失败: ' + (e.response?.data?.message || e.message))
  }
})

const handleToggleSkill = (skill) => withPopoverLock(async () => {
  const newStatus = skill.status === 'active' ? 'disabled' : 'active'
  const label = newStatus === 'active' ? '启用' : '禁用'
  try {
    await toolsAPI.toggleSkill({ name: skill.name, status: newStatus })
    ElMessage.success(`技能 "${skill.name}" 已${label}`)
    if (newStatus === 'disabled') {
      selectedSkills.value = selectedSkills.value.filter(n => n !== skill.name)
    }
    toolsStore.invalidateSkills()
    fetchSkills()
  } catch (e) {
    ElMessage.error(`${label}失败: ` + (e.response?.data?.message || e.message))
  }
})

// ── Skill Packages 操作 ──
const isSkillPackageSelected = (name) => {
  return selectedSkills.value.includes(`skill_${name}`)
}

const toggleSkillPackage = (name) => {
  const toolName = `skill_${name}`
  const idx = selectedSkills.value.indexOf(toolName)
  if (idx >= 0) {
    selectedSkills.value = selectedSkills.value.filter(n => n !== toolName)
  } else {
    selectedSkills.value = [...selectedSkills.value, toolName]
  }
}

const toggleSkillPackageStatus = (pkg) => withPopoverLock(async () => {
  const newStatus = pkg.status === 'active' ? 'disabled' : 'active'
  try {
    await toolsAPI.toggleSkillPackage({ name: pkg.name, status: newStatus })
    ElMessage.success(`技能包 "${pkg.name}" 已${newStatus === 'active' ? '启用' : '禁用'}`)
    if (newStatus === 'disabled') {
      const toolName = `skill_${pkg.name}`
      selectedSkills.value = selectedSkills.value.filter(n => n !== toolName)
    }
    fetchSkillPackages(true)
  } catch (e) {
    ElMessage.error('操作失败: ' + (e.response?.data?.message || e.message))
  }
})

const deleteSkillPackage = (name) => withPopoverLock(async () => {
  try {
    await ElMessageBox.confirm(`确定删除技能包 "${name}"？`, '确认删除', { type: 'warning' })
    await toolsAPI.deleteSkillPackage({ name })
    ElMessage.success(`技能包 "${name}" 已删除`)
    const toolName = `skill_${name}`
    selectedSkills.value = selectedSkills.value.filter(n => n !== toolName)
    fetchSkillPackages(true)
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('删除失败: ' + (e.response?.data?.message || e.message))
    }
  }
})

const viewSkillPackageDetail = async (name) => {
  try {
    const res = await toolsAPI.getSkillPackageDetail({ name })
    const instructions = res.data?.data?.instructions || '无指令内容'
    skillPreviewTitle.value = `技能包: ${name}`
    skillPreviewContent.value = instructions
    skillPreviewVisible.value = true
  } catch (e) {
    ElMessage.error('获取详情失败: ' + (e.response?.data?.message || e.message))
  }
}

// ── 上传按钮 ──
const handleUpload = () => {
  if (activeTab.value === 'langchain') {
    editingCustomTool.value = null
    showToolUploadDialog.value = true
  } else if (activeTab.value === 'mcp') {
    editingMcpServer.value = null
    showMcpUploadDialog.value = true
  } else if (activeTab.value === 'skill') {
    editingSkill.value = null
    showSkillUploadDialog.value = true
  }
}

const handleUploadSuccess = async () => {
  showToolUploadDialog.value = false
  editingCustomTool.value = null
  const oldNames = new Set(langchainTools.value.map(t => t.name))
  toolsStore.invalidateLangchainTools()
  await fetchLangchainTools(true)
  const newNames = langchainTools.value.filter(t => !oldNames.has(t.name)).map(t => t.name)
  if (newNames.length > 0) {
    selectedLangChainTools.value = [...selectedLangChainTools.value, ...newNames]
  }
}

const handleMcpAddSuccess = async () => {
  showMcpUploadDialog.value = false
  editingMcpServer.value = null
  toolsStore.invalidateMcpServers()
  await fetchMcpServers(true)
}

const handleSkillCreateSuccess = async () => {
  showSkillUploadDialog.value = false
  editingSkill.value = null
  const oldSkillNames = new Set(skills.value.map(s => s.name))
  const oldPkgNames = new Set(skillPackages.value.map(p => p.name))
  toolsStore.invalidateSkills()
  await fetchSkills(true)
  await fetchSkillPackages(true)
  const newSkillNames = skills.value.filter(s => !oldSkillNames.has(s.name)).map(s => s.name)
  const newPkgNames = skillPackages.value.filter(p => !oldPkgNames.has(p.name)).map(p => `skill_${p.name}`)
  const newSelected = [...newSkillNames, ...newPkgNames]
  if (newSelected.length > 0) {
    selectedSkills.value = [...selectedSkills.value, ...newSelected]
  }
}

// ── 刷新按钮 ──
const handleRefresh = () => {
  if (activeTab.value === 'langchain') fetchLangchainTools(true)
  else if (activeTab.value === 'mcp') fetchMcpServers(true)
  else if (activeTab.value === 'skill') { fetchSkills(true); fetchSkillPackages(true) }
}

const currentLoading = computed(() => {
  if (activeTab.value === 'langchain') return langchainLoading.value
  if (activeTab.value === 'mcp') return mcpLoading.value
  return skillLoading.value
})

const handlePopoverShow = () => {
  if (activeTab.value === 'langchain') fetchLangchainTools()
  else if (activeTab.value === 'mcp') fetchMcpServers()
  else if (activeTab.value === 'skill') { fetchSkills(); fetchSkillPackages() }
}

// ── Popover 可见性控制 ──
const popoverVisibleRaw = ref(false)
const popoverLocked = ref(false)  // 操作期间锁定，防止 popover 被关闭

/** writable computed：锁定时拦截关闭，Element Plus 尝试设 visible=false 时直接拒绝 */
const popoverVisible = computed({
  get: () => popoverVisibleRaw.value,
  set: (val) => {
    if (!val && popoverLocked.value) return  // 锁定期间拒绝关闭
    popoverVisibleRaw.value = val
  },
})

/** 包裹异步操作（ElMessageBox 等），期间锁定 popover 不关闭 */
const withPopoverLock = async (fn) => {
  popoverLocked.value = true
  try {
    await fn()
  } finally {
    setTimeout(() => { popoverLocked.value = false }, 200)
  }
}

// Dialog 打开期间持续锁定 popover（Dialog 关闭时不会误关 popover）
watch(
  [showToolUploadDialog, showMcpUploadDialog, showSkillUploadDialog, skillPreviewVisible],
  ([a, b, c, d]) => {
    if (a || b || c || d) {
      popoverLocked.value = true
    } else {
      // Dialog 全部关闭后延迟解锁
      setTimeout(() => { popoverLocked.value = false }, 200)
    }
  },
)

onMounted(() => {
  fetchLangchainTools()
  fetchMcpServers()
  fetchSkills()
  fetchSkillPackages()
})
</script>

<template>
  <el-popover
    v-model:visible="popoverVisible"
    placement="top-start"
    :width="560"
    trigger="click"
    :persistent="true"
    :teleported="true"
    :show-arrow="false"
    popper-class="tool-selector-popper"
    @show="handlePopoverShow"
  >
    <template #reference>
      <button
        type="button"
        class="toolbar-btn"
        :class="{ active: mergedSelected.length > 0 }"
        title="工具选择"
      >
        <el-icon :size="18"><SetUp /></el-icon>
        <span v-if="mergedSelected.length > 0" class="tool-badge">{{ mergedSelected.length }}</span>
      </button>
    </template>

    <div class="tool-selector">
      <div class="tool-selector-header">
        <span class="tool-selector-title">工具选择</span>
        <div class="tool-selector-actions">
          <el-tooltip content="刷新工具列表" placement="top">
            <el-button
              :icon="Refresh"
              size="small"
              circle
              :loading="currentLoading"
              @click="handleRefresh"
            />
          </el-tooltip>
          <el-tooltip content="上传/添加" placement="top">
            <el-button
              :icon="Upload"
              size="small"
              circle
              type="primary"
              title="上传/添加"
              @click="handleUpload"
            />
          </el-tooltip>
        </div>
      </div>

      <div v-if="mergedSelected.length > 0" class="selection-summary">
        <el-icon class="summary-icon"><Check /></el-icon>
        <span class="summary-text">
          已选 <strong>{{ mergedSelected.length }}</strong> 个工具
          <span class="summary-meta">
            （LangChain {{ selectedLangChainTools.length }} · MCP {{ selectedMcpServers.length }} · Skill {{ selectedSkills.length }}）
          </span>
        </span>
      </div>

      <el-tabs v-model="activeTab" class="tool-tabs">
        <!-- ── LangChain Tab ── -->
        <el-tab-pane name="langchain">
          <template #label>
            <span class="tab-label"><el-icon><SetUp /></el-icon> LangChain</span>
          </template>

          <div class="tab-batch-actions">
            <el-button size="small" link type="primary" @click="selectAllLangchain">全选</el-button>
            <el-button size="small" link type="info" @click="clearLangchain">清空</el-button>
          </div>

          <div v-if="langchainLoading" class="tool-loading">
            <el-icon class="is-loading"><Refresh /></el-icon> 加载中...
          </div>
          <div v-else-if="selectableLangchainTools.length === 0" class="tool-empty">暂无可用工具</div>
          <div v-else class="tool-list">
            <div v-if="systemLangchainTools.length > 0" class="tool-group">
              <div class="tool-group-label">系统内置</div>
              <div
                v-for="tool in systemLangchainTools"
                :key="tool.name"
                :class="['tool-item', { selected: isLangchainSelected(tool.name) }]"
              >
                <div class="tool-info">
                  <span class="tool-name">
                    {{ tool.name }}
                    <span v-if="tool.category" class="tool-category-tag">{{ typeof tool.category === 'object' ? tool.category.name : tool.category }}</span>
                  </span>
                  <span v-if="tool.description" class="tool-desc">{{ tool.description }}</span>
                </div>
                <div class="tool-actions">
                  <el-checkbox
                    :model-value="isLangchainSelected(tool.name)"
                    @change="toggleLangchainTool(tool.name)"
                  />
                </div>
              </div>
            </div>

            <div v-if="userLangchainTools.length > 0" class="tool-group">
              <div class="tool-group-label">自定义</div>
              <div
                v-for="tool in userLangchainTools"
                :key="tool.name"
                :class="['tool-item', { selected: isLangchainSelected(tool.name) }]"
              >
                <div class="tool-info">
                  <span class="tool-name">
                    {{ tool.name }}
                    <span class="tool-tag-custom">自定义</span>
                  </span>
                  <span v-if="tool.description" class="tool-desc">{{ tool.description }}</span>
                </div>
                <div class="tool-actions">
                  <el-button
                    :icon="Edit"
                    size="small"
                    circle
                    text
                    type="primary"
                    title="编辑"
                    @click.stop="handleEditCustomTool(tool)"
                  />
                  <el-button
                    :icon="Delete"
                    size="small"
                    circle
                    text
                    type="danger"
                    title="删除"
                    @click.stop="handleDeleteCustomTool(tool)"
                  />
                  <el-checkbox
                    :model-value="isLangchainSelected(tool.name)"
                    @change="toggleLangchainTool(tool.name)"
                  />
                </div>
              </div>
            </div>
          </div>

          <div v-if="hasAutoInfoTools" class="tool-auto-info">
            <el-collapse>
              <el-collapse-item>
                <template #title>
                  <span class="auto-info-title">
                    <el-icon><InfoFilled /></el-icon>
                    自动加载与开关控制工具
                  </span>
                </template>
                <div v-if="coreLangchainTools.length > 0" class="auto-info-section">
                  <span class="auto-info-label">始终自动加载：</span>
                  <span class="auto-info-names">{{ coreLangchainTools.map(t => t.name).join(', ') }}</span>
                </div>
                <div v-if="switchLangchainTools.length > 0" class="auto-info-section">
                  <span class="auto-info-label">由开关控制：</span>
                  <span class="auto-info-names">{{ switchLangchainTools.map(t => t.name).join(', ') }}</span>
                </div>
              </el-collapse-item>
            </el-collapse>
          </div>

        </el-tab-pane>

        <!-- ── MCP Tab ── -->
        <el-tab-pane name="mcp">
          <template #label>
            <span class="tab-label"><el-icon><Connection /></el-icon> MCP</span>
          </template>

          <div class="tab-batch-actions">
            <el-button size="small" link type="primary" @click="selectAllMcp">全选</el-button>
            <el-button size="small" link type="info" @click="clearMcp">清空</el-button>
          </div>

          <div v-if="mcpLoading" class="tool-loading">
            <el-icon class="is-loading"><Refresh /></el-icon> 加载中...
          </div>
          <div v-else-if="mcpServers.length === 0" class="tool-empty">暂无 MCP 服务器</div>
          <div v-else class="tool-list">
            <div v-if="systemMcpServers.length > 0" class="tool-group">
              <div class="tool-group-label">系统内置</div>
              <div
                v-for="srv in systemMcpServers"
                :key="srv.name"
                :class="['tool-item', { selected: isMcpSelected(srv.name) }]"
              >
                <div class="tool-info">
                  <span class="tool-name">
                    {{ srv.name }}
                    <el-tag size="small" type="info" effect="plain">{{ srv.transport }}</el-tag>
                  </span>
                  <span v-if="srv.description" class="tool-desc">{{ srv.description }}</span>
                  <div v-if="mcpTestResult && mcpTestResult.name === srv.name" class="test-result-inline" :class="mcpTestResult.success ? 'success' : 'error'">
                    <el-icon v-if="mcpTestResult.success"><Check /></el-icon>
                    <el-icon v-else><Warning /></el-icon>
                    <span>{{ mcpTestResult.message }}</span>
                  </div>
                </div>
                <div class="tool-actions">
                  <el-button
                    :icon="VideoPlay"
                    size="small"
                    circle
                    :loading="mcpTestingName === srv.name"
                    title="测试连接"
                    @click.stop="handleTestMcpServer(srv.name)"
                  />
                  <el-checkbox
                    :model-value="isMcpSelected(srv.name)"
                    @change="toggleMcpServer(srv.name)"
                  />
                </div>
              </div>
            </div>

            <div v-if="userMcpServers.length > 0" class="tool-group">
              <div class="tool-group-label">自定义</div>
              <div
                v-for="srv in userMcpServers"
                :key="srv.name"
                :class="['tool-item', { selected: isMcpSelected(srv.name) }]"
              >
                <div class="tool-info">
                  <span class="tool-name">
                    {{ srv.name }}
                    <el-tag size="small" type="success" effect="plain">{{ srv.transport }}</el-tag>
                  </span>
                  <span v-if="srv.description" class="tool-desc">{{ srv.description }}</span>
                  <div v-if="mcpTestResult && mcpTestResult.name === srv.name" class="test-result-inline" :class="mcpTestResult.success ? 'success' : 'error'">
                    <el-icon v-if="mcpTestResult.success"><Check /></el-icon>
                    <el-icon v-else><Warning /></el-icon>
                    <span>{{ mcpTestResult.message }}</span>
                  </div>
                </div>
                <div class="tool-actions">
                  <el-button
                    :icon="VideoPlay"
                    size="small"
                    circle
                    :loading="mcpTestingName === srv.name"
                    title="测试连接"
                    @click.stop="handleTestMcpServer(srv.name)"
                  />
                  <el-button
                    :icon="Edit"
                    size="small"
                    circle
                    text
                    type="primary"
                    title="编辑"
                    @click.stop="handleEditMcpServer(srv)"
                  />
                  <el-button
                    :icon="Delete"
                    size="small"
                    circle
                    text
                    type="danger"
                    title="删除"
                    @click.stop="handleDeleteMcpServer(srv.name)"
                  />
                  <el-checkbox
                    :model-value="isMcpSelected(srv.name)"
                    @change="toggleMcpServer(srv.name)"
                  />
                </div>
              </div>
            </div>
          </div>

        </el-tab-pane>

        <!-- ── Skill Tab ── -->
        <el-tab-pane name="skill">
          <template #label>
            <span class="tab-label"><el-icon><MagicStick /></el-icon> Skill</span>
          </template>

          <div class="tab-batch-actions">
            <el-button size="small" link type="primary" @click="selectAllSkills">全选</el-button>
            <el-button size="small" link type="info" @click="clearSkills">清空</el-button>
          </div>

          <div v-if="skillLoading" class="tool-loading">
            <el-icon class="is-loading"><Refresh /></el-icon> 加载中...
          </div>
          <div v-else-if="skills.length === 0 && skillPackages.length === 0" class="tool-empty">暂无技能</div>
          <div v-else class="tool-list">
            <div v-if="skillPackages.length > 0" class="tool-group">
              <div class="tool-group-label">技能包</div>
              <div
                v-for="pkg in skillPackages"
                :key="pkg.name"
                :class="['tool-item', { selected: isSkillPackageSelected(pkg.name) }]"
              >
                <div class="tool-info">
                  <span class="tool-name">
                    {{ pkg.name }}
                    <el-tag v-if="pkg.source === 'system'" size="small" type="info">系统</el-tag>
                    <el-tag size="small" type="success" effect="plain">顾问</el-tag>
                  </span>
                  <span v-if="pkg.description" class="tool-desc">{{ pkg.description }}</span>
                </div>
                <div class="tool-actions">
                  <el-button size="small" circle text title="查看详情" @click.stop="viewSkillPackageDetail(pkg.name)">
                    <el-icon><View /></el-icon>
                  </el-button>
                  <el-button
v-if="pkg.source !== 'system'" size="small" circle text type="danger"
                    title="删除" @click.stop="deleteSkillPackage(pkg.name)">
                    <el-icon><Delete /></el-icon>
                  </el-button>
                  <el-checkbox :model-value="isSkillPackageSelected(pkg.name)" @change="toggleSkillPackage(pkg.name)" />
                </div>
              </div>
            </div>

            <div v-if="systemSkills.length > 0" class="tool-group">
              <div class="tool-group-label">系统预置</div>
              <div
                v-for="skill in systemSkills"
                :key="skill.name"
                :class="['tool-item', { selected: isSkillSelected(skill.name) }]"
              >
                <div class="tool-info">
                  <span class="tool-name">
                    {{ skill.name.replace(/^skill_/, '') }}
                    <el-tag v-if="skill.mode" size="small" :type="skill.mode === 'pipeline' ? 'primary' : skill.mode === 'advisor' ? 'success' : 'warning'" effect="plain">
                      {{ skill.mode === 'pipeline' ? '管线' : skill.mode === 'advisor' ? '顾问' : '混合' }}
                    </el-tag>
                  </span>
                  <span v-if="skill.description" class="tool-desc">{{ skill.description }}</span>
                </div>
                <div class="tool-actions">
                  <el-checkbox
                    :model-value="isSkillSelected(skill.name)"
                    @change="toggleSkill(skill.name)"
                  />
                </div>
              </div>
            </div>

            <div v-if="userSkills.length > 0" class="tool-group">
              <div class="tool-group-label">自定义</div>
              <div
                v-for="skill in userSkills"
                :key="skill.name"
                :class="['tool-item', { selected: isSkillSelected(skill.name) }]"
              >
                <div class="tool-info">
                  <span class="tool-name">
                    {{ skill.name.replace(/^skill_/, '') }}
                    <span class="tool-tag-custom">自定义</span>
                    <el-tag v-if="skill.mode" size="small" :type="skill.mode === 'pipeline' ? 'primary' : skill.mode === 'advisor' ? 'success' : 'warning'" effect="plain">
                      {{ skill.mode === 'pipeline' ? '管线' : skill.mode === 'advisor' ? '顾问' : '混合' }}
                    </el-tag>
                  </span>
                  <span v-if="skill.description" class="tool-desc">{{ skill.description }}</span>
                </div>
                <div class="tool-actions">
                  <el-button
                    :icon="Edit"
                    size="small"
                    circle
                    text
                    type="primary"
                    title="编辑"
                    @click.stop="handleEditSkill(skill)"
                  />
                  <el-button
                    :icon="Delete"
                    size="small"
                    circle
                    text
                    type="danger"
                    title="删除"
                    @click.stop="handleDeleteSkill(skill)"
                  />
                  <el-checkbox
                    :model-value="isSkillSelected(skill.name)"
                    @change="toggleSkill(skill.name)"
                  />
                </div>
              </div>
            </div>
          </div>

        </el-tab-pane>
      </el-tabs>
    </div>

    <ToolUploadDialog
      v-model="showToolUploadDialog"
      :editing-tool="editingCustomTool"
      @success="handleUploadSuccess"
    />
    <McpUploadDialog
      v-model="showMcpUploadDialog"
      :editing-server="editingMcpServer"
      @success="handleMcpAddSuccess"
    />
    <SkillUploadDialog
      v-model="showSkillUploadDialog"
      :editing-skill="editingSkill"
      @success="handleSkillCreateSuccess"
    />
    <el-dialog
      v-model="skillPreviewVisible"
      :title="skillPreviewTitle"
      width="640px"
      :close-on-click-modal="true"
      append-to-body
    >
      <MarkdownRenderer :content="skillPreviewContent" />
    </el-dialog>
  </el-popover>
</template>

<style scoped>
.toolbar-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--muted-foreground);
  cursor: pointer;
  transition: all var(--transition-fast);
  position: relative;
}

.toolbar-btn:hover:not(:disabled) {
  background: var(--accent);
  color: var(--foreground);
}

.toolbar-btn.active {
  color: var(--sidebar-primary);
  background: color-mix(in srgb, var(--sidebar-primary) 10%, transparent);
}

.tool-badge {
  position: absolute;
  top: -2px;
  right: -2px;
  min-width: 16px;
  height: 16px;
  border-radius: 8px;
  background: var(--sidebar-primary);
  color: white;
  font-size: 10px;
  line-height: 16px;
  text-align: center;
  padding: 0 4px;
}

.tool-selector {
  display: flex;
  flex-direction: column;
  max-height: min(560px, calc(100vh - 120px));
  overflow: hidden;
}

.tool-selector-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  flex-shrink: 0;
}

.tool-selector-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--foreground);
}

.tool-selector-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.tool-selector-actions .el-button.is-circle {
  width: 28px;
  height: 28px;
  padding: 0;
}

.tool-tabs {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.tool-tabs :deep(.el-tabs__header) {
  margin-bottom: 8px;
  flex-shrink: 0;
}

.tool-tabs :deep(.el-tabs__content) {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: flex;
}

.tool-tabs :deep(.el-tab-pane) {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.tab-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
}

.tab-batch-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.tool-loading,
.tool-empty {
  text-align: center;
  padding: 20px;
  font-size: 13px;
  color: var(--muted-foreground);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.tool-list {
  flex: 1;
  overflow-y: auto;
  max-height: 280px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tool-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tool-group-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--muted-foreground);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 2px 0;
}

.tool-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 8px;
  border-radius: 6px;
  border: 1px solid transparent;
  cursor: pointer;
  transition: all var(--transition-fast);
  gap: 6px;
}

.tool-item:hover {
  background: var(--accent);
  border-color: var(--border);
}

.tool-item.selected {
  background: color-mix(in srgb, var(--sidebar-primary) 6%, transparent);
  border-color: var(--sidebar-primary);
}

.tool-info {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.tool-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--foreground);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  line-height: 1.3;
}

.tool-category-tag {
  font-size: 10px;
  font-weight: 400;
  color: var(--muted-foreground);
  background: var(--accent);
  padding: 0 4px;
  border-radius: 3px;
}

.tool-tag-custom {
  font-size: 10px;
  font-weight: 500;
  color: var(--el-color-primary);
  background: color-mix(in srgb, var(--el-color-primary) 10%, transparent);
  padding: 0 4px;
  border-radius: 3px;
}

.tool-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.tool-desc {
  font-size: 11px;
  color: var(--muted-foreground);
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 1px;
  line-height: 1.3;
}

.selection-summary {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 8px;
  padding: 6px 10px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--sidebar-primary) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--sidebar-primary) 25%, transparent);
  font-size: 12px;
  color: var(--foreground);
  line-height: 1.4;
}

.selection-summary .summary-icon {
  color: var(--sidebar-primary);
  font-size: 14px;
  flex-shrink: 0;
}

.selection-summary .summary-text strong {
  color: var(--sidebar-primary);
  font-weight: 600;
  margin: 0 1px;
}

.selection-summary .summary-meta {
  color: var(--muted-foreground);
  font-size: 11px;
  margin-left: 4px;
}

.test-result-inline {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  margin-top: 2px;
}

.test-result-inline.success {
  color: var(--el-color-success);
}

.test-result-inline.error {
  color: var(--el-color-danger);
}

.tool-auto-info {
  margin-top: 8px;
  border-top: 1px solid var(--border);
  padding-top: 4px;
  flex-shrink: 0;
}

.tool-auto-info :deep(.el-collapse) {
  border: none;
}

.tool-auto-info :deep(.el-collapse-item__header) {
  height: 28px;
  line-height: 28px;
  font-size: 12px;
  background: transparent;
  border: none;
  padding: 0;
}

.tool-auto-info :deep(.el-collapse-item__wrap) {
  border: none;
  background: transparent;
}

.tool-auto-info :deep(.el-collapse-item__content) {
  padding: 0 0 4px 0;
  font-size: 11px;
}

.auto-info-title {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--muted-foreground);
  font-size: 12px;
}

.auto-info-section {
  display: flex;
  gap: 4px;
  margin-bottom: 2px;
  line-height: 1.5;
}

.auto-info-label {
  color: var(--muted-foreground);
  white-space: nowrap;
  flex-shrink: 0;
}

.auto-info-names {
  color: var(--foreground);
  word-break: break-all;
}
</style>

<style>
/* Popper 层级全局样式：避免 scoped 失效 */
.tool-selector-popper {
  padding: 12px !important;
  border-radius: 10px !important;
  border: 1px solid var(--border) !important;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12) !important;
  max-height: calc(100vh - 80px) !important;
  overflow: hidden !important;
}

.tool-selector-popper .el-popper__arrow {
  display: none !important;
}
</style>
