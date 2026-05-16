<template>
  <div class="ai-queue" :class="className">
    <div class="queue-header" @click="isExpanded = !isExpanded">
      <span class="queue-title">
        <el-icon :size="14" class="queue-expand-icon"><ArrowDown v-if="isExpanded" /><ArrowRight v-else /></el-icon>
        队列
      </span>
      <span class="queue-count">{{ items.length }}</span>
    </div>
    <div v-if="isExpanded" class="queue-items">
      <slot></slot>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ArrowDown, ArrowRight } from '@element-plus/icons-vue'

defineProps({
  className: {
    type: String,
    default: ''
  },
  items: {
    type: Array,
    default: () => []
  }
})

const isExpanded = ref(true)
</script>

<style scoped>
.ai-queue {
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  background-color: var(--el-bg-color);
  overflow: hidden;
}

.queue-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid var(--el-border-color);
  background-color: var(--el-fill-color-lighter);
  cursor: pointer;
  user-select: none;
  transition: background-color 0.2s;
}

.queue-header:hover {
  background-color: var(--el-fill-color-light);
}

.queue-expand-icon {
  color: var(--el-text-color-secondary);
  transition: transform 0.2s;
  margin-right: 6px;
}

.queue-title {
  display: flex;
  align-items: center;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.queue-count {
  background-color: var(--el-color-primary);
  color: white;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
}

.queue-items {
  max-height: 300px;
  overflow-y: auto;
}
</style>
