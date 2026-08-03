<script setup>
import { ref, watch, nextTick, computed } from 'vue'
import { Promotion, Search, Document, Close, VideoPause, SetUp, Loading, RefreshRight, Check, CircleClose } from '@element-plus/icons-vue'
import SlashCommandPanel from '@/components/common/SlashCommandPanel.vue'
import ToolSelector from '@/components/chat/ToolSelector.vue'

const props = defineProps({
  modelValue: {
    type: String,
    default: '',
  },
  disabled: {
    type: Boolean,
    default: false,
  },
  loading: {
    type: Boolean,
    default: false,
  },
  isStreaming: {
    type: Boolean,
    default: false,
  },
  useWebSearch: {
    type: Boolean,
    default: false,
  },
  selectedMcpServers: {
    type: Array,
    default: () => [],
  },
  selectedTools: {
    type: Array,
    default: () => [],
  },
  placeholder: {
    type: String,
    default: '',
  },
  maxLength: {
    type: Number,
    default: 10000,
  },
  showToolbar: {
    type: Boolean,
    default: true,
  },
  attachments: {
    type: Array,
    default: () => [],
  },
  currentMode: {
    type: String,
    default: 'agent',
  },
  useDeepThinking: {
    type: Boolean,
    default: false,
  },
  modelSupportsDeepThinking: {
    type: Boolean,
    default: false,
  },
  isUploading: {
    type: Boolean,
    default: false,
  },
  researchContextInfo: {
    type: Object,
    default: null,
  },
})

const emit = defineEmits({
  'update:modelValue': (value) => typeof value === 'string',
  send: () => true,
  keydown: (event) => event instanceof KeyboardEvent,
  attach: () => true,
  'remove-attachment': (index) => typeof index === 'number',
  'web-search': () => true,
  mcp: () => true,
  'update:selectedMcpServers': (val) => Array.isArray(val),
  'update:selectedTools': (val) => Array.isArray(val),
  'update:useDeepThinking': (val) => typeof val === 'boolean',
  'stop-streaming': () => true,
  'command-select': (cmd) => cmd instanceof Object,
  'retry-upload': (index) => typeof index === 'number',
  'cancel-upload': () => true,
  'dismiss-research-context': () => true,
})

const textareaRef = ref(null)
const isFocused = ref(false)
const fileInputRef = ref(null)
const showCommandPanel = ref(false)
const commandFilter = ref('')
const selectedCommandIndex = ref(0)
const slashCommandPanelRef = ref(null)

const computedPlaceholder = computed(() => {
  if (props.isStreaming) return '正在生成回复...'
  return props.placeholder || '输入您的问题，按 Enter 发送... 输入 / 查看命令'
})

const hasAttachments = computed(() => props.attachments.length > 0)
const canSend = computed(() => (props.modelValue.trim() || hasAttachments.value) && !props.disabled)
const charCount = computed(() => props.modelValue?.length || 0)

const uploadingCount = computed(() =>
  props.attachments.filter(a => a.status === 'uploading').length
)
const totalCount = computed(() => props.attachments.length)
const currentUploadingName = computed(() => {
  const uploading = props.attachments.find(a => a.status === 'uploading')
  return uploading?.name || ''
})

const overallProgress = computed(() => {
  if (props.attachments.length === 0) return 0
  const totalProgress = props.attachments.reduce((sum, a) => sum + (a.progress || 0), 0)
  return Math.round(totalProgress / props.attachments.length)
})

const isSlashCommand = computed(() => {
  return props.modelValue.startsWith('/') && !props.modelValue.includes('\n')
})

watch(isSlashCommand, (val) => {
  if (val) {
    showCommandPanel.value = true
    commandFilter.value = props.modelValue.slice(1)
  } else {
    showCommandPanel.value = false
    commandFilter.value = ''
  }
})

watch(() => props.modelValue, (val) => {
  if (val.startsWith('/') && !val.includes('\n')) {
    commandFilter.value = val.slice(1)
  }
})

const formatFileSize = (bytes) => {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

const getFileIcon = (type) => {
  const iconMap = {
    pdf: '📄', doc: '📝', docx: '📝', txt: '📃',
    png: '🖼️', jpg: '🖼️', jpeg: '🖼️', gif: '🖼️', webp: '🖼️', svg: '🖼️',
    xls: '📊', xlsx: '📊', csv: '📊',
    py: '🐍', js: '📜', ts: '📜', html: '🌐', css: '🎨',
    json: '📋', xml: '📋', md: '📝',
  }
  return iconMap[type] || '📎'
}

const handleKeyDown = (event) => {
  if (showCommandPanel.value) {
    if (event.key === 'Escape') {
      showCommandPanel.value = false
      event.preventDefault()
      return
    }
    if (event.key === 'Tab' || event.key === 'Enter') {
      event.preventDefault()
      const panel = slashCommandPanelRef.value
      if (panel?.filteredCommands?.length > 0) {
        const idx = Math.min(selectedCommandIndex.value, panel.filteredCommands.length - 1)
        if (idx >= 0) {
          handleCommandSelect(panel.filteredCommands[idx])
        }
      }
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      if (selectedCommandIndex.value > 0) {
        selectedCommandIndex.value--
      }
      return
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      const panel = slashCommandPanelRef.value
      const maxIdx = panel?.filteredCommands?.length ? panel.filteredCommands.length - 1 : 0
      if (selectedCommandIndex.value < maxIdx) {
        selectedCommandIndex.value++
      }
      return
    }
  }

  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    emit('send')
  }
  emit('keydown', event)
}

const handleFocus = () => {
  isFocused.value = true
}

const handleBlur = () => {
  isFocused.value = false
  setTimeout(() => {
    showCommandPanel.value = false
  }, 300)
}

const handleAttach = () => {
  fileInputRef.value?.click()
}

const handleFileChange = (event) => {
  const files = event.target.files
  if (files && files.length > 0) {
    emit('attach', files)
  }
  event.target.value = ''
}

const handleRemoveAttachment = (index) => {
  emit('remove-attachment', index)
}

const handleWebSearch = () => {
  emit('web-search')
}

const handleCommandSelect = (cmd) => {
  showCommandPanel.value = false
  emit('command-select', cmd)
}

const adjustTextareaHeight = async () => {
  await nextTick()
  const textarea = textareaRef.value
  if (textarea) {
    textarea.style.height = 'auto'
    textarea.style.height = Math.min(textarea.scrollHeight, 150) + 'px'
  }
}

watch(() => props.modelValue, adjustTextareaHeight)
</script>

<template>
  <div class="chat-footer">
    <div class="input-container">
      <Transition name="slide-down">
        <div v-if="researchContextInfo || hasAttachments" class="attachment-preview">
          <TransitionGroup name="list" tag="div" class="attachment-list">
            <div v-if="researchContextInfo" key="research" class="attachment-item research-attachment-item">
              <span class="attachment-icon">🔬</span>
              <div class="attachment-info">
                <div class="attachment-name-row">
                  <span class="attachment-name">深度研究</span>
                  <el-icon class="attachment-status status-success" :size="14"><Check /></el-icon>
                </div>
                <span v-if="researchContextInfo.query" class="attachment-size">{{ researchContextInfo.query }}</span>
              </div>
              <div class="attachment-actions">
                <button class="attachment-remove" @click="emit('dismiss-research-context')">
                  <el-icon :size="12"><Close /></el-icon>
                </button>
              </div>
            </div>
            <div
              v-for="(file, index) in attachments"
              :key="file.name + index"
              class="attachment-item"
              :class="{ 'is-uploading': file.status === 'uploading', 'is-failed': file.status === 'failed' }"
            >
              <span class="attachment-icon">{{ getFileIcon(file.fileType || file.name?.split('.').pop()) }}</span>
              <div class="attachment-info">
                <div class="attachment-name-row">
                  <span class="attachment-name">{{ file.name || file.originalName }}</span>
                  <span v-if="file.status === 'pending'" class="attachment-status status-pending">🕐</span>
                  <el-icon v-else-if="file.status === 'uploading'" class="attachment-status status-uploading is-loading" :size="14"><Loading /></el-icon>
                  <el-icon v-else-if="file.status === 'success'" class="attachment-status status-success" :size="14"><Check /></el-icon>
                  <el-icon v-else-if="file.status === 'failed'" class="attachment-status status-failed" :size="14"><CircleClose /></el-icon>
                </div>
                <span v-if="file.size" class="attachment-size">{{ formatFileSize(file.size) }}</span>
                <el-progress
                  v-if="file.status === 'uploading'"
                  :percentage="file.progress || 0"
                  :stroke-width="4"
                  :show-text="false"
                  class="attachment-progress"
                />
                <span v-if="file.status === 'failed' && file.error" class="attachment-error">{{ file.error }}</span>
              </div>
              <div class="attachment-actions">
                <el-button
                  v-if="file.status === 'failed'"
                  text
                  size="small"
                  type="primary"
                  class="attachment-retry"
                  @click="emit('retry-upload', index)"
                >
                  <el-icon :size="12"><RefreshRight /></el-icon>
                </el-button>
                <button
                  v-if="file.status !== 'uploading'"
                  class="attachment-remove"
                  @click="handleRemoveAttachment(index)"
                >
                  <el-icon :size="12"><Close /></el-icon>
                </button>
              </div>
            </div>
          </TransitionGroup>
        </div>
      </Transition>

      <div v-if="isUploading" class="upload-status-bar">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>正在上传文件 {{ uploadingCount }}/{{ totalCount }}：{{ currentUploadingName }}</span>
        <el-progress
          :percentage="overallProgress"
          :stroke-width="4"
          :show-text="false"
          style="flex: 1; max-width: 120px; margin: 0 8px;"
        />
        <el-button text size="small" type="danger" @click="emit('cancel-upload')">取消上传</el-button>
      </div>

      <div class="input-card" :class="{ 'is-focused': isFocused, 'is-streaming': isStreaming }">
        <Transition name="slide-down">
          <SlashCommandPanel
            ref="slashCommandPanelRef"
            v-if="showCommandPanel && isSlashCommand"
            :filter="commandFilter"
            :selected-index="selectedCommandIndex"
            @select="handleCommandSelect"
            class="command-panel-inline"
          />
        </Transition>
        <div class="input-content">
          <div class="input-toolbar">
            <button
              class="toolbar-btn"
              :class="{ active: hasAttachments }"
              :disabled="disabled || loading"
              title="添加附件"
              @click="handleAttach"
            >
              <el-icon :size="18"><Document /></el-icon>
            </button>
            <button
              v-if="currentMode === 'agent' || currentMode === 'deep-research'"
              class="toolbar-btn"
              :class="{ active: useWebSearch }"
              :disabled="disabled || loading"
              :title="useWebSearch ? '关闭网络搜索' : '开启网络搜索'"
              @click="handleWebSearch"
            >
              <el-icon :size="18"><Search /></el-icon>
            </button>
            <button
              v-if="modelSupportsDeepThinking && (currentMode === 'agent' || currentMode === 'deep-research')"
              class="toolbar-btn"
              :class="{ active: useDeepThinking }"
              :disabled="disabled || loading"
              :title="useDeepThinking ? '关闭深度思考' : '开启深度思考'"
              @click="emit('update:useDeepThinking', !useDeepThinking)"
            >
              🧠
            </button>
            <ToolSelector
              v-if="currentMode === 'agent' || currentMode === 'deep-research'"
              :model-value="selectedTools"
              @update:model-value="(val) => emit('update:selectedTools', val)"
              @update:selected-mcp-servers="(val) => emit('update:selectedMcpServers', val)"
            />
          </div>

          <textarea
            ref="textareaRef"
            :value="modelValue"
            :disabled="disabled"
            :placeholder="computedPlaceholder"
            :maxlength="maxLength"
            class="input-textarea"
            rows="1"
            @input="(e) => emit('update:modelValue', e.target.value)"
            @keydown="handleKeyDown"
            @focus="handleFocus"
            @blur="handleBlur"
          ></textarea>

          <div class="input-actions">
            <span v-if="charCount > 0" class="char-count">{{ charCount }} / {{ maxLength }}</span>
            <Transition name="fade" mode="out-in">
              <button
                v-if="isStreaming"
                key="stop"
                class="stop-btn"
                @click="emit('stop-streaming')"
              >
                <el-icon :size="14"><VideoPause /></el-icon>
                <span>停止</span>
              </button>
              <button
                v-else
                key="send"
                class="send-btn"
                :class="{ active: canSend }"
                :disabled="!canSend"
                @click="emit('send')"
              >
                <el-icon :size="18"><Promotion /></el-icon>
              </button>
            </Transition>
          </div>
        </div>
      </div>

      <input
        ref="fileInputRef"
        type="file"
        style="display: none;"
        multiple
        accept=".txt,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.png,.jpg,.jpeg,.gif,.webp,.svg,.csv,.json,.xml,.md,.py,.js,.ts,.html,.css"
        @change="handleFileChange"
      />

      <div class="input-hint">
        <span>Enter 发送 · Shift+Enter 换行</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-footer {
  background: var(--card);
  padding: 8px 24px 12px;
  border-top: 1px solid var(--border);
  flex-shrink: 0;
}

.input-container {
  max-width: 768px;
  margin: 0 auto;
}

.attachment-preview {
  margin-bottom: 8px;
}

.research-attachment-item {
  max-width: 280px;
}

.research-attachment-item .attachment-size {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 200px;
}

.upload-status-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  margin-bottom: 8px;
  border-radius: 10px;
  background: linear-gradient(135deg, color-mix(in srgb, var(--sidebar-primary) 10%, transparent), color-mix(in srgb, var(--sidebar-primary) 5%, transparent));
  border: 1px solid color-mix(in srgb, var(--sidebar-primary) 20%, transparent);
  font-size: 13px;
  color: var(--sidebar-primary);
  animation: status-bar-pulse 2s ease-in-out infinite;
}

@keyframes status-bar-pulse {
  0%, 100% { border-color: color-mix(in srgb, var(--sidebar-primary) 20%, transparent); }
  50% { border-color: color-mix(in srgb, var(--sidebar-primary) 40%, transparent); }
}

.upload-status-bar .is-loading {
  color: var(--sidebar-primary);
  animation: rotating 1.5s linear infinite;
}

@keyframes rotating {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.attachment-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.attachment-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: var(--radius);
  background: var(--accent);
  border: 1px solid var(--border);
  font-size: 13px;
  max-width: 200px;
  transition: all var(--transition-fast);
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

.attachment-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--foreground);
  font-size: 12px;
  line-height: 1.3;
}

.attachment-size {
  color: var(--muted-foreground);
  font-size: 11px;
}

.attachment-remove {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--muted-foreground);
  cursor: pointer;
  flex-shrink: 0;
  transition: all var(--transition-fast);
}

.attachment-remove:hover {
  background: color-mix(in srgb, var(--destructive) 10%, transparent);
  color: var(--destructive);
}

.upload-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--el-color-primary, #409eff);
  padding: 4px 0 6px;
}

.attachment-item.is-uploading {
  border-color: var(--el-color-primary, #409eff);
  background: color-mix(in srgb, var(--el-color-primary) 4%, transparent);
}

.attachment-item.is-failed {
  border-color: var(--destructive);
  background: color-mix(in srgb, var(--destructive) 4%, transparent);
}

.attachment-name-row {
  display: flex;
  align-items: center;
  gap: 4px;
}

.attachment-status {
  flex-shrink: 0;
  line-height: 1;
}

.status-pending {
  font-size: 12px;
  opacity: 0.7;
}

.status-uploading {
  color: var(--el-color-primary, #409eff);
}

.status-success {
  color: var(--el-color-success, #67c23a);
}

.status-failed {
  color: var(--destructive);
}

.attachment-progress {
  margin-top: 4px;
}

.attachment-error {
  font-size: 11px;
  color: var(--destructive);
  margin-top: 2px;
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 160px;
}

.attachment-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.attachment-retry {
  padding: 2px !important;
  min-height: 20px;
}

.input-card {
  display: flex;
  flex-direction: column;
  border-radius: 24px;
  border: 1.5px solid var(--border);
  background: var(--card);
  padding: 6px 8px;
  transition: all var(--transition-normal);
  gap: 4px;
  box-shadow: var(--shadow-sm);
  position: relative;
}

.command-panel-inline {
  position: absolute;
  bottom: 100%;
  left: 0;
  right: 0;
  margin-bottom: 8px;
  z-index: 100;
}

.input-card.is-focused {
  border-color: var(--sidebar-primary);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--sidebar-primary) 12%, transparent), var(--shadow-sm);
}

.input-card.is-streaming {
  border-color: var(--el-color-warning);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--el-color-warning) 12%, transparent);
}

.input-content {
  display: flex;
  align-items: flex-end;
  gap: 4px;
}

.input-toolbar {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
}

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

.toolbar-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.mcp-badge {
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

.input-textarea {
  flex: 1;
  border: none;
  outline: none;
  resize: none;
  background: transparent;
  font-size: 14px;
  line-height: 1.6;
  color: var(--foreground);
  padding: 8px 4px;
  min-height: 24px;
  max-height: 150px;
  overflow-y: auto;
  font-family: inherit;
}

.input-textarea::placeholder {
  color: var(--muted-foreground);
}

.input-textarea:disabled {
  opacity: 0.6;
}

.input-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.char-count {
  font-size: 12px;
  color: var(--muted-foreground);
  white-space: nowrap;
  transition: color var(--transition-fast);
}

.char-count.warning {
  color: var(--el-color-warning);
}

.char-count.danger {
  color: var(--destructive);
}

.send-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border: none;
  border-radius: 50%;
  background: var(--accent);
  color: var(--muted-foreground);
  cursor: not-allowed;
  transition: all var(--transition-normal);
}

.send-btn.active {
  background: var(--sidebar-primary);
  color: white;
  cursor: pointer;
  box-shadow: var(--shadow-primary);
}

.send-btn.active:hover {
  filter: brightness(1.1);
  transform: scale(1.05);
  box-shadow: 0 6px 16px color-mix(in srgb, var(--sidebar-primary) 35%, transparent);
}

.send-btn.active:active {
  transform: scale(0.95);
}

.stop-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 6px 14px;
  border: 1px solid color-mix(in srgb, var(--destructive) 30%, transparent);
  border-radius: 18px;
  background: color-mix(in srgb, var(--destructive) 8%, transparent);
  color: var(--destructive);
  font-size: 13px;
  cursor: pointer;
  transition: all var(--transition-fast);
  white-space: nowrap;
}

.stop-btn:hover {
  background: color-mix(in srgb, var(--destructive) 15%, transparent);
  border-color: color-mix(in srgb, var(--destructive) 50%, transparent);
}

.input-hint {
  text-align: center;
  padding: 6px 0 0;
  font-size: 12px;
  color: var(--muted-foreground);
  letter-spacing: 0.3px;
  opacity: 0.7;
}

.slide-down-enter-active,
.slide-down-leave-active {
  transition: all var(--transition-normal);
}

.slide-down-enter-from,
.slide-down-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

.list-enter-active,
.list-leave-active {
  transition: all var(--transition-normal);
}

.list-enter-from,
.list-leave-to {
  opacity: 0;
  transform: scale(0.9);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--transition-fast);
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 768px) {
  .chat-footer {
    padding: 8px 12px;
  }

  .input-hint {
    display: none;
  }
}

@media (max-width: 480px) {
  .chat-footer {
    padding: 6px 8px;
  }

  .input-card {
    border-radius: 20px;
    padding: 4px 6px;
  }

  .toolbar-btn {
    width: 30px;
    height: 30px;
  }

  .send-btn {
    width: 32px;
    height: 32px;
  }
}
</style>
