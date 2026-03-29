<script setup>
import { ref } from 'vue'
import { ArrowDown, MagicStick } from '@element-plus/icons-vue'

defineProps({
  reasoning: {
    type: Object,
    default: () => ({})
  },
  isStreaming: {
    type: Boolean,
    default: false
  }
})

const isExpanded = ref(true)

const toggleExpand = () => {
  isExpanded.value = !isExpanded.value
}
</script>

<template>
  <div class="ai-reasoning" :class="{ 'is-streaming': isStreaming }">
    <div class="ai-reasoning__header" @click="toggleExpand">
      <div class="ai-reasoning__title">
        <el-icon class="brain-icon"><MagicStick /></el-icon>
        <span>推理过程</span>
        <span v-if="reasoning.duration" class="duration">
          {{ reasoning.duration }}s
        </span>
      </div>
      <el-icon class="expand-icon" :class="{ 'is-expanded': isExpanded }">
        <ArrowDown />
      </el-icon>
    </div>

    <div v-if="isExpanded && reasoning.content" class="ai-reasoning__content">
      <div class="reasoning-text">
        {{ reasoning.content }}
      </div>
      <div v-if="isStreaming" class="streaming-indicator">
        <span class="streaming-dot"></span>
        <span class="streaming-dot"></span>
        <span class="streaming-dot"></span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ai-reasoning {
  background-color: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  margin: 12px 0;
  overflow: hidden;
}

.ai-reasoning.is-streaming {
  border-color: var(--el-color-primary-light-5);
}

.ai-reasoning__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  cursor: pointer;
  transition: background-color 0.2s;
}

.ai-reasoning__header:hover {
  background-color: var(--el-fill-color);
}

.ai-reasoning__title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 14px;
  color: var(--el-text-color-primary);
}

.brain-icon {
  color: var(--el-color-primary);
}

.duration {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-weight: normal;
}

.expand-icon {
  transition: transform 0.2s;
  color: var(--el-text-color-secondary);
}

.expand-icon.is-expanded {
  transform: rotate(180deg);
}

.ai-reasoning__content {
  padding: 12px 16px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.reasoning-text {
  font-size: 14px;
  line-height: 1.8;
  color: var(--el-text-color-regular);
  white-space: pre-wrap;
}

.streaming-indicator {
  display: flex;
  gap: 4px;
  margin-top: 12px;
}

.streaming-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: var(--el-color-primary);
  animation: bounce 1.4s infinite ease-in-out both;
}

.streaming-dot:nth-child(1) { animation-delay: -0.32s; }
.streaming-dot:nth-child(2) { animation-delay: -0.16s; }

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}
</style>
