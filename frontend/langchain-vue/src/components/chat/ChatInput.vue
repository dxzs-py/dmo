<script setup>
import { ref, watch, nextTick } from 'vue'
import { ArrowRight, Document, Search, Link } from '@element-plus/icons-vue'

/**
 * ChatInput组件 - 聊天输入框
 * @component
 * @description 提供消息输入、附件上传、网络搜索、语音输入等功能
 */
const props = defineProps({
  /**
   * 输入框绑定值（v-model）
   * @type {string}
   */
  modelValue: {
    type: String,
    default: '',
  },
  /**
   * 是否禁用
   * @type {boolean}
   */
  disabled: {
    type: Boolean,
    default: false,
  },
  /**
   * 发送按钮加载状态
   * @type {boolean}
   */
  loading: {
    type: Boolean,
    default: false,
  },
  /**
   * 是否启用网络搜索
   * @type {boolean}
   */
  useWebSearch: {
    type: Boolean,
    default: false,
  },
  /**
   * 是否启用语音输入
   * @type {boolean}
   */
  useMicrophone: {
    type: Boolean,
    default: false,
  },
  /**
   * 占位提示文本
   * @type {string}
   */
  placeholder: {
    type: String,
    default: '输入您的问题... (Enter 发送，Shift+Enter 换行)',
  },
  /**
   * 最大输入字符数
   * @type {number}
   */
  maxLength: {
    type: Number,
    default: 10000,
    validator: (value) => value > 0 && value <= 50000,
  },
  /**
   * 是否显示工具栏（附件、搜索等）
   * @type {boolean}
   */
  showToolbar: {
    type: Boolean,
    default: true,
  },
})

const emit = defineEmits({
  /**
   * 更新modelValue事件（v-model）
   * @param {string} value - 新值
   */
  'update:modelValue': (value) => typeof value === 'string',
  /**
   * 发送消息事件
   */
  send: () => true,
  /**
   * 键盘按下事件
   * @param {KeyboardEvent} event - 键盘事件对象
   */
  keydown: (event) => event instanceof KeyboardEvent,
  /**
   * 点击附件按钮事件
   */
  attach: () => true,
  /**
   * 点击语音按钮事件
   */
  voice: () => true,
  /**
   * 切换网络搜索状态事件
   */
  webSearch: () => true,
  /**
   * 切换麦克风状态事件
   */
  microphone: () => true,
})

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

const handleWebSearch = () => {
  emit('webSearch')
}

const handleMicrophone = () => {
  emit('microphone')
}

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
            :disabled="disabled || loading"
            size="small"
            title="添加附件"
            @click="handleAttach"
          />
          <el-button
            circle
            :icon="Search"
            :disabled="disabled || loading"
            size="small"
            :type="useWebSearch ? 'primary' : 'default'"
            :title="useWebSearch ? '关闭网络搜索' : '开启网络搜索'"
            @click="handleWebSearch"
          />
          <el-button
            circle
            :icon="Link"
            :disabled="disabled || loading"
            size="small"
            :type="useMicrophone ? 'primary' : 'default'"
            :title="useMicrophone ? '关闭语音输入' : '开启语音输入'"
            @click="handleMicrophone"
          />
        </div>
        <el-input
          ref="textareaRef"
          :model-value="modelValue"
          type="textarea"
          :rows="1"
          :disabled="disabled"
          :placeholder="placeholder"
          resize="none"
          class="auto-resize"
          @update:model-value="(val) => emit('update:modelValue', val)"
          @keydown="handleKeyDown"
          @focus="handleFocus"
          @blur="handleBlur"
        />
      </div>
      <div class="action-buttons">
        <el-button
          type="primary"
          :loading="loading"
          :disabled="!modelValue.trim() || disabled"
          :icon="ArrowRight"
          round
          size="small"
          @click="emit('send')"
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
