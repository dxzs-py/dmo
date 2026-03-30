<template>
  <div class="ai-inline-citation" :class="className">
    <el-tag size="small" type="info">
      <slot></slot>
    </el-tag>
  </div>
</template>

<script setup>
defineProps({
  className: {
    type: String,
    default: ''
  },
  number: {
    type: [Number, String],
    default: ''
  }
})
</script>

<style scoped>
.ai-inline-citation {
  display: inline-block;
  margin: 0 2px;
}
</style>
