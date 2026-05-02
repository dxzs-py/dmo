<template>
  <div v-show="index === currentBranchValue" v-bind="$attrs">
    <slot></slot>
  </div>
</template>

<script setup>
import { inject, computed } from 'vue'

defineProps({
  index: {
    type: Number,
    required: true
  }
})

const branchContext = inject('messageBranchContext')

if (!branchContext) {
  throw new Error('MessageBranchContent must be used within MessageBranch')
}

const currentBranchValue = computed(() => {
  const val = branchContext.currentBranch
  return typeof val === 'object' && val !== null && 'value' in val ? val.value : val
})
</script>
