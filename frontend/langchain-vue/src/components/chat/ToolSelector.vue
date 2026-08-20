<script setup>
import { ref, computed, watch, onMounted, provide } from 'vue'
import { SetUp, Upload, Refresh, Check, Connection, MagicStick } from '@element-plus/icons-vue'
import { useToolsStore } from '../../stores/tools'
import LangchainToolList from './tool-selector/LangchainToolList.vue'
import McpServerList from './tool-selector/McpServerList.vue'
import SkillList from './tool-selector/SkillList.vue'

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

// ── 各 Tab 数据（从 store 读取，用于 modelValue 拆分/聚合） ──
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

// ── 合并后的选中工具名（对外接口） ──
const mergedSelected = computed(() => [
  ...selectedLangChainTools.value,
  ...selectedMcpServers.value,
  ...selectedSkills.value,
])

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

// ── Tab 子组件引用（header「上传/添加」按钮按当前 tab 打开对应对话框） ──
const langchainToolListRef = ref(null)
const mcpServerListRef = ref(null)
const skillListRef = ref(null)

// ── 上传按钮 ──
const handleUpload = () => {
  if (activeTab.value === 'langchain') {
    langchainToolListRef.value?.openUploadDialog()
  } else if (activeTab.value === 'mcp') {
    mcpServerListRef.value?.openUploadDialog()
  } else if (activeTab.value === 'skill') {
    skillListRef.value?.openUploadDialog()
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

/** 包裹异步操作（ElMessageBox 等），期间锁定 popover 不关闭；注入给各 Tab 子组件使用 */
const withPopoverLock = async (fn) => {
  popoverLocked.value = true
  try {
    await fn()
  } finally {
    setTimeout(() => { popoverLocked.value = false }, 200)
  }
}
provide('toolSelectorWithPopoverLock', withPopoverLock)

// Dialog 打开期间持续锁定 popover（Dialog 关闭时不会误关 popover）
// 各 Tab 子组件上报自身对话框可见性，父容器聚合
const langchainDialogOpen = ref(false)
const mcpDialogOpen = ref(false)
const skillDialogOpen = ref(false)

watch([langchainDialogOpen, mcpDialogOpen, skillDialogOpen], ([a, b, c]) => {
  if (a || b || c) {
    popoverLocked.value = true
  } else {
    // Dialog 全部关闭后延迟解锁
    setTimeout(() => { popoverLocked.value = false }, 200)
  }
})

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

          <langchain-tool-list
            ref="langchainToolListRef"
            :selected="selectedLangChainTools"
            @update:selected="selectedLangChainTools = $event"
            @dialog-visibility-change="langchainDialogOpen = $event"
          />
        </el-tab-pane>

        <!-- ── MCP Tab ── -->
        <el-tab-pane name="mcp">
          <template #label>
            <span class="tab-label"><el-icon><Connection /></el-icon> MCP</span>
          </template>

          <mcp-server-list
            ref="mcpServerListRef"
            :selected="selectedMcpServers"
            @update:selected="selectedMcpServers = $event"
            @dialog-visibility-change="mcpDialogOpen = $event"
          />
        </el-tab-pane>

        <!-- ── Skill Tab ── -->
        <el-tab-pane name="skill">
          <template #label>
            <span class="tab-label"><el-icon><MagicStick /></el-icon> Skill</span>
          </template>

          <skill-list
            ref="skillListRef"
            :selected="selectedSkills"
            @update:selected="selectedSkills = $event"
            @dialog-visibility-change="skillDialogOpen = $event"
          />
        </el-tab-pane>
      </el-tabs>
    </div>
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

/* 跨 Tab 共享的列表内容样式：scoped 样式无法命中子组件内部元素，
   统一放在 popper 命名空间下的全局块，供三个 Tab 子组件复用 */
.tool-selector-popper .tab-batch-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.tool-selector-popper .tool-loading,
.tool-selector-popper .tool-empty {
  text-align: center;
  padding: 20px;
  font-size: 13px;
  color: var(--muted-foreground);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.tool-selector-popper .tool-list {
  flex: 1;
  overflow-y: auto;
  max-height: 280px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tool-selector-popper .tool-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tool-selector-popper .tool-group-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--muted-foreground);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 2px 0;
}

.tool-selector-popper .tool-item {
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

.tool-selector-popper .tool-item:hover {
  background: var(--accent);
  border-color: var(--border);
}

.tool-selector-popper .tool-item.selected {
  background: color-mix(in srgb, var(--sidebar-primary) 6%, transparent);
  border-color: var(--sidebar-primary);
}

.tool-selector-popper .tool-info {
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.tool-selector-popper .tool-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--foreground);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  line-height: 1.3;
}

.tool-selector-popper .tool-tag-custom {
  font-size: 10px;
  font-weight: 500;
  color: var(--el-color-primary);
  background: color-mix(in srgb, var(--el-color-primary) 10%, transparent);
  padding: 0 4px;
  border-radius: 3px;
}

.tool-selector-popper .tool-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.tool-selector-popper .tool-desc {
  font-size: 11px;
  color: var(--muted-foreground);
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 1px;
  line-height: 1.3;
}
</style>
