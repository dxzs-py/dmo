<template>
  <span class="message-branch-page" :class="className" v-bind="$attrs">
    {{ currentBranchValue + 1 }} of {{ totalBranchesValue }}
  </span>
</template>

<script setup>
import { inject, computed } from 'vue'

defineProps({
  className: {
    type: String,
    default: ''
  }
})

const branchContext = inject('messageBranchContext')

if (!branchContext) {
  throw new Error('MessageBranchPage must be used within MessageBranch')
}

const currentBranchValue = computed(() => {
  const val = branchContext.currentBranch
  return typeof val === 'object' && val !== null && 'value' in val ? val.value : (val ?? 0)
})

const totalBranchesValue = computed(() => {
  const val = branchContext.totalBranches
  return typeof val === 'object' && val !== null && 'value' in val ? val.value : (val ?? 0)
})
</script>

<style scoped>
.message-branch-page {
  padding: 0 12px;
  display: flex;
  align-items: center;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  background-color: var(--el-bg-color);
  border-top: 1px solid var(--el-border-color);
  border-bottom: 1px solid var(--el-border-color);
}
</style>
