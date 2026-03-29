<script setup>
import { computed } from 'vue'
import { InfoFilled, SuccessFilled, WarningFilled, CircleCloseFilled } from '@element-plus/icons-vue'

const props = defineProps({
  title: {
    type: String,
    default: ''
  },
  description: {
    type: String,
    default: ''
  },
  type: {
    type: String,
    default: 'info',
    validator: (v) => ['success', 'warning', 'info', 'error'].includes(v)
  },
  closable: {
    type: Boolean,
    default: true
  },
  closeText: {
    type: String,
    default: ''
  },
  showIcon: {
    type: Boolean,
    default: true
  },
  center: {
    type: Boolean,
    default: false
  },
  effect: {
    type: String,
    default: 'light',
    validator: (v) => ['light', 'dark'].includes(v)
  }
})

const emit = defineEmits(['close'])

const iconMap = {
  success: SuccessFilled,
  warning: WarningFilled,
  info: InfoFilled,
  error: CircleCloseFilled
}

const typeClass = computed(() => `lx-alert--${props.type}`)

const handleClose = () => {
  emit('close')
}
</script>

<template>
  <div
    :class="[
      'lx-alert',
      typeClass,
      `is-${effect}`,
      { 'has-description': description, 'is-center': center }
    ]"
    role="alert"
  >
    <el-icon v-if="showIcon" class="lx-alert__icon">
      <component :is="iconMap[type]" />
    </el-icon>
    <div class="lx-alert__content">
      <span v-if="title || $slots.title" class="lx-alert__title">
        <slot name="title">{{ title }}</slot>
      </span>
      <p v-if="description" class="lx-alert__description">
        <slot>{{ description }}</slot>
      </p>
      <slot />
    </div>
    <button
      v-if="closable || closeText"
      type="button"
      class="lx-alert__close"
      @click="handleClose"
    >
      <span v-if="closeText">{{ closeText }}</span>
      <el-icon v-else :size="14"><Close /></el-icon>
    </button>
  </div>
</template>

<style scoped>
.lx-alert {
  position: relative;
  padding: 8px 16px;
  border-radius: 4px;
  display: flex;
  align-items: flex-start;
  transition: opacity 0.3s;
}

.lx-alert.has-description {
  padding: 12px 16px;
}

.lx-alert.is-center {
  justify-content: center;
  text-align: center;
}

.lx-alert__icon {
  font-size: 16px;
  margin-right: 8px;
  flex-shrink: 0;
}

.has-description .lx-alert__icon {
  font-size: 20px;
}

.lx-alert__content {
  flex: 1;
  min-width: 0;
}

.lx-alert__title {
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
  color: var(--el-text-color-primary);
}

.has-description .lx-alert__title {
  font-size: 16px;
}

.lx-alert__description {
  font-size: 14px;
  margin-top: 4px;
  color: var(--el-text-color-regular);
  line-height: 1.6;
}

.lx-alert__close {
  position: absolute;
  top: 50%;
  right: 16px;
  transform: translateY(-50%);
  background: none;
  border: none;
  cursor: pointer;
  color: var(--el-text-color-secondary);
  font-size: 14px;
  padding: 4px;
}

.lx-alert__close:hover {
  color: var(--el-text-color-primary);
}

.lx-alert--success {
  background-color: var(--el-color-success-light-9);
  border: 1px solid var(--el-color-success-light-7);
}

.lx-alert--success .lx-alert__icon {
  color: var(--el-color-success);
}

.lx-alert--warning {
  background-color: var(--el-color-warning-light-9);
  border: 1px solid var(--el-color-warning-light-7);
}

.lx-alert--warning .lx-alert__icon {
  color: var(--el-color-warning);
}

.lx-alert--info {
  background-color: var(--el-color-info-light-9);
  border: 1px solid var(--el-color-info-light-7);
}

.lx-alert--info .lx-alert__icon {
  color: var(--el-color-info);
}

.lx-alert--error {
  background-color: var(--el-color-danger-light-9);
  border: 1px solid var(--el-color-danger-light-7);
}

.lx-alert--error .lx-alert__icon {
  color: var(--el-color-danger);
}

.is-dark.lx-alert--success {
  background-color: var(--el-color-success);
  border-color: var(--el-color-success);
  color: #fff;
}

.is-dark .lx-alert__icon {
  color: #fff;
}

.is-dark .lx-alert__title,
.is-dark .lx-alert__description {
  color: #fff;
}

.is-dark .lx-alert__close {
  color: #fff;
}

.is-dark .lx-alert__close:hover {
  color: rgba(255, 255, 255, 0.8);
}
</style>
