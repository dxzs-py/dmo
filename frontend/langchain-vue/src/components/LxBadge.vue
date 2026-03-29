<script setup>
import { computed, useSlots } from 'vue'

const props = defineProps({
  value: {
    type: [String, Number],
    default: ''
  },
  max: {
    type: Number,
    default: 99
  },
  isDot: {
    type: Boolean,
    default: false
  },
  hidden: {
    type: Boolean,
    default: false
  },
  type: {
    type: String,
    default: 'primary',
    validator: (v) => ['primary', 'success', 'warning', 'danger', 'info'].includes(v)
  }
})

const displayValue = computed(() => {
  if (props.isDot) return ''
  if (typeof props.value === 'number' && props.max) {
    return props.value > props.max ? `${props.max}+` : props.value
  }
  return props.value
})

const slots = useSlots()

const classes = computed(() => [
  'lx-badge',
  `lx-badge--${props.type}`,
  {
    'is-dot': props.isDot,
    'is-fixed': !!slots.default,
  }
])
</script>

<template>
  <div :class="['lx-badge-wrapper']">
    <slot />
    <sup
      v-show="!hidden"
      :class="classes"
    >
      {{ displayValue }}
    </sup>
  </div>
</template>

<style scoped>
.lx-badge-wrapper {
  position: relative;
  display: inline-block;
}

.lx-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 6px;
  font-size: 12px;
  font-weight: 500;
  border-radius: 10px;
  line-height: 1;
  white-space: nowrap;
}

.lx-badge.is-dot {
  width: 8px;
  height: 8px;
  min-width: 8px;
  padding: 0;
  border-radius: 50%;
}

.lx-badge--primary {
  background-color: var(--el-color-primary);
  color: #fff;
}

.lx-badge--success {
  background-color: var(--el-color-success);
  color: #fff;
}

.lx-badge--warning {
  background-color: var(--el-color-warning);
  color: #fff;
}

.lx-badge--danger {
  background-color: var(--el-color-danger);
  color: #fff;
}

.lx-badge--info {
  background-color: var(--el-color-info);
  color: #fff;
}

sup.lx-badge.is-fixed {
  position: absolute;
  top: 0;
  right: 0;
  transform: translate(50%, -50%);
}
</style>
