<template>
  <div class="ai-web-preview-navigation" :class="className">
    <slot></slot>
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
.ai-web-preview-navigation {
  display: flex;
  align-items: center;
  gap: 4px;
  border-bottom: 1px solid var(--el-border-color);
  padding: 8px;
}
</style>
