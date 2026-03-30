<template>
  <button 
    class="ai-confirmation-action" 
    :class="className"
    type="button"
    @click="$emit('click', $event)"
  >
    <slot></slot>
  </button>
</template>

<script setup>
defineProps({
  className: {
    type: String,
    default: ''
  }
})

defineEmits(['click'])
</script>

<style scoped>
.ai-confirmation-action {
  height: 2rem;
  padding: 0 0.75rem;
  font-size: 0.875rem;
  border-radius: 0.375rem;
  border: none;
  background-color: var(--primary);
  color: var(--primary-foreground);
  cursor: pointer;
  transition: opacity 0.2s;
}

.ai-confirmation-action:hover {
  opacity: 0.9;
}
</style>
