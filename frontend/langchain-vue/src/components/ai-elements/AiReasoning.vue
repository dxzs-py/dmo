<template>
  <div class="ai-reasoning" :class="[className, { 'is-streaming': isStreaming }]">
    <div class="reasoning-header" @click="toggleOpen">
      <div class="reasoning-header-left">
        <div class="reasoning-indicator">
          <div v-if="isStreaming" class="thinking-dot"></div>
          <svg v-else class="reasoning-check" viewBox="0 0 16 16" fill="none">
            <path d="M6.5 12L2.5 8L3.56 6.94L6.5 9.88L12.44 3.94L13.5 5L6.5 12Z" fill="currentColor"/>
          </svg>
        </div>
        <span class="reasoning-label">{{ thinkingMessage }}</span>
      </div>
      <el-icon :class="{ 'rotated': isOpen }" class="reasoning-arrow"><ArrowDown /></el-icon>
    </div>
    <Transition name="reasoning-expand">
      <div v-if="isOpen" class="reasoning-body">
        <div class="reasoning-content">
          <MarkdownRenderer :content="content" />
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { ArrowDown } from '@element-plus/icons-vue'
import MarkdownRenderer from '../common/MarkdownRenderer.vue'

const props = defineProps({
  content: {
    type: String,
    required: true
  },
  duration: {
    type: Number,
    default: undefined
  },
  isStreaming: {
    type: Boolean,
    default: false
  },
  defaultOpen: {
    type: Boolean,
    default: true
  },
  className: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['openChange'])

const isOpen = ref(props.defaultOpen)
const hasAutoClosed = ref(false)
const startTime = ref(null)
const duration = ref(props.duration)

const AUTO_CLOSE_DELAY = 1500
const MS_IN_S = 1000

const thinkingMessage = computed(() => {
  if (props.isStreaming || duration.value === 0) {
    return '正在思考'
  }
  if (duration.value === undefined) {
    return '已深度思考'
  }
  return `已深度思考（用时 ${duration.value} 秒）`
})

function toggleOpen() {
  isOpen.value = !isOpen.value
  emit('openChange', isOpen.value)
}

watch(() => props.isStreaming, (isStreaming) => {
  if (isStreaming) {
    if (startTime.value === null) {
      startTime.value = Date.now()
    }
  } else if (startTime.value !== null) {
    duration.value = Math.ceil((Date.now() - startTime.value) / MS_IN_S)
    startTime.value = null
  }
})

watch([() => props.isStreaming, isOpen, () => props.defaultOpen], ([isStreaming, open, defaultOpen]) => {
  if (defaultOpen && !isStreaming && open && !hasAutoClosed.value) {
    const timer = setTimeout(() => {
      isOpen.value = false
      hasAutoClosed.value = true
    }, AUTO_CLOSE_DELAY)
    return () => clearTimeout(timer)
  }
}, { flush: 'post' })

watch(() => props.duration, (newDuration) => {
  duration.value = newDuration
})
</script>

<style scoped>
.ai-reasoning {
  margin-bottom: 12px;
  border-left: 2px solid color-mix(in srgb, var(--sidebar-primary) 30%, transparent);
  border-radius: 0 8px 8px 0;
  background: color-mix(in srgb, var(--sidebar-primary) 4%, transparent);
  overflow: hidden;
}

.ai-reasoning.is-streaming {
  border-left-color: var(--sidebar-primary);
  background: color-mix(in srgb, var(--sidebar-primary) 6%, transparent);
}

.reasoning-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  cursor: pointer;
  user-select: none;
  transition: background-color 0.15s ease;
}

.reasoning-header:hover {
  background: color-mix(in srgb, var(--sidebar-primary) 6%, transparent);
}

.ai-reasoning.is-streaming .reasoning-header:hover {
  background: color-mix(in srgb, var(--sidebar-primary) 8%, transparent);
}

.reasoning-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.reasoning-indicator {
  width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.reasoning-check {
  width: 14px;
  height: 14px;
  color: var(--sidebar-primary);
}

.thinking-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--sidebar-primary);
  animation: thinking-pulse 1.4s ease-in-out infinite;
}

@keyframes thinking-pulse {
  0%, 100% { opacity: 0.4; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.2); }
}

.reasoning-label {
  font-size: 13px;
  color: var(--muted-foreground);
  font-weight: 500;
}

.ai-reasoning.is-streaming .reasoning-label {
  color: var(--sidebar-primary);
}

.reasoning-arrow {
  font-size: 12px;
  color: var(--muted-foreground);
  transition: transform 0.25s ease;
  flex-shrink: 0;
}

.reasoning-arrow.rotated {
  transform: rotate(180deg);
}

.reasoning-body {
  border-top: 1px solid color-mix(in srgb, var(--border) 50%, transparent);
}

.reasoning-content {
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--muted-foreground);
  max-height: 400px;
  overflow-y: auto;
}

.reasoning-content :deep(p) {
  margin: 0 0 6px;
}

.reasoning-content :deep(p:last-child) {
  margin-bottom: 0;
}

.reasoning-content :deep(code) {
  font-size: 12px;
}

.reasoning-expand-enter-active {
  transition: all 0.25s ease;
}

.reasoning-expand-leave-active {
  transition: all 0.2s ease;
}

.reasoning-expand-enter-from,
.reasoning-expand-leave-to {
  opacity: 0;
  max-height: 0;
}

.reasoning-expand-enter-to,
.reasoning-expand-leave-from {
  opacity: 1;
  max-height: 500px;
}
</style>
