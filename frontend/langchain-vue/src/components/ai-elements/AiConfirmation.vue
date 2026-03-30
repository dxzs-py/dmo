<template>
  <div v-if="shouldShow" class="ai-confirmation" :class="className">
    <slot></slot>
  </div>
</template>

<script setup>
import { computed, provide } from 'vue'

const props = defineProps({
  approval: {
    type: Object,
    default: null
  },
  state: {
    type: String,
    required: true
  },
  className: {
    type: String,
    default: ''
  }
})

const shouldShow = computed(() => {
  return props.approval && 
         props.state !== 'input-streaming' && 
         props.state !== 'input-available'
})

provide('confirmation', {
  approval: computed(() => props.approval),
  state: computed(() => props.state)
})
</script>

<style scoped>
.ai-confirmation {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding: 1rem;
  border-radius: 0.5rem;
  border: 1px solid var(--border);
  background-color: var(--card);
}
</style>
