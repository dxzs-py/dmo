<template>
  <div class="ai-artifact-content" :class="className">
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
.ai-artifact-content {
  flex: 1;
  overflow: auto;
  padding: 16px;
}
</style>
