<template>
  <div class="ai-artifact-actions" :class="className">
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
.ai-artifact-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
</style>
