<script setup>
/**
 * 消息附件展示区。
 *
 * 包含两类附件：
 * - 深度研究上下文（researchContext）：以🔬图标标识，展示研究查询
 * - 文件附件（attachments）：根据文件类型显示对应图标与大小
 */
import { Check } from '@element-plus/icons-vue'
import { formatFileSize } from '../../utils/format'

defineProps({
  researchContext: {
    type: Object,
    default: null,
  },
  attachments: {
    type: Array,
    default: () => [],
  },
})

defineEmits({})

/** 根据文件扩展名返回对应 emoji 图标 */
function getFileIcon(type) {
  const iconMap = {
    pdf: '📄', doc: '📝', docx: '📝', txt: '📃',
    png: '🖼️', jpg: '🖼️', jpeg: '🖼️', gif: '🖼️', webp: '🖼️', svg: '🖼️',
    xls: '📊', xlsx: '📊', csv: '📊',
    py: '🐍', js: '📜', ts: '📜', html: '🌐', css: '🎨',
    json: '📋', xml: '📋', md: '📝',
  }
  return iconMap[type] || '📎'
}
</script>

<template>
  <div v-if="researchContext" class="attachment-list">
    <div class="attachment-item research-attachment-item">
      <span class="attachment-icon">🔬</span>
      <div class="attachment-info">
        <div class="attachment-name-row">
          <span class="attachment-name">深度研究</span>
          <el-icon class="attachment-status status-success" :size="14"><Check /></el-icon>
        </div>
        <span v-if="researchContext.query" class="attachment-size research-query">{{ researchContext.query }}</span>
      </div>
    </div>
  </div>

  <div v-if="attachments.length > 0" class="attachment-list">
    <div v-for="(att, idx) in attachments" :key="idx" class="attachment-item">
      <span class="attachment-icon">{{ getFileIcon(att.fileType) }}</span>
      <div class="attachment-info">
        <div class="attachment-name-row">
          <span class="attachment-name">{{ att.name }}</span>
        </div>
        <span v-if="att.size" class="attachment-size">{{ formatFileSize(att.size) }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.attachment-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}

.attachment-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 6px 10px;
  font-size: 13px;
  color: var(--foreground);
  transition: all var(--transition-fast);
  max-width: 200px;
}

.attachment-item:hover {
  border-color: var(--sidebar-primary);
  box-shadow: var(--shadow-sm);
}

.attachment-icon {
  font-size: 16px;
  flex-shrink: 0;
}

.attachment-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.attachment-name-row {
  display: flex;
  align-items: center;
  gap: 4px;
}

.attachment-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--foreground);
  font-size: 12px;
  line-height: 1.3;
  max-width: 120px;
}

.attachment-size {
  color: var(--muted-foreground);
  font-size: 11px;
  flex-shrink: 0;
}

.research-attachment-item {
  max-width: 280px;
}

.research-query {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 200px;
}

.attachment-status {
  flex-shrink: 0;
  line-height: 1;
}

.status-success {
  color: var(--el-color-success, #67c23a);
}
</style>
