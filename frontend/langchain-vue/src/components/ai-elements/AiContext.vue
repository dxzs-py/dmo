<template>
  <div class="ai-context" :class="className">
    <div class="context-label">上下文</div>
    <div class="context-content">
      <slot></slot>
    </div>
  </div>
</template>

<script setup>
defineProps({
  className: {
    type: String,
    default: ''
  }
})
</script>

<style scoped>
.ai-context {
  padding: 12px;
  border-radius: 8px;
  background-color: var(--el-fill-color-lighter);
  border-left: 3px solid var(--el-color-primary);
}

.context-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-color-primary);
  margin-bottom: 8px;
  text-transform: uppercase;
}

.context-content {
  font-size: 14px;
  color: var(--el-text-color-primary);
}
</style>
