<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { ArrowRight, Document, Search, Link } from '@element-plus/icons-vue'

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
  useWebSearch: {
    type: Boolean,
    default: false,
  },
  useMicrophone: {
    type: Boolean,
    default: false,
  },
  placeholder: {
    type: String,
    default: '输入您的问题... (Enter 发送，Shift+Enter 换行)',
  },
})

const emit = defineEmits(['update:modelValue', 'send', 'keydown', 'attach', 'voice', 'webSearch', 'microphone'])

const textareaRef = ref(null)
const isFocused = ref(false)

const handleKeyDown = (event) => {
  emit('keydown', event)
}

const handleFocus = () => {
  isFocused.value = true
}

const handleBlur = () => {
  isFocused.value = false
}

const handleAttach = () => {
  emit('attach')
}

const handleVoice = () => {
  emit('voice')
}

const handleWebSearch = () => {
  emit('webSearch')
}

const handleMicrophone = () => {
  emit('microphone')
}

// 自动调整文本框高度
const adjustTextareaHeight = async () => {
  await nextTick()
  if (textareaRef.value) {
    const textarea = textareaRef.value.$el?.querySelector('textarea') || textareaRef.value.textarea
    if (textarea) {
      textarea.style.height = 'auto'
      textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px'
    }
  }
}

watch(() => props.modelValue, adjustTextareaHeight)
</script>

<template>
  <div class="chat-footer">
    <div class="input-area">
      <div class="input-wrapper" :class="{ 'is-focused': isFocused }">
        <div class="input-tools">
          <el-button
            circle
            :icon="Document"
            @click="handleAttach"
            :disabled="disabled || loading"
            size="small"
            title="添加附件"
          />
          <el-button
            circle
            :icon="Search"
            @click="handleWebSearch"
            :disabled="disabled || loading"
            size="small"
            :type="useWebSearch ? 'primary' : 'default'"
            :title="useWebSearch ? '关闭网络搜索' : '开启网络搜索'"
          />
          <el-button
            circle
            :icon="Link"
            @click="handleMicrophone"
            :disabled="disabled || loading"
            size="small"
            :type="useMicrophone ? 'primary' : 'default'"
            :title="useMicrophone ? '关闭语音输入' : '开启语音输入'"
          />
        </div>
        <el-input
          ref="textareaRef"
          :model-value="modelValue"
          @update:model-value="(val) => emit('update:modelValue', val)"
          type="textarea"
          :rows="1"
          :disabled="disabled"
          :placeholder="placeholder"
          @keydown="handleKeyDown"
          @focus="handleFocus"
          @blur="handleBlur"
          resize="none"
          class="auto-resize"
        />
      </div>
      <div class="action-buttons">
        <el-button
          type="primary"
          @click="emit('send')"
          :loading="loading"
          :disabled="!modelValue.trim() || disabled"
          :icon="ArrowRight"
          round
          size="small"
        >
          发送
        </el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-footer {
  background: var(--el-bg-color);
  border-top: 1px solid var(--el-border-color);
  padding: 16px 24px;
  transition: all 0.3s ease;
}

.input-area {
  display: flex;
  gap: 12px;
  align-items: flex-end;
  max-width: 900px;
  margin: 0 auto;
}

.input-wrapper {
  flex: 1;
  position: relative;
  border-radius: 12px;
  border: 1px solid var(--el-border-color);
  transition: all 0.3s ease;
  background-color: var(--el-bg-color);
}

.input-wrapper.is-focused {
  border-color: var(--el-color-primary);
  box-shadow: 0 0 0 2px rgba(64, 158, 255, 0.2);
}

.input-tools {
  position: absolute;
  top: 8px;
  left: 8px;
  display: flex;
  gap: 4px;
  z-index: 1;
}

.auto-resize :deep(.el-textarea) {
  height: auto;
  min-height: 40px;
  max-height: 120px;
}

.auto-resize :deep(.el-textarea__inner) {
  border: none;
  border-radius: 12px;
  padding: 12px 16px 12px 60px;
  font-size: 14px;
  line-height: 1.6;
  resize: none;
  overflow-y: auto;
  min-height: 40px;
  max-height: 120px;
}

.auto-resize :deep(.el-textarea__inner:focus) {
  box-shadow: none;
}

.action-buttons {
  display: flex;
  gap: 8px;
}

.action-buttons :deep(.el-button) {
  transition: all 0.3s ease;
}

.action-buttons :deep(.el-button:hover) {
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}

.action-buttons :deep(.el-button:disabled) {
  transform: none;
  box-shadow: none;
}
</style>
