<template>
  <slot v-if="shouldShow"></slot>
</template>

<script setup>
import { computed, inject } from 'vue'

const confirmation = inject('confirmation')

const shouldShow = computed(() => {
  return confirmation?.state?.value === 'approval-requested'
})
</script>
