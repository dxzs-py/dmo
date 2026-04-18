<template>
  <div class="ai-reasoning" :class="className" v-bind="$attrs">
    <el-collapse v-model="activePanels" @change="handleOpenChange">
      <el-collapse-item name="reasoning">
        <template #title>
          <div class="reasoning-trigger" @click.stop>
            <el-icon class="reasoning-icon"><ChatDotRound /></el-icon>
            <span class="reasoning-text">{{ thinkingMessage }}</span>
            <el-icon :class="{ 'rotated': isOpen }" class="reasoning-arrow"><ArrowDown /></el-icon>
          </div>
        </template>
        <div class="reasoning-content">
          <MarkdownRenderer :content="content" />
        </div>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { ChatDotRound, ArrowDown } from '@element-plus/icons-vue'
import MarkdownRenderer from '../MarkdownRenderer.vue'

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

const activePanels = ref(props.defaultOpen ? ['reasoning'] : [])
const hasAutoClosed = ref(false)
const startTime = ref(null)
const duration = ref(props.duration)

const AUTO_CLOSE_DELAY = 1000
const MS_IN_S = 1000

const isOpen = computed(() => activePanels.value.includes('reasoning'))

const thinkingMessage = computed(() => {
  if (props.isStreaming || duration.value === 0) {
    return '正在思考...'
  }
  if (duration.value === undefined) {
    return '思考了几秒钟'
  }
  return `思考了 ${duration.value} 秒钟`
})

const handleOpenChange = (value) => {
  emit('openChange', value.includes('reasoning'))
}

// 跟踪流式传输时的持续时间
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

// 流式传输开始时自动打开，结束时自动关闭（仅一次）
watch([() => props.isStreaming, isOpen, () => props.defaultOpen], ([isStreaming, open, defaultOpen]) => {
  if (defaultOpen && !isStreaming && open && !hasAutoClosed.value) {
    // 添加小延迟，让用户有时间看到内容
    const timer = setTimeout(() => {
      activePanels.value = []
      hasAutoClosed.value = true
    }, AUTO_CLOSE_DELAY)

    return () => clearTimeout(timer)
  }
}, { flush: 'post' })

// 监听duration属性变化
watch(() => props.duration, (newDuration) => {
  duration.value = newDuration
})
</script>

<style scoped>
.ai-reasoning {
  margin-bottom: 16px;
}

.reasoning-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  transition: color 0.2s;
}

.reasoning-trigger:hover {
  color: var(--el-text-color-primary);
}

.reasoning-icon {
  font-size: 16px;
}

.reasoning-arrow {
  font-size: 12px;
  transition: transform 0.3s;
}

.reasoning-arrow.rotated {
  transform: rotate(180deg);
}

.reasoning-content {
  margin-top: 12px;
  padding: 12px;
  background-color: var(--el-fill-color-light);
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}
</style>