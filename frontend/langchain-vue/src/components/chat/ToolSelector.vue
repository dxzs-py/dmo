<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { SetUp, Upload, Refresh } from '@element-plus/icons-vue'
import { chatAPI } from '../../api'

const props = defineProps({
  modelValue: {
    type: Array,
    default: () => [],
  },
  enabled: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits({
  'update:modelValue': (val) => Array.isArray(val),
  'update:enabled': (val) => typeof val === 'boolean',
  change: () => true,
})

const tools = ref([])
const loading = ref(false)
const showUploadDialog = ref(false)
const activeCategory = ref('all')

const selectedTools = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val),
})

const enabled = computed({
  get: () => props.enabled,
  set: (val) => emit('update:enabled', val),
})

const categories = computed(() => {
  const cats = new Set(tools.value.map(t => t.category))
  return ['all', ...Array.from(cats)]
})

const categoryLabels = {
  all: '全部',
  basic: '基础',
  web_search: '搜索',
  file: '文件',
  weather: '天气',
  translation: '翻译',
  agent: '智能体',
  todo: '待办',
  web_fetch: '抓取',
}

const filteredTools = computed(() => {
  if (activeCategory.value === 'all') return tools.value
  return tools.value.filter(t => t.category === activeCategory.value)
})

const fetchTools = async () => {
  loading.value = true
  try {
    const res = await chatAPI.getToolList()
    const data = res.data?.data || res.data
    tools.value = data.tools || []
  } catch (e) {
    tools.value = []
  } finally {
    loading.value = false
  }
}

const toggleTool = (toolName) => {
  const idx = selectedTools.value.indexOf(toolName)
  if (idx >= 0) {
    selectedTools.value = selectedTools.value.filter(n => n !== toolName)
  } else {
    selectedTools.value = [...selectedTools.value, toolName]
  }
  emit('change')
}

const isSelected = (name) => selectedTools.value.includes(name)

const selectAll = () => {
  selectedTools.value = filteredTools.value.map(t => t.name)
  emit('change')
}

const clearAll = () => {
  selectedTools.value = []
  emit('change')
}

const handleUploadSuccess = () => {
  showUploadDialog.value = false
  fetchTools()
  emit('change')
}

watch(() => props.enabled, (val) => {
  if (val && tools.value.length === 0) {
    fetchTools()
  }
})

onMounted(() => {
  if (props.enabled) {
    fetchTools()
  }
})
</script>

<template>
  <el-popover
    placement="top-start"
    :width="400"
    trigger="click"
    :disabled="!enabled"
    @show="fetchTools"
  >
    <template #reference>
      <button
        class="toolbar-btn"
        :class="{ active: enabled }"
        title="工具选择"
      >
        <el-icon :size="18"><SetUp /></el-icon>
        <span v-if="enabled && selectedTools.length > 0" class="tool-badge">{{ selectedTools.length }}</span>
      </button>
    </template>

    <div class="tool-selector">
      <div class="tool-selector-header">
        <span class="tool-selector-title">工具选择</span>
        <div class="tool-selector-actions">
          <el-button
            :icon="Refresh"
            size="small"
            circle
            :loading="loading"
            @click="fetchTools"
          />
          <el-button
            :icon="Upload"
            size="small"
            circle
            type="primary"
            title="上传自定义工具"
            @click="showUploadDialog = true"
          />
        </div>
      </div>

      <div class="tool-categories">
        <button
          v-for="cat in categories"
          :key="cat"
          :class="['cat-btn', { active: activeCategory === cat }]"
          @click="activeCategory = cat"
        >
          {{ categoryLabels[cat] || cat }}
        </button>
      </div>

      <div class="tool-batch-actions">
        <el-button size="small" link type="primary" @click="selectAll">全选</el-button>
        <el-button size="small" link type="info" @click="clearAll">清空</el-button>
      </div>

      <div v-if="filteredTools.length === 0 && !loading" class="tool-empty">
        暂无可用工具
      </div>

      <div v-else class="tool-list">
        <div
          v-for="tool in filteredTools"
          :key="tool.name"
          :class="['tool-item', { selected: isSelected(tool.name) }]"
          @click="toggleTool(tool.name)"
        >
          <div class="tool-info">
            <span class="tool-name">{{ tool.name }}</span>
            <span v-if="tool.description" class="tool-desc">{{ tool.description }}</span>
          </div>
          <el-checkbox
            :model-value="isSelected(tool.name)"
            @change="toggleTool(tool.name)"
          />
        </div>
      </div>

      <div v-if="selectedTools.length > 0" class="tool-selector-footer">
        已选择 {{ selectedTools.length }} / {{ tools.length }} 个工具
      </div>
    </div>

    <ToolUploadDialog
      v-model="showUploadDialog"
      @success="handleUploadSuccess"
    />
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
  max-height: 450px;
  display: flex;
  flex-direction: column;
}

.tool-selector-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.tool-selector-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--foreground);
}

.tool-selector-actions {
  display: flex;
  gap: 4px;
}

.tool-categories {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 8px;
}

.cat-btn {
  padding: 3px 10px;
  border-radius: 12px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--muted-foreground);
  font-size: 12px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.cat-btn:hover {
  border-color: var(--sidebar-primary);
  color: var(--sidebar-primary);
}

.cat-btn.active {
  background: var(--sidebar-primary);
  color: white;
  border-color: var(--sidebar-primary);
}

.tool-batch-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.tool-empty {
  text-align: center;
  padding: 20px;
  font-size: 13px;
  color: var(--muted-foreground);
}

.tool-list {
  flex: 1;
  overflow-y: auto;
  max-height: 280px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tool-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  border-radius: 6px;
  border: 1px solid transparent;
  cursor: pointer;
  transition: all var(--transition-fast);
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
}

.tool-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--foreground);
  display: block;
}

.tool-desc {
  font-size: 11px;
  color: var(--muted-foreground);
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-top: 1px;
}

.tool-selector-footer {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
  font-size: 12px;
  color: var(--sidebar-primary);
  text-align: center;
}
</style>
