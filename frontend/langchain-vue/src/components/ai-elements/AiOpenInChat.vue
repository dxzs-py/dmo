<template>
  <el-button
    :class="className"
    size="small"
    type="primary"
    link
    @click="$emit('click', $event)"
  >
    <el-icon><ChatDotRound /></el-icon>
    <span v-if="!compact">{{ label }}</span>
  </el-button>
</template>

<script setup>
import { ChatDotRound } from '@element-plus/icons-vue'

defineProps({
  className: {
    type: String,
    default: ''
  },
  label: {
    type: String,
    default: '在聊天中打开'
  },
  compact: {
    type: Boolean,
    default: false
  }
})

defineEmits(['click'])
</script>

<style scoped>
</style>
