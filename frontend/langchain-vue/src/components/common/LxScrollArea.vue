<script setup>
import { ref } from 'vue'

defineProps({
  height: {
    type: String,
    default: '100%'
  },
  maxHeight: {
    type: String,
    default: 'none'
  },
  native: {
    type: Boolean,
    default: false
  },
  wrapClass: {
    type: [String, Object, Array],
    default: ''
  },
  viewClass: {
    type: [String, Object, Array],
    default: ''
  }
})

const emit = defineEmits(['scroll'])

const wrapRef = ref(null)

const handleScroll = (event) => {
  emit('scroll', event)
}
</script>

<template>
  <div v-if="native" class="lx-scroll-area">
    <div
      ref="wrapRef"
      class="lx-scroll-area__wrap"
      :class="wrapClass"
      @scroll="handleScroll"
    >
      <div class="lx-scroll-area__view" :class="viewClass">
        <slot />
      </div>
    </div>
  </div>
  <div v-else class="lx-scroll-area">
    <div
      ref="wrapRef"
      class="lx-scroll-area__wrap"
      :class="wrapClass"
      :style="{
        height: height,
        maxHeight: maxHeight !== 'none' ? maxHeight : undefined
      }"
      @scroll="handleScroll"
    >
      <div class="lx-scroll-area__view" :class="viewClass">
        <slot />
      </div>
    </div>
  </div>
</template>

<style scoped>
.lx-scroll-area {
  position: relative;
  overflow: hidden;
}

.lx-scroll-area__wrap {
  overflow: auto;
  height: 100%;
  scrollbar-width: thin;
  scrollbar-color: var(--el-scrollbar-bar-color) transparent;
}

.lx-scroll-area__wrap::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}

.lx-scroll-area__wrap::-webkit-scrollbar-thumb {
  background-color: var(--el-scrollbar-bar-color, #dcdfe6);
  border-radius: 3px;
}

.lx-scroll-area__wrap::-webkit-scrollbar-track {
  background-color: transparent;
}

.lx-scroll-area__wrap::-webkit-scrollbar-corner {
  background-color: transparent;
}

.lx-scroll-area__view {
  display: inline-block;
  min-width: 100%;
}
</style>
