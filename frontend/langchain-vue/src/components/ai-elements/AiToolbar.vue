<template>
  <div class="ai-toolbar" :class="className">
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
.ai-toolbar {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  border-radius: 0.125rem;
  border: 1px solid var(--border);
  background-color: var(--background);
  padding: 0.375rem;
  position: absolute;
  bottom: 100%;
  left: 50%;
  transform: translateX(-50%);
  margin-bottom: 0.5rem;
}
</style>
