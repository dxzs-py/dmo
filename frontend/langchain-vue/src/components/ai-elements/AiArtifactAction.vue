<template>
  <el-tooltip v-if="tooltip" :content="tooltip" placement="top">
    <el-button
      :class="className"
      size="small"
      type="button"
      link
      @click="$emit('click', $event)"
    >
      <component :is="icon" v-if="icon" />
      <slot v-else></slot>
      <span class="sr-only">{{ label || tooltip }}</span>
    </el-button>
  </el-tooltip>
  <el-button v-else :class="className" size="small" type="button" link @click="$emit('click', $event)">
    <component :is="icon" v-if="icon" />
    <slot v-else></slot>
    <span class="sr-only">{{ label || tooltip }}</span>
  </el-button>
</template>

<script setup>
defineProps({
  tooltip: {
    type: String,
    default: ''
  },
  label: {
    type: String,
    default: ''
  },
  icon: {
    type: [Object, Function],
    default: null
  },
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
