<template>
  <div class="ai-panel" :class="className">
    <slot></slot>
  </div>
</template>

<script setup>
defineProps({
  className: {
    type: String,
    default: ''
  },
  position: {
    type: String,
    default: 'bottom-left'
  }
})
</script>

<style scoped>
.ai-panel {
  margin: 16px;
  overflow: hidden;
  border-radius: 6px;
  border: 1px solid var(--el-border-color);
  background-color: var(--el-bg-color);
  padding: 4px;
}
</style>
