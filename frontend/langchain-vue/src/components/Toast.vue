<script setup>
defineOptions({
  name: 'ToastNotification'
})

import { reactive } from 'vue'
import { Success, Warning, Error, Info, Close } from '@element-plus/icons-vue'

defineProps({
  position: {
    type: String,
    default: 'top-right',
    validator: (value) => ['top-right', 'top-center', 'top-left', 'bottom-right', 'bottom-center', 'bottom-left'].includes(value)
  }
})

const toasts = reactive([])
let toastId = 0

const iconMap = {
  success: Success,
  warning: Warning,
  error: Error,
  info: Info,
}

const typeClass = {
  success: 'toast-success',
  warning: 'toast-warning',
  error: 'toast-error',
  info: 'toast-info',
}

function addToast(options) {
  const id = ++toastId
  const toast = {
    id,
    type: options.type || 'info',
    title: options.title || '',
    message: options.message || '',
    duration: options.duration ?? 3000,
    icon: iconMap[options.type] || Info,
    class: typeClass[options.type] || 'toast-info',
    closable: options.closable ?? true,
  }

  toasts.push(toast)

  if (toast.duration > 0) {
    setTimeout(() => {
      removeToast(id)
    }, toast.duration)
  }

  return id
}

function removeToast(id) {
  const index = toasts.findIndex(t => t.id === id)
  if (index !== -1) {
    toasts.splice(index, 1)
  }
}

function success(message, options = {}) {
  return addToast({ ...options, type: 'success', message })
}

function error(message, options = {}) {
  return addToast({ ...options, type: 'error', message })
}

function warning(message, options = {}) {
  return addToast({ ...options, type: 'warning', message })
}

function info(message, options = {}) {
  return addToast({ ...options, type: 'info', message })
}

function loading(message, options = {}) {
  return addToast({ ...options, type: 'info', message, duration: 0 })
}

defineExpose({
  success,
  error,
  warning,
  info,
  loading,
  remove: removeToast,
})
</script>

<template>
  <Teleport to="body">
    <div class="toast-container" :class="[`position-${position.replace('-', '-')}`]">
      <TransitionGroup name="toast">
        <div
          v-for="toast in toasts"
          :key="toast.id"
          class="toast-item"
          :class="toast.class"
        >
          <div class="toast-icon">
            <el-icon :size="20">
              <component :is="toast.icon" />
            </el-icon>
          </div>
          <div class="toast-content">
            <div v-if="toast.title" class="toast-title">{{ toast.title }}</div>
            <div class="toast-message">{{ toast.message }}</div>
          </div>
          <button
            v-if="toast.closable"
            class="toast-close"
            @click="removeToast(toast.id)"
          >
            <el-icon :size="16"><Close /></el-icon>
          </button>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<style scoped>
.toast-container {
  position: fixed;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 8px;
  pointer-events: none;
}

.position-top-right {
  top: 20px;
  right: 20px;
}

.position-top-center {
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
}

.position-top-left {
  top: 20px;
  left: 20px;
}

.position-bottom-right {
  bottom: 20px;
  right: 20px;
}

.position-bottom-center {
  bottom: 20px;
  left: 50%;
  transform: translateX(-50%);
}

.position-bottom-left {
  bottom: 20px;
  left: 20px;
}

.toast-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
  background: var(--el-bg-color);
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  min-width: 300px;
  max-width: 420px;
  pointer-events: auto;
  border-left: 4px solid;
}

.toast-success {
  border-left-color: var(--el-color-success);
}

.toast-success .toast-icon {
  color: var(--el-color-success);
}

.toast-warning {
  border-left-color: var(--el-color-warning);
}

.toast-warning .toast-icon {
  color: var(--el-color-warning);
}

.toast-error {
  border-left-color: var(--el-color-danger);
}

.toast-error .toast-icon {
  color: var(--el-color-danger);
}

.toast-info {
  border-left-color: var(--el-color-info);
}

.toast-info .toast-icon {
  color: var(--el-color-info);
}

.toast-icon {
  flex-shrink: 0;
  margin-top: 2px;
}

.toast-content {
  flex: 1;
  min-width: 0;
}

.toast-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--el-text-color-primary);
  margin-bottom: 4px;
}

.toast-message {
  font-size: 14px;
  color: var(--el-text-color-regular);
  line-height: 1.5;
  word-break: break-word;
}

.toast-close {
  flex-shrink: 0;
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px;
  color: var(--el-text-color-secondary);
  transition: color 0.2s;
}

.toast-close:hover {
  color: var(--el-text-color-primary);
}

.toast-enter-active,
.toast-leave-active {
  transition: all 0.3s ease;
}

.toast-enter-from {
  opacity: 0;
  transform: translateX(100%);
}

.toast-leave-to {
  opacity: 0;
  transform: translateX(100%);
}

@media (max-width: 768px) {
  .toast-container {
    left: 20px !important;
    right: 20px !important;
  }

  .toast-item {
    min-width: auto;
    max-width: none;
  }
}
</style>
