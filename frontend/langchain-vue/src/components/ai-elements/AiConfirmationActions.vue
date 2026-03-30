<template>
  <div v-if="shouldShow" class="ai-confirmation-actions" :class="className">
    <slot></slot>
  </div>
</template>

<script setup>
import { computed, inject } from 'vue'

defineProps({
  className: {
    type: String,
    default: ''
  }
})

const confirmation = inject('confirmation')

const shouldShow = computed(() => {
  return confirmation?.state?.value === 'approval-requested'
})
</script>

<style scoped>
.ai-confirmation-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 0.5rem;
  align-self: flex-end;
}
</style>
