<script setup>
import { computed } from 'vue'

const props = defineProps({
  percentage: {
    type: Number,
    default: 0,
    validator: (v) => v >= 0 && v <= 100
  },
  type: {
    type: String,
    default: 'line',
    validator: (v) => ['line', 'circle', 'dashboard'].includes(v)
  },
  strokeWidth: {
    type: Number,
    default: 6
  },
  color: {
    type: [String, Function, Array],
    default: ''
  },
  width: {
    type: Number,
    default: 126
  },
  showText: {
    type: Boolean,
    default: true
  },
  status: {
    type: String,
    default: '',
    validator: (v) => ['', 'success', 'warning', 'exception'].includes(v)
  },
  textInside: {
    type: Boolean,
    default: false
  }
})

const barStyle = computed(() => {
  const color = getColor()
  return {
    width: `${props.percentage}%`,
    backgroundColor: color
  }
})

const getColor = () => {
  if (typeof props.color === 'function') {
    return props.color(props.percentage)
  }
  if (typeof props.color === 'string') {
    return props.color
  }
  if (Array.isArray(props.color)) {
    const percentage = props.percentage
    for (const item of props.color) {
      if (percentage <= item.percentage) {
        return item.color
      }
    }
  }

  const statusColorMap = {
    success: 'var(--el-color-success)',
    warning: 'var(--el-color-warning)',
    exception: 'var(--el-color-danger)'
  }

  if (props.status && statusColorMap[props.status]) {
    return statusColorMap[props.status]
  }

  if (props.percentage < 30) {
    return 'var(--el-color-danger)'
  }
  if (props.percentage < 70) {
    return 'var(--el-color-warning)'
  }
  return 'var(--el-color-success)'
}

const relativeStrokeWidth = computed(() => {
  return (props.strokeWidth / props.width * 100).toFixed(1)
})

const radius = computed(() => {
  if (props.type === 'circle' || props.type === 'dashboard') {
    return parseInt((50 - parseFloat(relativeStrokeWidth.value) / 2).toFixed(1))
  }
  return 0
})

const perimeter = computed(() => {
  return 2 * Math.PI * radius.value
})

const strokeDashoffset = computed(() => {
  const percent = props.percentage / 100
  return perimeter.value * (1 - percent)
})

const statusIcon = computed(() => {
  const iconMap = {
    success: 'Check',
    warning: 'Warning',
    exception: 'Close'
  }
  return iconMap[props.status] || ''
})
</script>

<template>
  <div :class="['lx-progress', `lx-progress--${type}`]">
    <div v-if="type === 'line'" class="lx-progress__bar">
      <div
        class="lx-progress__bar-inner"
        :style="barStyle"
        :class="{ 'is-text-inside': textInside }"
      >
        <span v-if="textInside && showText" class="lx-progress__text">
          {{ percentage }}%
        </span>
      </div>
      <span v-if="!textInside && showText" class="lx-progress__text">
        <slot>
          {{ percentage }}%
          <el-icon v-if="status" :size="14"><component :is="statusIcon" /></el-icon>
        </slot>
      </span>
    </div>

    <div v-else class="lx-progress__circle">
      <svg :width="width" :height="width" :viewPort="`0 0 ${width} ${width}`">
        <circle
          class="lx-progress__circle-track"
          :cx="width / 2"
          :cy="width / 2"
          :r="radius"
          fill="none"
          :stroke-width="strokeWidth"
        />
        <circle
          class="lx-progress__circle-bar"
          :cx="width / 2"
          :cy="width / 2"
          :r="radius"
          fill="none"
          :stroke-width="strokeWidth"
          :stroke-dasharray="`${perimeter} ${perimeter}`"
          :stroke-dashoffset="strokeDashoffset"
          :class="`is-${status}`"
        />
      </svg>
      <div v-if="showText" class="lx-progress__text">
        <slot>
          {{ percentage }}%
          <el-icon v-if="status" :size="14"><component :is="statusIcon" /></el-icon>
        </slot>
      </div>
    </div>
  </div>
</template>

<style scoped>
.lx-progress {
  position: relative;
  font-size: 14px;
  color: var(--el-text-color-regular);
}

.lx-progress__bar {
  display: flex;
  align-items: center;
  width: 100%;
}

.lx-progress__bar-inner {
  position: relative;
  flex: 1;
  height: 6px;
  background-color: var(--el-color-primary);
  border-radius: 100px;
  transition: width 0.6s ease;
}

.lx-progress__bar-inner.is-text-inside {
  height: 100%;
}

.lx-progress__text {
  margin-left: 10px;
  min-width: 40px;
  text-align: right;
  font-size: 14px;
  color: var(--el-text-color-regular);
}

.lx-progress__circle {
  position: relative;
  display: inline-block;
}

.lx-progress__circle-track {
  stroke: var(--el-border-color-light);
}

.lx-progress__circle-bar {
  stroke: var(--el-color-primary);
  stroke-linecap: round;
  transition: stroke-dashoffset 0.6s ease;
}

.lx-progress__circle-bar.is-success {
  stroke: var(--el-color-success);
}

.lx-progress__circle-bar.is-warning {
  stroke: var(--el-color-warning);
}

.lx-progress__circle-bar.is-exception {
  stroke: var(--el-color-danger);
}
</style>
