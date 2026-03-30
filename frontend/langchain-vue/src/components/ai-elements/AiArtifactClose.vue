<template>
  <el-button
    :class="className"
    size="small"
    type="button"
    link
    @click="$emit('click', $event)"
  >
    <slot>
      <el-icon><Close /></el-icon>
    </slot>
    <span class="sr-only">Close</span>
  </el-button>
</template>

<script setup>
import { Close } from '@element-plus/icons-vue'

defineProps({
  className: {
    type: String,
    default: ''
  }
})

defineEmits(['click'])
</script>

<style scoped>
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>
