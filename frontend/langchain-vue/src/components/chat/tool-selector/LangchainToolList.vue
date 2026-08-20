<script setup>
import { ref, computed, watch, inject } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Edit, InfoFilled, Refresh } from '@element-plus/icons-vue'
import { toolsAPI } from '@/api/tools'
import { useToolsStore } from '@/stores/tools'
import ToolUploadDialog from '../ToolUploadDialog.vue'

const props = defineProps({
  selected: {
    type: Array,
    default: () => [],
  },
})

const emit = defineEmits({
  'update:selected': (val) => Array.isArray(val),
  'dialog-visibility-change': (val) => typeof val === 'boolean',
})

const toolsStore = useToolsStore()
/** 父容器（popover 拥有者）注入的 popover 锁包裹函数：异步操作期间锁定 popover 不关闭 */
const withPopoverLock = inject('toolSelectorWithPopoverLock')

// ── 数据（从 store 读取） ──
const langchainTools = computed(() => toolsStore.langchainTools)
const langchainLoading = computed(() => toolsStore.langchainLoading)

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

const fetchLangchainTools = (force = false) => toolsStore.loadLangchainTools(force)

// ── 对话框 ──
const showToolUploadDialog = ref(false)
const editingCustomTool = ref(null)

// 上报对话框可见性，父容器据此锁定 popover
watch(showToolUploadDialog, (open) => {
  emit('dialog-visibility-change', open)
})

/** 供父容器 header「上传/添加」按钮调用：以新增模式打开上传对话框 */
const openUploadDialog = () => {
  editingCustomTool.value = null
  showToolUploadDialog.value = true
}
defineExpose({ openUploadDialog })

// ── 选择操作 ──
const isLangchainSelected = (name) => props.selected.includes(name)

const toggleLangchainTool = (name) => {
  const idx = props.selected.indexOf(name)
  if (idx >= 0) {
    emit('update:selected', props.selected.filter(n => n !== name))
  } else {
    emit('update:selected', [...props.selected, name])
  }
}

const selectAllLangchain = () => {
  emit('update:selected', selectableLangchainTools.value.map(t => t.name))
}

const clearLangchain = () => {
  emit('update:selected', [])
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
    emit('update:selected', props.selected.filter(n => n !== tool.name))
    toolsStore.invalidateLangchainTools()
    fetchLangchainTools()
  } catch (e) {
    ElMessage.error('删除失败: ' + (e.response?.data?.message || e.message))
  }
})

const handleEditCustomTool = (tool) => {
  editingCustomTool.value = { ...tool }
  showToolUploadDialog.value = true
}

const handleUploadSuccess = async () => {
  showToolUploadDialog.value = false
  editingCustomTool.value = null
  const oldNames = new Set(langchainTools.value.map(t => t.name))
  toolsStore.invalidateLangchainTools()
  await fetchLangchainTools(true)
  const newNames = langchainTools.value.filter(t => !oldNames.has(t.name)).map(t => t.name)
  if (newNames.length > 0) {
    emit('update:selected', [...props.selected, ...newNames])
  }
}
</script>

<template>
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

  <ToolUploadDialog
    v-model="showToolUploadDialog"
    :editing-tool="editingCustomTool"
    @success="handleUploadSuccess"
  />
</template>

<style scoped>
.tool-category-tag {
  font-size: 10px;
  font-weight: 400;
  color: var(--muted-foreground);
  background: var(--accent);
  padding: 0 4px;
  border-radius: 3px;
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
