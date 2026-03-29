<script setup>
import { computed } from 'vue'

const props = defineProps({
  type: {
    type: String,
    default: 'spinner',
    validator: (v) => ['spinner', 'dots', 'pulse', 'bars'].includes(v)
  },
  size: {
    type: String,
    default: 'medium',
    validator: (v) => ['small', 'medium', 'large'].includes(v)
  },
  color: {
    type: String,
    default: 'primary'
  },
  text: {
    type: String,
    default: ''
  }
})

const sizeMap = {
  small: 16,
  medium: 24,
  large: 40
}

const loaderSize = computed(() => sizeMap[props.size])
</script>

<template>
  <div :class="['ai-loader', `ai-loader--${type}`, `ai-loader--${size}`]">
    <div v-if="type === 'spinner'" class="spinner" :style="{ width: `${loaderSize}px`, height: `${loaderSize}px` }">
      <svg viewBox="0 0 24 24" fill="none">
        <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" opacity="0.3" />
        <path d="M12 2C6.48 2 2 6.48 2 12" stroke="currentColor" stroke-width="3" stroke-linecap="round" />
      </svg>
    </div>

    <div v-else-if="type === 'dots'" class="dots">
      <span class="dot" :style="{ width: `${loaderSize / 3}px`, height: `${loaderSize / 3}px` }"></span>
      <span class="dot" :style="{ width: `${loaderSize / 3}px`, height: `${loaderSize / 3}px` }"></span>
      <span class="dot" :style="{ width: `${loaderSize / 3}px`, height: `${loaderSize / 3}px` }"></span>
    </div>

    <div v-else-if="type === 'pulse'" class="pulse" :style="{ width: `${loaderSize}px`, height: `${loaderSize}px` }"></div>

    <div v-else-if="type === 'bars'" class="bars">
      <span class="bar"></span>
      <span class="bar"></span>
      <span class="bar"></span>
      <span class="bar"></span>
    </div>

    <span v-if="text" class="ai-loader__text">{{ text }}</span>
  </div>
</template>

<style scoped>
.ai-loader {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--el-color-primary);
}

.ai-loader__text {
  font-size: 14px;
  color: var(--el-text-color-secondary);
}

.spinner svg {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.dots {
  display: flex;
  gap: 4px;
}

.dot {
  background-color: currentColor;
  border-radius: 50%;
  animation: dot-bounce 1.4s infinite ease-in-out both;
}

.dot:nth-child(1) { animation-delay: -0.32s; }
.dot:nth-child(2) { animation-delay: -0.16s; }

@keyframes dot-bounce {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}

.pulse {
  background-color: currentColor;
  border-radius: 50%;
  animation: pulse-grow 1s infinite ease-in-out;
}

@keyframes pulse-grow {
  0%, 100% { transform: scale(0.8); opacity: 0.5; }
  50% { transform: scale(1); opacity: 1; }
}

.bars {
  display: flex;
  gap: 3px;
  align-items: flex-end;
  height: 100%;
}

.bar {
  width: 4px;
  background-color: currentColor;
  animation: bar-stretch 1s infinite ease-in-out;
}

.bar:nth-child(1) { height: 40%; animation-delay: -0.4s; }
.bar:nth-child(2) { height: 70%; animation-delay: -0.3s; }
.bar:nth-child(3) { height: 50%; animation-delay: -0.2s; }
.bar:nth-child(4) { height: 80%; animation-delay: -0.1s; }

@keyframes bar-stretch {
  0%, 100% { transform: scaleY(0.5); }
  50% { transform: scaleY(1); }
}
</style>
