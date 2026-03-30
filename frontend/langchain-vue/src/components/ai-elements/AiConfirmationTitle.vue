<template>
  <div class="ai-confirmation-title">
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
.ai-confirmation-title {
  display: inline;
  color: var(--foreground);
}
</style>
