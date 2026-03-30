<template>
  <p class="ai-artifact-title" :class="className">
    <slot></slot>
  </p>
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
.ai-artifact-title {
  font-weight: 500;
  color: var(--el-text-color-primary);
  font-size: 14px;
}
</style>
