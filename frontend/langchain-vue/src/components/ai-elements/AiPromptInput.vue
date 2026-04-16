<script setup>
import { ref, computed } from 'vue'
import { Promotion, Refresh } from '@element-plus/icons-vue'

const props = defineProps({
  modelValue: {
    type: String,
    default: ''
  },
  placeholder: {
    type: String,
    default: '输入消息... (Enter 发送，Shift+Enter 换行)'
  },
  disabled: {
    type: Boolean,
    default: false
  },
  loading: {
    type: Boolean,
    default: false
  },
  maxLength: {
    type: Number,
    default: 4000
  },
  showCharCount: {
    type: Boolean,
    default: false
  },
  submitOnEnter: {
    type: Boolean,
    default: true
  }
})

const emit = defineEmits(['update:modelValue', 'submit', 'stop', 'regenerate'])

const textareaRef = ref(null)

const charCount = computed(() => props.modelValue?.length || 0)

const inputValue = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
})

const handleKeyDown = (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    if (props.submitOnEnter) {
      event.preventDefault()
      handleSubmit()
    }
  }
}

const handleSubmit = () => {
  if (!props.modelValue?.trim() || props.disabled || props.loading) {
    return
  }
  emit('submit', props.modelValue)
}

const handleStop = () => {
  emit('stop')
}

const handleRegenerate = () => {
  emit('regenerate')
}

defineExpose({
  focus: () => {
    textareaRef.value?.focus()
  }
})
</script>

<template>
  <div class="ai-prompt-input">
    <div class="ai-prompt-input__container">
      <div class="ai-prompt-input__main">
        <textarea
          ref="textareaRef"
          v-model="inputValue"
          class="ai-prompt-input__textarea"
          :placeholder="placeholder"
          :disabled="disabled"
          :maxlength="maxLength"
          rows="2"
          @keydown="handleKeyDown"
        />
      </div>

      <div class="ai-prompt-input__actions">
        <div class="ai-prompt-input__left">
          <slot name="left-actions" />
        </div>

        <div class="ai-prompt-input__right">
          <span v-if="showCharCount" class="char-count">
            {{ charCount }} / {{ maxLength }}
          </span>

          <el-button
            v-if="loading"
            class="action-btn stop-btn"
            title="停止生成"
            @click="handleStop"
          >
            <el-icon :size="18"><Refresh /></el-icon>
          </el-button>

          <el-button
            v-else
            class="action-btn"
            :disabled="disabled || !modelValue"
            title="重新生成"
            @click="handleRegenerate"
          >
            <el-icon :size="18"><Refresh /></el-icon>
          </el-button>

          <el-button
            class="action-btn submit-btn"
            type="primary"
            :disabled="disabled || loading || !modelValue?.trim()"
            @click="handleSubmit"
          >
            <el-icon :size="18"><Promotion /></el-icon>
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ai-prompt-input {
  width: 100%;
}

.ai-prompt-input__container {
  background-color: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color);
  border-radius: 12px;
  padding: 12px 16px;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.ai-prompt-input__container:focus-within {
  border-color: var(--el-color-primary);
  box-shadow: 0 0 0 2px var(--el-color-primary-light-8);
}

.ai-prompt-input__main {
  position: relative;
}

.ai-prompt-input__textarea {
  width: 100%;
  min-height: 60px;
  max-height: 200px;
  border: none;
  background: transparent;
  resize: none;
  font-size: 14px;
  line-height: 1.6;
  color: var(--el-text-color-regular);
  outline: none;
}

.ai-prompt-input__textarea::placeholder {
  color: var(--el-text-color-placeholder);
}

.ai-prompt-input__textarea:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.ai-prompt-input__actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.ai-prompt-input__left {
  display: flex;
  gap: 8px;
}

.ai-prompt-input__right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.char-count {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
  margin-right: 8px;
}

.action-btn {
  border: none;
  background: transparent;
  padding: 8px;
  cursor: pointer;
  border-radius: 8px;
  transition: background-color 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
}

.action-btn:hover:not(:disabled) {
  background-color: var(--el-fill-color);
}

.action-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.submit-btn {
  background-color: var(--el-color-primary) !important;
  color: #fff !important;
}

.submit-btn:hover:not(:disabled) {
  background-color: var(--el-color-primary-light-3) !important;
}

.stop-btn {
  background-color: var(--el-color-warning) !important;
  color: #fff !important;
}
</style>
