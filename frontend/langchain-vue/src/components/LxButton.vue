<script setup>
import { computed } from 'vue'
import { Loading } from '@element-plus/icons-vue'

const props = defineProps({
  type: {
    type: String,
    default: 'default',
    validator: (v) => ['default', 'primary', 'success', 'warning', 'danger', 'info', 'text'].includes(v)
  },
  size: {
    type: String,
    default: 'medium',
    validator: (v) => ['large', 'medium', 'small', 'mini'].includes(v)
  },
  plain: {
    type: Boolean,
    default: false
  },
  round: {
    type: Boolean,
    default: false
  },
  circle: {
    type: Boolean,
    default: false
  },
  loading: {
    type: Boolean,
    default: false
  },
  disabled: {
    type: Boolean,
    default: false
  },
  icon: {
    type: [String, Object],
    default: null
  },
  iconPosition: {
    type: String,
    default: 'left',
    validator: (v) => ['left', 'right'].includes(v)
  }
})

const emit = defineEmits(['click'])

const classes = computed(() => [
  'lx-button',
  `lx-button--${props.type}`,
  `lx-button--${props.size}`,
  {
    'is-plain': props.plain,
    'is-round': props.round,
    'is-circle': props.circle,
    'is-loading': props.loading,
    'is-disabled': props.disabled,
  }
])

const handleClick = (event) => {
  if (!props.loading && !props.disabled) {
    emit('click', event)
  }
}
</script>

<template>
  <button
    :class="classes"
    :disabled="disabled || loading"
    @click="handleClick"
  >
    <el-icon v-if="loading" class="is-loading">
      <Loading />
    </el-icon>
    <el-icon v-else-if="icon && iconPosition === 'left'" class="lx-button__icon">
      <component :is="icon" />
    </el-icon>
    <span v-if="$slots.default" class="lx-button__content">
      <slot />
    </span>
    <el-icon v-if="icon && iconPosition === 'right' && !loading" class="lx-button__icon">
      <component :is="icon" />
    </el-icon>
  </button>
</template>

<style scoped>
.lx-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 8px 16px;
  font-size: 14px;
  border-radius: 4px;
  border: 1px solid var(--el-border-color);
  background-color: var(--el-bg-color);
  color: var(--el-text-color-regular);
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
  outline: none;
  user-select: none;
}

.lx-button:hover:not(.is-disabled):not(.is-loading) {
  color: var(--el-color-primary);
  border-color: var(--el-color-primary-light-7);
  background-color: var(--el-color-primary-light-9);
}

.lx-button:active:not(.is-disabled):not(.is-loading) {
  color: var(--el-color-primary-light-3);
  border-color: var(--el-color-primary-light-3);
}

.lx-button.is-disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.lx-button.is-loading {
  cursor: wait;
}

.lx-button--primary {
  color: #fff;
  background-color: var(--el-color-primary);
  border-color: var(--el-color-primary);
}

.lx-button--primary:hover:not(.is-disabled):not(.is-loading) {
  background-color: var(--el-color-primary-light-3);
  border-color: var(--el-color-primary-light-3);
}

.lx-button--success {
  color: #fff;
  background-color: var(--el-color-success);
  border-color: var(--el-color-success);
}

.lx-button--success:hover:not(.is-disabled):not(.is-loading) {
  background-color: var(--el-color-success-light-3);
  border-color: var(--el-color-success-light-3);
}

.lx-button--warning {
  color: #fff;
  background-color: var(--el-color-warning);
  border-color: var(--el-color-warning);
}

.lx-button--warning:hover:not(.is-disabled):not(.is-loading) {
  background-color: var(--el-color-warning-light-3);
  border-color: var(--el-color-warning-light-3);
}

.lx-button--danger {
  color: #fff;
  background-color: var(--el-color-danger);
  border-color: var(--el-color-danger);
}

.lx-button--danger:hover:not(.is-disabled):not(.is-loading) {
  background-color: var(--el-color-danger-light-3);
  border-color: var(--el-color-danger-light-3);
}

.lx-button--info {
  color: #fff;
  background-color: var(--el-color-info);
  border-color: var(--el-color-info);
}

.lx-button--info:hover:not(.is-disabled):not(.is-loading) {
  background-color: var(--el-color-info-light-3);
  border-color: var(--el-color-info-light-3);
}

.lx-button--text {
  color: var(--el-color-primary);
  background-color: transparent;
  border-color: transparent;
  padding: 8px 0;
}

.lx-button--text:hover:not(.is-disabled):not(.is-loading) {
  color: var(--el-color-primary-light-3);
  border-color: transparent;
  background-color: transparent;
}

.lx-button--large {
  padding: 12px 20px;
  font-size: 16px;
}

.lx-button--small {
  padding: 6px 12px;
  font-size: 12px;
}

.lx-button--mini {
  padding: 4px 8px;
  font-size: 12px;
}

.lx-button.is-round {
  border-radius: 20px;
}

.lx-button.is-circle {
  border-radius: 50%;
  padding: 8px;
}

.lx-button.is-circle.lx-button--large {
  padding: 12px;
}

.lx-button.is-circle.lx-button--small {
  padding: 6px;
}

.lx-button.is-circle.lx-button--mini {
  padding: 4px;
}

.lx-button__icon {
  margin-right: 4px;
}

.lx-button__content {
  display: inline-flex;
  align-items: center;
}
</style>
