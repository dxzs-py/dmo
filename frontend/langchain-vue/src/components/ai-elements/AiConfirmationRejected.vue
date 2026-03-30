<template>
  <slot v-if="shouldShow"></slot>
</template>

<script setup>
import { computed, inject } from 'vue'

const confirmation = inject('confirmation')

const shouldShow = computed(() => {
  const approval = confirmation?.approval?.value
  const state = confirmation?.state?.value
  return approval?.approved === false && 
         (state === 'approval-responded' || 
          state === 'output-denied' || 
          state === 'output-available')
})
</script>
