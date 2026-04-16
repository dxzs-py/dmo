<template>
  <el-button 
    size="small" 
    :disabled="totalBranches <= 1" 
    :class="className"
    v-bind="$attrs"
    @click="goToPrevious"
  >
    <el-icon><ArrowLeft /></el-icon>
  </el-button>
</template>

<script setup>
import { inject } from 'vue'
import { ArrowLeft } from '@element-plus/icons-vue'

defineProps({
  className: {
    type: String,
    default: ''
  }
})

const branchContext = inject('messageBranchContext')

if (!branchContext) {
  throw new Error('MessageBranchPrevious must be used within MessageBranch')
}

const { goToPrevious, totalBranches } = branchContext
</script>

<style scoped>
/* 可根据需要添加样式 */
</style>