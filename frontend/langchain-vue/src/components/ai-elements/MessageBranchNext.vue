<template>
  <el-button 
    size="small" 
    :disabled="totalBranches <= 1" 
    @click="goToNext"
    :class="className"
    v-bind="$attrs"
  >
    <el-icon><ArrowRight /></el-icon>
  </el-button>
</template>

<script setup>
import { inject } from 'vue'
import { ArrowRight } from '@element-plus/icons-vue'

defineProps({
  className: {
    type: String,
    default: ''
  }
})

const branchContext = inject('messageBranchContext')

if (!branchContext) {
  throw new Error('MessageBranchNext must be used within MessageBranch')
}

const { goToNext, totalBranches } = branchContext
</script>

<style scoped>
/* 可根据需要添加样式 */
</style>