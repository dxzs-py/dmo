<template>
  <div v-if="totalBranches > 1" class="message-branch-selector" :class="className" v-bind="$attrs">
    <el-button-group>
      <MessageBranchPrevious />
      <MessageBranchPage />
      <MessageBranchNext />
    </el-button-group>
  </div>
</template>

<script setup>
import { inject, computed } from 'vue'
import MessageBranchPrevious from './MessageBranchPrevious.vue'
import MessageBranchNext from './MessageBranchNext.vue'
import MessageBranchPage from './MessageBranchPage.vue'

defineProps({
  from: {
    type: String,
    required: true
  },
  className: {
    type: String,
    default: ''
  }
})

const branchContext = inject('messageBranchContext')

if (!branchContext) {
  throw new Error('MessageBranchSelector must be used within MessageBranch')
}

const totalBranches = computed(() => branchContext.totalBranches())
</script>

<style scoped>
.message-branch-selector {
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 8px;
}
</style>