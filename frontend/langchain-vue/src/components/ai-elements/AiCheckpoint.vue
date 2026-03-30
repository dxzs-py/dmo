<template>
  <div class="ai-checkpoint" :class="className">
    <div class="checkpoint-icon">
      <el-icon><CircleCheck /></el-icon>
    </div>
    <div class="checkpoint-content">
      <slot></slot>
    </div>
  </div>
</template>

<script setup>
import { CircleCheck } from '@element-plus/icons-vue'

defineProps({
  className: {
    type: String,
    default: ''
  },
  status: {
    type: String,
    default: 'completed'
  }
})
</script>

<style scoped>
.ai-checkpoint {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px;
  border-radius: 8px;
  background-color: var(--el-fill-color-lighter);
}

.checkpoint-icon {
  color: var(--el-color-success);
  flex-shrink: 0;
  margin-top: 2px;
}

.checkpoint-content {
  flex: 1;
}
</style>
