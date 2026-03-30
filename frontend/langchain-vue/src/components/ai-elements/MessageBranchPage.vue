<template>
  <span class="message-branch-page" :class="className" v-bind="$attrs">
    {{ currentBranch + 1 }} of {{ totalBranches }}
  </span>
</template>

<script setup>
import { inject } from 'vue'

const props = defineProps({
  className: {
    type: String,
    default: ''
  }
})

const branchContext = inject('messageBranchContext')

if (!branchContext) {
  throw new Error('MessageBranchPage must be used within MessageBranch')
}

const { currentBranch, totalBranches } = branchContext
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