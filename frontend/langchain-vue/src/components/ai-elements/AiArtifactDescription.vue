<template>
  <p class="ai-artifact-description" :class="className">
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
.ai-artifact-description {
  color: var(--el-text-color-secondary);
  font-size: 14px;
}
</style>
