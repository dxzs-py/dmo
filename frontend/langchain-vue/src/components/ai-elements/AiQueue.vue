<template>
  <div class="ai-queue" :class="className">
    <div class="queue-header">
      <span class="queue-title">队列</span>
      <span class="queue-count">{{ items.length }}</span>
    </div>
    <div class="queue-items">
      <slot></slot>
    </div>
  </div>
</template>

<script setup>
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
}

.queue-title {
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
  max-height: 200px;
  overflow-y: auto;
}
</style>
