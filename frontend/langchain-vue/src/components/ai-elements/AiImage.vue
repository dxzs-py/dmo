<template>
  <img
    :class="className"
    :alt="alt"
    :src="imageSrc"
  />
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  base64: {
    type: String,
    default: ''
  },
  uint8Array: {
    type: Uint8Array,
    default: null
  },
  mediaType: {
    type: String,
    default: 'image/png'
  },
  className: {
    type: String,
    default: ''
  },
  alt: {
    type: String,
    default: ''
  }
})

const imageSrc = computed(() => {
  if (props.base64) {
    return `data:${props.mediaType};base64,${props.base64}`
  }
  if (props.uint8Array) {
    const base64 = btoa(String.fromCharCode.apply(null, props.uint8Array))
    return `data:${props.mediaType};base64,${base64}`
  }
  return ''
})
</script>

<style scoped>
img {
  height: auto;
  max-width: 100%;
  overflow: hidden;
  border-radius: 6px;
}
</style>
