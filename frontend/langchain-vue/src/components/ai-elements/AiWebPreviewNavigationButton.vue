<template>
  <el-tooltip v-if="tooltip" :content="tooltip" placement="top">
    <el-button
      :class="className"
      size="small"
      :disabled="disabled"
      link
      @click="$emit('click', $event)"
    >
      <slot></slot>
    </el-button>
  </el-tooltip>
  <el-button v-else :class="className" size="small" :disabled="disabled" link @click="$emit('click', $event)">
    <slot></slot>
  </el-button>
</template>

<script setup>
defineProps({
  className: {
    type: String,
    default: ''
  },
  disabled: {
    type: Boolean,
    default: false
  },
  tooltip: {
    type: String,
    default: ''
  }
})

defineEmits(['click'])
</script>

<style scoped>
</style>
